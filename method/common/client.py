"""Agent-side plain-text Responses client for the fixed host gateway only."""
import json
import os
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


def complete(prompt, system='You are a helpful assistant.', stage='generation', *, step=None):
    base = os.environ['AUTODRIVER_BOUNDED_GATEWAY_URL']
    url = urlsplit(base)
    if url.scheme != 'http' or url.hostname != '127.0.0.1' or url.path != '/v1':
        raise ValueError('Expected fixed local gateway')
    body = {'model': os.environ['OPENAI_MODEL'], 'instructions': system,
            'input': prompt, 'stream': True, 'store': False,
            'metadata': {'method_stage': stage}}
    if step is not None:
        body['metadata']['method_step'] = step
    configured = json.loads(os.environ.get('AUTODRIVER_MODEL_SETTINGS', '{}'))
    if set(configured) - {'reasoning', 'max_output_tokens'}:
        raise ValueError('Unsupported explicit model settings')
    body.update(configured)
    key = os.environ['AUTODRIVER_GATEWAY_TOKEN']
    request = urllib.request.Request(base + '/responses', data=json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    started = time.time()
    directory = Path(os.environ['AUTODRIVER_METHOD_LOGS'])
    directory.mkdir(exist_ok=True, parents=True)
    record = {'stage': stage, 'prompt': prompt, 'system': system, 'started': started}
    name = directory / ('request-' + str(time.time_ns()) + '.json')
    name.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    try:
        # Host gateway owns retry/deadline enforcement. Do not abandon a
        # buffered response while the host is still making authorized retries.
        with opener.open(request, timeout=float(os.environ.get('AUTODRIVER_GATEWAY_WAIT_SECONDS', '1805'))) as response:
            raw = response.read().decode('utf-8')
        final = None
        for line in raw.splitlines():
            if not line.startswith('data: '):
                continue
            try:
                event = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            if event.get('type') in ('response.completed', 'response.incomplete', 'response.failed'):
                final = event.get('response', {})
        if final is None:
            raise RuntimeError('No terminal Responses event')
        text = ''.join(c.get('text', '') for item in final.get('output', [])
                       if item.get('type') == 'message' for c in item.get('content', [])
                       if c.get('type') == 'output_text')
        record.update(response=text, usage=final.get('usage'), response_model=final.get('model'),
                      response_status=final.get('status'))
        if final.get('status') != 'completed':
            raise RuntimeError('Model response did not complete; do not accept truncated extraction')
        if not text:
            raise RuntimeError('Empty model response')
        return text
    except Exception as error:
        record['error_type'] = type(error).__name__
        raise
    finally:
        record['finished'] = time.time()
        name.write_text(json.dumps(record, ensure_ascii=False, indent=2))

