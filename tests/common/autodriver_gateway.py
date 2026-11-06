"""Autodriver's fixed public-tool endpoint on its existing bounded gateway."""
import hmac
import json
import time

from tests.common.execution.gateway import Gateway


class AutodriverGateway(Gateway):
    def __init__(self,*,candidate_tools,**kwargs):
        self.candidate_tools=candidate_tools
        super().__init__(**kwargs)

    def handle(self,handler):
        if handler.path!='/v1/autodriver/candidate-check':return super().handle(handler)
        if (handler.headers.get('Host')!=f'127.0.0.1:{self.port}'
                or not hmac.compare_digest(handler.headers.get('Authorization',''),'Bearer '+self.token)
                or handler.headers.get('Content-Encoding') not in (None,'identity')
                or handler.headers.get('Transfer-Encoding')):
            handler.send_error(403);return
        try:
            if self.closing or time.monotonic()>=self.deadline:raise TimeoutError('shared trial deadline reached')
            length=int(handler.headers.get('Content-Length','0'))
            if not 0<length<=1024:raise ValueError('finite tool request exceeded size bound')
            handler.connection.settimeout(10);request=json.loads(handler.rfile.read(length))
            result=self.candidate_tools.check(request)
            data=json.dumps(result,ensure_ascii=False).encode()
            if len(data)>1_000_000:raise ValueError('complete tool diagnostic exceeds bounded response')
            handler.send_response(200);handler.send_header('Content-Type','application/json')
            handler.send_header('Content-Length',str(len(data)));handler.end_headers();handler.wfile.write(data)

            self.events.append({'event':'autodriver_public_tool','revision':request['revision'],'status':result['status'],
                'attempt':result['attempt'],'model_requests':0,'independent_scoring':'not_run','hardware':'not_run'})
        except (ValueError,KeyError,TypeError,TimeoutError) as error:
            data=json.dumps({'error':type(error).__name__,'reason':str(error)}).encode()
            handler.send_response(400);handler.send_header('Content-Type','application/json')
            handler.send_header('Content-Length',str(len(data)));handler.end_headers();handler.wfile.write(data)

    def close(self):
        super().close()
        # Tool subprocesses use the same absolute deadline. Settle their
        # bounded cleanup before collecting final source or freezing it.
        with self.candidate_tools.lock:
            pass
