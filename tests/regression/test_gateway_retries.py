"""Real gateway/ledger exercised with an in-memory upstream, no model calls."""
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import urllib.error
from unittest.mock import patch

from benchmark.common.api import ROOT
from tests.common.execution.gateway import Gateway
from tests.common.execution.retry import delay
from tests.common.execution.runtime import request_records


def response():
    return io.BytesIO(('data: '+json.dumps({'type':'response.completed', 'response':{
        'id':'response-fixture','status':'completed','model':'offline-fixture-model',
        'output':[], 'usage':{'input_tokens':20,'output_tokens':10,
                             'input_tokens_details':{'cached_tokens':0,'cache_write_tokens':0}}}})+'\n\n').encode())


class Partial(io.BytesIO):
    def __init__(self):
        super().__init__(b'data: {"type":"response.created"}\n\n')
        self.sent = False

    def read1(self, n):
        if self.sent:
            raise TimeoutError('fixture timeout')
        self.sent = True
        return super().read(n)


class GatewayRetries(unittest.TestCase):
    def test_local_repair_preserves_original_deadline_and_shutdown_evidence(self):
        from unittest.mock import Mock
        for closing in (False, True):
            with self.subTest(closing=closing):
                gateway=Gateway.__new__(Gateway)
                gateway.ledger=SimpleNamespace(finish=Mock(side_effect=OSError('fixture')))
                gateway.responses=SimpleNamespace(settle=Mock())
                gateway.repair_settlement=True
                gateway.closing=closing
                gateway.deadline=10
                gateway.events=[]
                with patch('tests.common.execution.gateway.time.monotonic',return_value=11):
                    with self.assertRaises(OSError):gateway._settle_response('request',65,{})
                gateway.responses.settle.assert_not_called()
                self.assertEqual(gateway.events[0]['reason'],
                    'controller_closing' if closing else 'episode_deadline_reached')
                self.assertEqual(gateway.events[0]['new_upstream_requests'],0)

    def exercise(self, outcomes, *, max_requests=8, failure=None, deadline=30, repair_settlement=False):
        with tempfile.TemporaryDirectory(dir=ROOT/'run') as temp:
            # A private fixture must stay independent of the user's live
            # provider configuration and the deliberately unconfigured template.
            pricing = {
                'currency': 'CNY', 'authority': 'user_provided',
                'source': 'Synthetic offline fixture', 'checked_at': '2026-11-06',
                'provider_url': 'https://provider.example.invalid/v1',
                'model': 'offline-fixture-model',
                'max_input_tokens_per_request': 1024000,
                'max_input_cny_per_million': 2.030,
                'max_output_cny_per_million': 7.308,
                'max_other_cny_per_request': 0,
                'output_limit_includes_reasoning': True,
                'cache_write_semantics': 'replaces_ordinary_input',
                'threshold_input_tokens': 272000,
                'member_rates': {
                    'short': {'input': .812, 'cache_write': 1.015, 'cache_read': .081, 'output': 4.872},
                    'long': {'input': 1.624, 'cache_write': 2.030, 'cache_read': .162, 'output': 7.308}}}
            pricing_path = Path(temp) / 'pricing.json'
            pricing_path.write_text(json.dumps(pricing))
            gateway=Gateway(provider_url=pricing['provider_url'],model=pricing['model'],
                            api_key='local-fixture-secret',token='local-token',
                            pricing_path=pricing_path,
                            ledger_path=Path(temp)/'ledger.sqlite',episode='fixture',
                            episode_limit_cny=20,max_requests=max_requests,
                            deadline_seconds=deadline,request_timeout_seconds=deadline,
                            repair_settlement=repair_settlement)
            calls=[]
            def upstream(request,timeout):
                calls.append(request.data)
                outcome=outcomes[len(calls)-1]
                if isinstance(outcome,Exception):raise outcome
                return outcome()
            gateway.opener=SimpleNamespace(open=upstream)
            statuses=[]
            handler=SimpleNamespace(path='/v1/responses',
                headers={'Host':f'127.0.0.1:{gateway.port}','Authorization':'Bearer local-token'},
                connection=SimpleNamespace(settimeout=lambda n:None),
                rfile=io.BytesIO(json.dumps({'model':gateway.model,'input':'fixture'}).encode()),
                wfile=io.BytesIO(),send_response=lambda n:statuses.append(n),
                send_error=lambda n,*args:statuses.append(n),send_header=lambda *args:None,end_headers=lambda:None)
            handler.headers['Content-Length']=str(len(handler.rfile.getvalue()))
            try:
                with patch('tests.common.execution.retry.BACKOFF',(0.,0.)):
                    if failure in ('settle','settle_committed','settle_repair_failed','settle_bound'):
                        original=gateway.ledger.finish
                        def finish(ident,charged,usage,status):
                            if status=='usage_recorded':
                                if failure=='settle_bound':
                                    from tests.common.execution.budget import BudgetDenied
                                    raise BudgetDenied('Fixture accounting conflict')
                                if failure=='settle_committed':original(ident,charged,usage,status)
                                raise OSError('fixture disk failure')
                            return original(ident,charged,usage,status)
                        with patch.object(gateway.ledger,'finish',side_effect=finish):
                            if failure=='settle_repair_failed':
                                with patch.object(gateway.responses,'settle',side_effect=OSError('still unavailable')) as repair:
                                    gateway.handle(handler)
                                    self.assertEqual(repair.call_count,1)
                            else:gateway.handle(handler)
                    elif failure=='delivery':
                        handler.wfile=SimpleNamespace(write=lambda data:(_ for _ in ()).throw(BrokenPipeError()))
                        gateway.handle(handler)
                    elif failure=='context':
                        with patch.object(gateway.responses,'prepare',side_effect=OSError('fixture context disk failure')):
                            gateway.handle(handler)
                    else:gateway.handle(handler)
                return {'calls':calls,'statuses':statuses,'ledger':gateway.ledger.summary(),
                        'events':list(gateway.events),'records':request_records(gateway),
                        'receipts':[json.loads(p.read_text()) for p in Path(temp).glob('response-receipts/*/*.json')],
                        'raw':[p.read_bytes() for p in Path(temp).glob('response-receipts/*/*.sse')],
                        'delivered':handler.wfile.getvalue() if isinstance(handler.wfile,io.BytesIO) else None}
            finally:gateway.close()

    def test_duplicate_terminal_is_delivered_once_and_conflict_stops_without_retry(self):
        raw=response().getvalue()
        success=self.exercise([lambda:io.BytesIO(raw+raw)])
        self.assertEqual(success['statuses'],[200])
        self.assertEqual(len(success['calls']),1)
        self.assertEqual(success['delivered'].count(b'response.completed'),1)
        conflicting=raw+raw.replace(b'response-fixture',b'different-id-local-fixture-secret')
        failure=self.exercise([lambda:io.BytesIO(conflicting),response])
        self.assertEqual(failure['statuses'],[502])
        self.assertEqual(len(failure['calls']),1)
        self.assertEqual(len(failure['raw']),1)
        self.assertNotIn(b'local-fixture-secret',failure['raw'][0])
        self.assertFalse(failure['receipts'])
        from tests.common.execution.termination import classify
        termination=classify({},failure['events'])
        self.assertEqual(termination['reason'],'provider_protocol')
        self.assertFalse(termination['new_generation_allowed'])

    def test_headers_timeout_retries_once_and_retains_unknown_cost(self):
        r=self.exercise([TimeoutError(),response])
        self.assertEqual(r['statuses'],[200]);self.assertEqual(len(r['calls']),2)
        self.assertEqual(r['calls'][0],r['calls'][1])
        self.assertEqual([x['status'] for x in r['ledger']['requests']],['outcome_unknown','usage_recorded'])
        self.assertEqual([x['attempt'] for x in r['records']],[0,1])
        self.assertEqual(len({x['logical_request_id'] for x in r['records']}),1)
        self.assertGreater(r['ledger']['accounted_upper_cny'],r['ledger']['requests'][1]['settled_upper_cny'])

    def test_partial_stream_not_delivered_before_retry(self):
        r=self.exercise([Partial,response]);self.assertEqual(r['statuses'],[200])
        error=r['records'][0]['error'];self.assertEqual(error['phase'],'upstream_body')
        self.assertGreater(error['response_bytes_received'],0)

    def test_clean_eof_without_terminal_retries(self):
        r=self.exercise([lambda:io.BytesIO(b'data: {}\n\n'),response])
        self.assertEqual(r['statuses'],[200]);self.assertEqual(len(r['calls']),2)

    def test_maximum_three_upstream_attempts(self):
        r=self.exercise([TimeoutError(),TimeoutError(),TimeoutError()])
        self.assertEqual(len(r['calls']),3);self.assertEqual(r['statuses'],[502])
        self.assertTrue(all(x['status']=='outcome_unknown' for x in r['ledger']['requests']))

    def test_request_budget_stops_retry_without_erasing_timeout(self):
        r=self.exercise([TimeoutError()],max_requests=1)
        self.assertEqual(len(r['calls']),1);self.assertEqual(r['statuses'],[502])
        self.assertTrue(any(x['event']=='retry_stopped' for x in r['events']))
        self.assertEqual(r['records'][0]['error']['error_type'],'TimeoutError')

    def test_balance_failure_not_retried(self):
        err=urllib.error.HTTPError('https://fixture',429,'quota',{},io.BytesIO(b'{"error":{"code":"insufficient_quota"}}'))
        r=self.exercise([err]);self.assertEqual(len(r['calls']),1)

    def test_authentication_failure_not_retried(self):
        err=urllib.error.HTTPError('https://fixture',401,'auth',{},io.BytesIO(b'{}'))
        r=self.exercise([err]);self.assertEqual(len(r['calls']),1)

    def test_retry_after_outside_deadline_stops(self):
        err=urllib.error.HTTPError('https://fixture',503,'busy',{'Retry-After':'3600'},io.BytesIO(b'{}'))
        r=self.exercise([err]);self.assertEqual(len(r['calls']),1)

    def test_transient_http_service_failure_retries(self):
        err=urllib.error.HTTPError('https://fixture',503,'busy',{},io.BytesIO(b'{}'))
        r=self.exercise([err,response]);self.assertEqual(r['statuses'],[200])
        self.assertEqual(len(r['calls']),2)

    def test_terminal_service_failure_retains_accounting_and_retries(self):
        def failed():
            return io.BytesIO(('data: '+json.dumps({'type':'response.failed','response':{
                'id':'failed','status':'failed','error':{'code':'server_error'},'output':[],
                'usage':{'input_tokens':20,'output_tokens':10}}})+'\n\n').encode())
        r=self.exercise([failed,response]);self.assertEqual(r['statuses'],[200])
        self.assertEqual(len(r['calls']),2)
        self.assertEqual(r['records'][0]['response_status'],'failed')
        self.assertEqual(r['ledger']['requests'][0]['status'],'usage_recorded')

    def test_settlement_failure_keeps_receipt_and_does_not_regenerate(self):
        r=self.exercise([response],failure='settle')
        self.assertEqual(len(r['calls']),1);self.assertEqual(len(r['receipts']),1)
        self.assertEqual(r['records'][0]['error']['phase'],'usage_settlement')

    def test_delivery_failure_does_not_regenerate_or_double_charge(self):
        r=self.exercise([response],failure='delivery')
        self.assertEqual(len(r['calls']),1);self.assertEqual(len(r['receipts']),1)
        self.assertEqual(r['ledger']['requests'][0]['status'],'usage_recorded')

    def test_local_settlement_repair_delivers_original_receipt_once(self):
        for failure in ('settle','settle_committed'):
            with self.subTest(failure=failure):
                r=self.exercise([response],failure=failure,repair_settlement=True)
                self.assertEqual(r['statuses'],[200])
                self.assertEqual(len(r['calls']),1)
                self.assertEqual(len(r['ledger']['requests']),1)
                self.assertEqual(r['ledger']['requests'][0]['status'],'usage_recorded')
                self.assertEqual(sum(e['event']=='local_settlement_recovered' for e in r['events']),1)
                self.assertEqual(sum(e['event']=='delivery_completed' for e in r['events']),1)
                self.assertEqual(r['records'][0]['local_recovery']['new_upstream_requests'],0)
                self.assertEqual(r['records'][0]['local_settlement_policy'],'saved-receipt-local-settlement/v1')
                self.assertFalse(any(e['event'] in ('error','retry_scheduled') for e in r['events']))

    def test_local_repair_failure_preserves_receipt_without_upstream_retry(self):
        r=self.exercise([response],failure='settle_repair_failed',repair_settlement=True)
        self.assertEqual(r['statuses'],[502])
        self.assertEqual(len(r['calls']),1)
        self.assertEqual(len(r['receipts']),1)
        self.assertEqual(r['ledger']['requests'][0]['status'],'outcome_unknown')
        self.assertFalse(any(e['event'].startswith('delivery_') for e in r['events']))

    def test_accounting_conflicts_are_not_treated_as_transient_disk_failures(self):
        r=self.exercise([response],failure='settle_bound',repair_settlement=True)
        self.assertEqual(r['statuses'],[429])
        self.assertEqual(len(r['calls']),1)
        self.assertFalse(any(e['event']=='local_settlement_recovered' for e in r['events']))

    def test_context_storage_failure_keeps_reservation_in_exported_accounting(self):
        r=self.exercise([],failure='context')
        self.assertEqual(r['calls'],[])
        self.assertEqual(r['statuses'],[502])
        self.assertEqual(len(r['records']),1)
        self.assertEqual(r['records'][0]['error']['phase'],'request_context')
        self.assertEqual(r['ledger']['requests'][0]['status'],'not_dispatched')
        self.assertEqual(r['ledger']['accounted_upper_cny'],0)
        self.assertIsNone(r['records'][0]['accounting']['raw_usage'])
        self.assertEqual(r['records'][0]['accounting']['evidence'],'host_no_upstream_dispatch')
        from tests.common.execution.accounting import summarize
        self.assertEqual(summarize(r['records'])['cost_upper_cny'],0)
        self.assertTrue(summarize(r['records'])['accounting_complete'])

    def test_policy_delays_and_nontransport_errors(self):
        d={'phase':'upstream_headers','error_type':'TimeoutError'}
        self.assertEqual([delay(d,n) for n in range(3)],[2.,5.,None])
        self.assertEqual(delay(dict(d,retry_after='12'),0),12.)
        self.assertIsNone(delay(dict(d,phase='usage_settlement'),0))


if __name__=='__main__':unittest.main()
