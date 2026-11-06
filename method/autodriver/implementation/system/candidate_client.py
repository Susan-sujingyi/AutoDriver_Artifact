"""Method-side client for one fixed, hash-addressed public check operation."""
import json
import os
from pathlib import Path
import urllib.request
from urllib.parse import urlsplit

from .protocol.assembler import digest


def check_candidate(workspace,proposal):
    base=os.environ['AUTODRIVER_BOUNDED_GATEWAY_URL'];url=urlsplit(base)
    if url.scheme!='http' or url.hostname!='127.0.0.1' or url.path!='/v1':raise ValueError('fixed local host gateway required')
    directory=Path(workspace)/'output/autodriver'
    body={'schema':'autodriver.candidate-check-request/v1','revision':proposal['revision'],
        'proposal_sha256':digest((directory/'code-proposal.json').read_bytes()),
        'state_sha256':digest((directory/'contract-state.json').read_bytes())}
    request=urllib.request.Request(base+'/autodriver/candidate-check',data=json.dumps(body).encode(),
        headers={'Content-Type':'application/json','Authorization':'Bearer '+os.environ['AUTODRIVER_GATEWAY_TOKEN']})
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request,timeout=float(os.environ.get('AUTODRIVER_GATEWAY_WAIT_SECONDS','1805'))) as response:
        raw=response.read(1_000_001)
    if len(raw)>1_000_000:raise ValueError('host tool response exceeds bounded diagnostic size')
    result=json.loads(raw)
    if result.get('schema')!='autodriver.candidate-check/v1' or result.get('revision')!=proposal['revision']:
        raise ValueError('candidate check response identity mismatch')
    return result
