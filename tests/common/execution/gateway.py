"""Host-only Responses gateway: fixed upstream/model, durable fee reservations.

The experiment scheduler owns trials; this layer owns request accounting.
Explicit rates and a provider input/output billing bound are prerequisites.
"""
from __future__ import annotations
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .budget import BudgetDenied, Ledger, load_pricing, request_bound
from .accounting import estimate
from .retry import POLICY as RETRY_POLICY, delay as retry_delay
from .responses import ResponseStore, ResponseProtocolError, terminal_response, terminal_analysis, delivery_payload, encoded, sha


class RecordedFailure(Exception):
    """Failure already journaled and accounted; never duplicate its charge."""
    def __init__(self, diagnostic):
        self.diagnostic = diagnostic


class ProviderResponseFailure(Exception):
    def __init__(self, code, request_id):
        self.code = code
        self.request_id = request_id


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise BudgetDenied('Provider redirects are forbidden')


class EventJournal(list):
    def __init__(self,path):
        super().__init__();self.path=Path(path);self.lock=threading.Lock()

    def append(self,event):
        with self.lock:
            super().append(dict(event,time=time.time()))
            temp=self.path.with_suffix('.tmp')
            temp.write_text(json.dumps(self,indent=2)+'\n');temp.chmod(0o600)
            os.replace(temp,self.path)


class Gateway:
    def __init__(self, *, provider_url, model, api_key, pricing_path, ledger_path,
                 episode, token, episode_limit_cny=10, max_requests=1,
                 max_output_tokens=1024, deadline_seconds=300, request_timeout_seconds=90,
                 admission_stop_file=None, reasoning_effort=None, ledger=None, checkpoint_store=None,
                 replay_requests=None, replay_cache_key_policy=None, repair_settlement=False,
                 replay_resume_prompt=None, continue_after_replay=False):
        if continue_after_replay and not replay_requests:
            raise ValueError('Continuation requires an explicit saved response barrier')
        if replay_cache_key_policy is not None or replay_resume_prompt is not None:
            raise ValueError('Only exact normalized-request replay is supported')
        url=urlsplit(provider_url)
        if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('Fixed HTTPS provider URL required')
        self.pricing=load_pricing(pricing_path,model,provider_url)
        if (not isinstance(self.pricing.get('max_input_tokens_per_request'),int)
                or self.pricing['max_input_tokens_per_request']<1):
            raise BudgetDenied('Verified maximum billable input tokens required, including protocol overhead')
        self.ledger=ledger if ledger is not None else Ledger(ledger_path)
        self.prior_request_ids={r['id'] for r in self.ledger.summary()['requests'] if r['episode']==episode}
        self.checkpoint_store=checkpoint_store
        self.repair_settlement=repair_settlement
        self.provider_url=provider_url.rstrip('/')+'/responses';self.model=model
        self.api_key=api_key;self.token=token;self.episode=episode
        self.episode_limit=episode_limit_cny;self.max_requests=max_requests
        self.output_limit=max_output_tokens;self.deadline=time.monotonic()+deadline_seconds
        if not 0<request_timeout_seconds<=deadline_seconds:raise ValueError('Invalid upstream timeout')
        self.request_timeout_seconds=request_timeout_seconds
        if reasoning_effort is not None and reasoning_effort not in ('none','minimal','low','medium','high','xhigh'):
            raise ValueError('Invalid reasoning effort')
        self.reasoning_effort=reasoning_effort
        self.closing=False
        self.admission_stop_file=Path(admission_stop_file) if admission_stop_file else None
        self.lock=threading.Lock();self.opener=urllib.request.build_opener(NoRedirect(),urllib.request.ProxyHandler({}))
        self.events=EventJournal(Path(ledger_path).parent/(episode+'-events-'+str(time.time_ns())+'.json'))
        if not episode or Path(episode).name != episode or episode in ('.','..'):
            raise ValueError('Episode must be a single path component')
        self.responses=ResponseStore(self.ledger, Path(ledger_path).parent/'response-receipts'/episode)
        # Explicit host recovery mode is replay-only. It cannot fall through
        # to fresh generation when a receipt is missing, mismatched or spent.
        self.replay_requests=None if replay_requests is None else list(replay_requests)
        self.replay_cache_key_policy=replay_cache_key_policy
        self.replay_resume_prompt=replay_resume_prompt
        self.continue_after_replay=continue_after_replay
        self.replayed_hashes=set()
        self.replay_delivery_complete=threading.Event()
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):self.send_error(403)
            def do_CONNECT(self):self.send_error(403)
            def do_POST(self):owner.handle(self)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.daemon_threads=True
        self.port=self.server.server_address[1]
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    def handle(self, handler):
        import hmac
        if (handler.path!='/v1/responses'
                or handler.headers.get('Host')!=f'127.0.0.1:{self.port}'
                or not hmac.compare_digest(handler.headers.get('Authorization',''),'Bearer '+self.token)
                or handler.headers.get('Content-Encoding') not in (None,'identity')
                or handler.headers.get('Transfer-Encoding')):
            handler.send_error(403);return
        reservation=None
        logical_request_id=uuid.uuid4().hex
        phase='request_validation';response_bytes=0
        try:
            length=int(handler.headers.get('Content-Length','0'))
            if not 0<length<=32*1024*1024:raise BudgetDenied('Request size limit')
            handler.connection.settimeout(10)
            body=json.loads(handler.rfile.read(length))
            if not isinstance(body,dict) or body.get('model')!=self.model:
                raise BudgetDenied('Model mismatch')
            # No remote tools, stored conversations, server background jobs or
            # referenced media. All context is counted in this one request.
            allowed={'model','instructions','input','tools','tool_choice','parallel_tool_calls',
                     'stream','store','reasoning','text','include','max_output_tokens',
                     'temperature','top_p','truncation','metadata','prompt_cache_key','client_metadata'}
            if set(body)-allowed:raise BudgetDenied('Unsupported request fields: '+','.join(sorted(set(body)-allowed)))
            # Client installation metadata is not part of model input.
            # Do not send this client-specific field to compatible providers.
            body.pop('client_metadata',None)
            def check_tools(items,namespace=False):
                if not isinstance(items,list):raise BudgetDenied('Invalid tools list')
                for tool in items:
                    if not isinstance(tool,dict):raise BudgetDenied('Invalid tool')
                    kind=tool.get('type')
                    if kind=='namespace' and not namespace:
                        check_tools(tool.get('tools'),namespace=True)
                    elif kind not in ('function','custom'):
                        raise BudgetDenied('Only local function/custom tools, optionally in a namespace, are allowed')
            check_tools(body.get('tools',[]))
            def inspect(value):
                if isinstance(value,dict):
                    if value.get('type') == 'input_image':
                        import base64
                        media = value.get('image_url', '')
                        if not isinstance(media,str) or not media.startswith(('data:image/png;base64,','data:image/jpeg;base64,')):
                            raise BudgetDenied('Only inline PNG/JPEG page images are allowed')
                        try:
                            data=base64.b64decode(media.split(',',1)[1],validate=True)
                        except ValueError:
                            raise BudgetDenied('Invalid inline image')
                        if len(data)>8*1024*1024:
                            raise BudgetDenied('Inline image size limit')
                    if value.get('type') in ('input_file','input_audio','item_reference'):
                        raise BudgetDenied('Remote media and server-side item references forbidden')
                    for item in value.values():inspect(item)
                elif isinstance(value,list):
                    for item in value:inspect(item)
            inspect(body)
            if not isinstance(body.get('metadata',{}),dict):
                raise BudgetDenied('Invalid request metadata')
            if body.get('reasoning') is not None and not isinstance(body['reasoning'],dict):
                raise BudgetDenied('Invalid reasoning configuration')
            requested=body.get('max_output_tokens',self.output_limit)
            if type(requested) is not int or requested<1:raise BudgetDenied('Invalid output limit')
            body['max_output_tokens']=min(requested,self.output_limit);body['store']=False
            if self.reasoning_effort is not None:
                body['reasoning'] = dict(body.get('reasoning') or {}, effort=self.reasoning_effort)
            body['stream']=True
            request_sha256 = sha(encoded(body))
            bound=request_bound(self.pricing,self.pricing['max_input_tokens_per_request'],body['max_output_tokens'])
            # Serialize upstream requests. Every request, including retries and
            # tool-command attempts to access the gateway, spends this ledger.
            with self.lock:
                last_failure = None
                replaying=self.replay_requests is not None and bool(self.replay_requests)
                if self.replay_requests is not None:
                    phase='response_replay'
                if self.closing:
                    raise BudgetDenied('Host closed request admission')
                if time.monotonic()>=self.deadline:
                    raise BudgetDenied('Episode deadline reached')
                if self.admission_stop_file is not None and self.admission_stop_file.exists():
                    raise BudgetDenied('Host stopped request admission')
                if self.replay_requests == []:
                    if not self.continue_after_replay:
                        raise BudgetDenied('Replay-only gateway has no remaining authorized receipt')
                    # A second request cannot bypass a failed/ambiguous replay
                    # delivery. The writer sets this only after durable delivery.
                    if not self.replay_delivery_complete.wait(timeout=min(1,max(0,self.deadline-time.monotonic()))):
                        raise BudgetDenied('Saved response delivery has not completed')
                    if request_sha256 in self.replayed_hashes:
                        raise BudgetDenied('Saved response cannot be regenerated upstream')
                if replaying:
                    phase='response_replay'
                    settled_request_id=self.replay_requests[0]
                    context=self.responses.context(settled_request_id)
                    if (context['episode'] != self.episode or
                            context['model'] != self.model or context['provider_url'] != self.provider_url):
                        raise BudgetDenied('Replay request does not match its original context')
                    replay_match=self.responses.match_replay(settled_request_id,body,
                        cache_key_policy=self.replay_cache_key_policy, resume_prompt=self.replay_resume_prompt)
                    self.replayed_hashes.update((request_sha256,replay_match['request_sha256']))
                    request_sha256=replay_match['request_sha256']
                    settlement=self.responses.settle(settled_request_id)
                    _, receipt, final, receipt_sha=self.responses.load(settled_request_id)
                    logical_request_id=context['logical_request_id']
                    self.events.append({'event':'response_replayed','id':settled_request_id,
                        'logical_request_id':logical_request_id,'request_sha256':request_sha256,
                        'receipt_sha256':receipt_sha,'settlement':settlement,'new_upstream_requests':0,
                        'request_match':replay_match})
                for attempt in range(0 if replaying else 3):
                    reservation=None
                    phase='request_admission';response_bytes=0
                    try:
                        if self.closing:
                            raise BudgetDenied('Host closed request admission')
                        if self.admission_stop_file is not None and self.admission_stop_file.exists():
                            raise BudgetDenied('Host stopped request admission')
                        if time.monotonic()>=self.deadline:raise BudgetDenied('Episode deadline reached')
                        if self.checkpoint_store is not None:
                            extra={'logical_request_id':logical_request_id} if getattr(self.checkpoint_store,'code_budget',None) is not None else {}
                            reservation=self.checkpoint_store.reserve(self.episode,bound,self.episode_limit,self.max_requests,body,**extra)
                        else:reservation=self.ledger.reserve(self.episode,bound,self.episode_limit,self.max_requests)
                        previous = next((e['id'] for e in reversed(self.events)
                                         if e['event'] == 'reserved' and e.get('request_sha256') == request_sha256), None)
                        self.events.append({'event':'reserved','id':reservation,'logical_request_id':logical_request_id,
                            'attempt':attempt,'retry_policy':RETRY_POLICY,'model':self.model,'upper_micro_cny':bound,
                            'reasoning':body.get('reasoning'), 'max_output_tokens':body['max_output_tokens'],
                            'request_sha256': request_sha256, 'same_request_previous_id': previous,
                            'method_stage':body.get('metadata',{}).get('method_stage'),
                            'method_step':body.get('metadata',{}).get('method_step')})
                        phase='request_context'
                        self.responses.prepare(reservation, episode=self.episode, logical_request_id=logical_request_id,
                                               body=body, pricing=self.pricing, provider_url=self.provider_url)
                        request=urllib.request.Request(self.provider_url,data=json.dumps(body).encode(),
                            headers={'Authorization':'Bearer '+self.api_key,'Content-Type':'application/json','Accept':'text/event-stream'})
                        if self.checkpoint_store is not None:
                            self.checkpoint_store.begin(reservation, body, retry={
                                'logical_request_id':logical_request_id, 'attempt':attempt,
                                'previous_request_id':last_failure.get('id') if last_failure else None,
                                'prior_failure':last_failure})
                        if time.monotonic() >= self.deadline:
                            raise BudgetDenied('Episode deadline reached before upstream dispatch')
                        chunks=[];size=0;pending=b'';terminal=False
                        phase='upstream_headers'
                        request_deadline=min(self.deadline,time.monotonic()+self.request_timeout_seconds)
                        with self.opener.open(request,timeout=max(0.001,request_deadline-time.monotonic())) as upstream:
                            phase='upstream_body'
                            self.events.append({'event':'upstream_headers','id':reservation})
                            reader=getattr(upstream,'read1',upstream.read)
                            while True:
                                remaining=request_deadline-time.monotonic()
                                if remaining<=0:
                                    if time.monotonic() >= self.deadline:
                                        raise BudgetDenied('Episode deadline reached')
                                    raise TimeoutError('Upstream absolute request deadline reached')
                                # urllib's timeout is per blocking socket operation. Tighten
                                # it to the remaining request duration for each SSE read.
                                sock=getattr(getattr(getattr(upstream,'fp',None),'raw',None),'_sock',None)
                                if sock is not None:sock.settimeout(remaining)
                                # read1 does not wait for 64 KiB or EOF before returning
                                # a small SSE frame. Terminal events, not TCP closure,
                                # delimit a completed Responses request.
                                part=reader(65536)
                                if not part:break
                                size+=len(part)
                                response_bytes=size
                                if size>16*1024*1024:raise BudgetDenied('Response size limit')
                                chunks.append(part)
                                pending+=part
                                lines=pending.split(b'\n');pending=lines.pop()
                                for line in lines:
                                    if not line.startswith(b'data: '):continue
                                    try:frame=json.loads(line[6:])
                                    except (json.JSONDecodeError,UnicodeDecodeError):continue
                                    if isinstance(frame,dict) and frame.get('type') in ('response.completed','response.incomplete','response.failed','error'):
                                        terminal=True
                                if terminal:break
                        if not terminal:
                            raise ConnectionError('Upstream ended before terminal response')
                        # Never forward or archive an upstream error/message that echoes
                        # the host credential. Metering still uses the provider's usage.
                        payload=b''.join(chunks).replace(self.api_key.encode(), b'[REDACTED_HOST_CREDENTIAL]').replace(self.token.encode(), b'[REDACTED_GATEWAY_TOKEN]')
                        phase='response_receipt'
                        self._save_receipt(reservation, logical_request_id, request_sha256, payload)
                        final_response, normalization=terminal_analysis(payload)
                        if normalization['ignored_scanner_trailers'] or normalization['duplicate_terminals']:
                            self.events.append({'event':'response_normalized','id':reservation,
                                                'payload_sha256':sha(payload), **normalization})
                        usage=final_response.get('usage')
                        response_model=final_response.get('model')
                        response_status=final_response.get('status')
                        phase='usage_settlement'
                        charged=None
                        accounting=estimate(usage,self.pricing)
                        if usage and type(usage.get('input_tokens')) is int and type(usage.get('output_tokens')) is int:
                            if (usage['input_tokens']<0 or usage['output_tokens']<0
                                    or usage['input_tokens']>self.pricing['max_input_tokens_per_request']
                                    or usage['output_tokens']>body['max_output_tokens']):
                                self.ledger.finish(reservation,bound+1,usage,'bound_violation')
                            charged=accounting['estimate_upper_micro_cny']
                        if self.checkpoint_store is not None:
                            self.checkpoint_store.result(reservation, final_response, raw=payload)
                        self._settle_response(reservation, charged, usage)
                        self.events.append({'event':'response','id':reservation,'response_model':response_model,
                            'terminal_error_code': final_response['error'].get('code') if isinstance(final_response,dict) and isinstance(final_response.get('error'),dict) else None,
                            'response_id': final_response.get('id') if isinstance(final_response,dict) else None,
                            'response_status':response_status,'accounting':accounting,
                            'reserved_micro_cny':bound,'requested_model':self.model,
                            'stage':body.get('metadata',{}).get('method_stage','generation'),
                            'response_bytes':len(payload)})
                        settled_request_id=reservation
                        reservation=None
                        if response_status == 'failed':
                            error = final_response.get('error') or {}
                            code = error.get('code') if isinstance(error, dict) else None
                            if code in ('server_error', 'overloaded', 'rate_limit_exceeded'):
                                phase='upstream_terminal'
                                raise ProviderResponseFailure(code, settled_request_id)
                        break
                    except Exception as exc:
                        if phase in ('upstream_body', 'response_receipt') and response_bytes and reservation:
                            raw=b''.join(chunks).replace(self.api_key.encode(), b'[REDACTED_HOST_CREDENTIAL]').replace(self.token.encode(), b'[REDACTED_GATEWAY_TOKEN]')
                            try:
                                proof=self.responses.save_raw(reservation,raw)
                                self.events.append({'event':'raw_response_retained','id':reservation, **proof})
                            except (OSError, ValueError, BudgetDenied) as storage_error:
                                self.events.append({'event':'raw_response_storage_failed','id':reservation,
                                                    'error_type':type(storage_error).__name__})
                        if isinstance(exc, BudgetDenied) and phase == 'request_admission' and last_failure:
                            self.events.append({'event':'retry_stopped','logical_request_id':logical_request_id,
                                                'reason':str(exc),'prior_failure':last_failure})
                            raise RecordedFailure(last_failure) from exc
                        diagnostic = self._record_error(exc, reservation, phase, response_bytes)
                        last_failure = diagnostic
                        reservation = None
                        wait = retry_delay(diagnostic, attempt)
                        if wait is None or time.monotonic()+wait >= self.deadline or self.closing:
                            raise RecordedFailure(diagnostic) from exc
                        self.events.append({'event':'retry_scheduled','logical_request_id':logical_request_id,
                                            'failed_request_id':diagnostic['id'], 'next_attempt':attempt+1,
                                            'delay_seconds':wait,'policy':RETRY_POLICY})
                        until = time.monotonic()+wait
                        while time.monotonic() < until:
                            if self.closing or (self.admission_stop_file and self.admission_stop_file.exists()):
                                raise RecordedFailure(diagnostic) from exc
                            time.sleep(max(0, min(0.1, until-time.monotonic())))
                phase='delivery_claim'
                payload=self.responses.claim_delivery(settled_request_id,episode=self.episode,
                    request_sha256=request_sha256,allow_failed=not replaying)
                payload=delivery_payload(payload)
                if replaying:
                    self.replay_requests.pop(0)
            phase='client_response'
            self.events.append({'event':'delivery_started','id':settled_request_id,
                                'logical_request_id':logical_request_id})
            handler.send_response(200);handler.send_header('Content-Type','text/event-stream')
            handler.send_header('Content-Length',str(len(payload)));handler.end_headers();handler.wfile.write(payload)
            if hasattr(handler.wfile,'flush'):
                handler.wfile.flush()
            self.responses.delivered(settled_request_id)
            self.events.append({'event':'delivery_completed','id':settled_request_id,
                                'logical_request_id':logical_request_id})
            if replaying and not self.replay_requests:
                self.replay_delivery_complete.set()
        except Exception as exc:
            diagnostic = (exc.diagnostic if isinstance(exc, RecordedFailure) else
                          self._record_error(exc, settled_request_id if phase in ('client_response','delivery_claim') else reservation, phase, response_bytes))
            if phase == 'client_response':
                # Headers/body may already have reached the client. Do not send
                # a second response or repeat a paid generation.
                return
            try:
                handler.send_error(429 if diagnostic.get('error_type') == 'BudgetDenied' else 502,
                                   'Host gateway rejected or could not complete request')
            except OSError:
                pass

    def _settle_response(self, reservation, charged, usage):
        """Repair one local transaction while the original method is waiting.

        The receipt is already durable and no client delivery has started.
        Recover from that receipt, never issue another provider request. This
        does not resume a dead method or resolve ambiguous client consumption.
        """
        try:
            self.ledger.finish(reservation, charged, usage,
                               'usage_recorded' if charged is not None else 'usage_unknown')
        except (OSError, sqlite3.OperationalError) as error:
            if not self.repair_settlement:
                raise
            if self.closing or time.monotonic() >= self.deadline:
                self.events.append({'event':'local_settlement_recovery_skipped','id':reservation,
                    'policy':'saved-receipt-local-settlement/v1',
                    'reason':'controller_closing' if self.closing else 'episode_deadline_reached',
                    'original_error_type':type(error).__name__,
                    'new_upstream_requests':0, 'new_reservations':0})
                raise
            # A new transaction also handles commit-success/acknowledgement-loss.
            # Identity, usage, reservation bounds and prior settlement are all
            # checked by the durable receipt store. Any second failure escapes.
            recovered=self.responses.settle(reservation)
            self.events.append({'event':'local_settlement_recovered','id':reservation,
                'policy':'saved-receipt-local-settlement/v1',
                'original_error_type':type(error).__name__, 'settlement':recovered,
                'new_upstream_requests':0, 'new_reservations':0})

    def _save_receipt(self, reservation, logical_request_id, request_sha256, payload):
        # Host-only, sanitized wire receipt precedes accounting/delivery. A local
        # failure after this point must not trigger another upstream request.
        record=self.responses.save(reservation,payload)
        path=self.responses.path(reservation)
        self.events.append({'event':'response_receipt','id':reservation,
                            'payload_sha256':record['payload_sha256'],
                            'file':str(path.relative_to(self.events.path.parent))})

    def _record_error(self, exc, reservation, phase, response_bytes):
        diagnostic={'event':'error','id':reservation,'error_type':type(exc).__name__,
                    'phase':phase,'response_bytes_received':response_bytes}
        if hasattr(exc, 'checkpoint_diagnostic'):
            diagnostic['checkpoint'] = exc.checkpoint_diagnostic
        if isinstance(exc, ProviderResponseFailure):
            diagnostic.update(id=exc.request_id, provider_error_code=exc.code)
        if isinstance(exc,(BudgetDenied,ResponseProtocolError)):diagnostic['reason']=str(exc)
        if (isinstance(exc,TimeoutError) and phase in ('upstream_headers','upstream_body')
                and time.monotonic() >= self.deadline):
            # A socket read also uses the remaining episode duration. When
            # that bound expires, retain the observed exception but attribute
            # termination to the actual budget, not an inferred network fault.
            diagnostic.update(reason='Episode deadline reached',deadline_scope='episode')
        if isinstance(exc,urllib.error.HTTPError):
            diagnostic['http_status']=exc.code
            diagnostic['retry_after']=exc.headers.get('Retry-After') if exc.headers else None
            try:
                error_body=exc.read(8192).decode('utf-8',errors='replace')
                try:
                    error_data=json.loads(error_body).get('error',{})
                    if isinstance(error_data,dict):diagnostic['provider_error_code']=error_data.get('code') or error_data.get('type')
                except (ValueError,AttributeError):pass
                diagnostic['provider_error']=error_body.replace(self.api_key,'[REDACTED]').replace(self.token,'[REDACTED]')[:1024]
            except Exception:pass
            finally:exc.close()
        undispatched = phase in ('request_validation', 'request_admission', 'request_context')
        diagnostic['upstream_dispatched'] = not undispatched
        self.events.append(diagnostic)
        if reservation:
            if self.checkpoint_store is not None:
                try:self.checkpoint_store.error(reservation,type(exc).__name__)
                except OSError:pass
            # No opener call is reachable in these phases. Do not retain a
            # fictitious provider charge for a host-only deadline/admission error.
            try:self.ledger.finish(reservation,0 if undispatched else None,None,
                                   'not_dispatched' if undispatched else 'outcome_unknown')
            except BudgetDenied:pass
        return diagnostic

    def close(self):
        self.closing=True
        self.server.shutdown()
        self.server.server_close()
        # Settle an already admitted request before archiving its usage. It has
        # an absolute bounded upstream deadline; never start a replacement.
        with self.lock:
            pass
