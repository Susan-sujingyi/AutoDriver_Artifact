"""Finite native event checks against recovered framework/resource contracts.

Executors must supply observations from a candidate. This module is a monitor,
not a native executor; tests with synthetic events are labelled as such.
"""
from dataclasses import dataclass, field

from .model import ContractError, Unknown, Node, unwrap
from .data import commit,ValidFrame


@dataclass
class Request:
    operation: str
    instance: str
    buffer: str | None
    status: str = "submitted"


@dataclass
class NativeMonitor:
    api: Node
    buffer_owners: dict = field(default_factory=dict)
    requests: dict = field(default_factory=dict)
    frames: dict = field(default_factory=dict)
    committed_sample: object = None
    checks: int = 0
    gaps: list = field(default_factory=list)

    def __post_init__(self):
        if self.api.name not in {"APIContract", "AsyncI2CContract"}:
            raise ContractError("native monitor requires a recovered API contract")

    def has(self, value, name):
        value = unwrap(value)
        if isinstance(value, Node):
            if value.name == name: return True
            if value.name in {'Callback','SyncReturn'}:return self.has(value['resource_effect'],name)
            # An effect found inside an arbitrary guard/FSM branch does not
            # establish an unconditional resource guarantee.
            return False
        if isinstance(value, (tuple,list)):
            return any(self.has(v,name) for v in value)
        return False

    def guarantee(self,slot,name):
        def encoded(api):
            if self.has(api[slot],name):return True
            if api.name=='APIContract':
                return self.has(api.get('resource_effect'),name)
            return False
        try:
            known=encoded(self.api)
            if not known and self.api.name=='AsyncI2CContract':
                endpoints=[self.api.get(key) for key in ('write','read','write_read') if self.api.get(key) is not None]
                known=bool(endpoints) and all(isinstance(api,Node) and api.name=='APIContract' and encoded(api) for api in endpoints)
        except Unknown:known=False
        if not known:
            gap='native '+slot+' resource guarantee is not encoded as '+name
            if gap not in self.gaps:self.gaps.append(gap)
        return known

    def returned(self,request,returned_buffer,required):
        if returned_buffer==request.buffer and returned_buffer is not None:
            self.buffer_owners[returned_buffer]='caller'
        elif required and returned_buffer!=request.buffer:
            raise ContractError('completion/rejection lost or replaced the current buffer')
        elif request.buffer is not None:
            self.buffer_owners[request.buffer]=('unknown',request.operation,request.instance)

    def feed(self, *, operation, instance, phase, buffer=None, returned_buffer=None,
             required_frames=(), frame=None):
        key = (operation,instance)
        self.checks += 1
        if not operation or not instance:
            raise ContractError("native event must identify current operation")
        if phase == "submit":
            if key in self.requests: raise ContractError("request identity reused")
            if buffer is not None and self.buffer_owners.get(buffer,"caller") != "caller":
                owner=self.buffer_owners[buffer]
                if isinstance(owner,tuple) and owner[0]=='unknown':
                    self.gaps.append('buffer reuse cannot be resolved from the recovered resource effects')
                else:raise ContractError("submitted buffer is still owned by framework")
            self.requests[key] = Request(operation,instance,buffer)
            if buffer is not None:self.buffer_owners[buffer] = ('submitting',key)
            return
        request = self.requests.get(key)
        if request is None: raise ContractError("event belongs to unknown/stale request")
        if phase == "accepted":
            if request.status != "submitted": raise ContractError("request accepted twice or after completion")
            known=self.guarantee('accepted','HoldsBufferUntilCompletion')
            if request.buffer is not None:self.buffer_owners[request.buffer] = key if known else ('unknown',operation,instance)
            request.status = "accepted"
        elif phase == "rejected":
            if request.status != "submitted":
                raise ContractError("accepted request cannot be mapped to synchronous rejection")
            self.returned(request,returned_buffer,self.guarantee('rejected','ReturnsErrorAndBuffer'))
            request.status = "rejected"
        elif phase in {"callback_success", "callback_failure"}:
            if request.status != "accepted":
                raise ContractError("stale/duplicate callback or callback before acceptance")
            self.returned(request,returned_buffer,self.guarantee('completion','ReturnsBufferToClient'))
            request.status = phase
        elif phase == "frame_valid":
            if request.status != "callback_success" or not isinstance(frame,ValidFrame) or frame.instance != instance:
                raise ContractError("invalid or foreign frame cannot be published")
            self.frames[frame.name] = frame
        elif phase == "commit":
            if request.status != "callback_success":
                raise ContractError("failed or incomplete acquisition cannot publish success")
            self.committed_sample = commit(self.frames,required_frames,instance)
        elif phase == "use_buffer":
            if buffer is None or self.buffer_owners.get(buffer,"caller") != "caller":
                owner=self.buffer_owners.get(buffer)
                if isinstance(owner,tuple) and owner[0]=='unknown':
                    self.gaps.append('buffer use ownership is unknown from the recovered effects');return
                raise ContractError("buffer used before ownership return")
        else:
            raise Unknown("native observation phase is not implemented")

    def result(self):
        pending = [r for r in self.requests.values() if r.status in {"submitted","accepted"}]
        held = [name for name,owner in self.buffer_owners.items() if owner != "caller"]
        return {"status": "unknown" if self.gaps or pending or held else "satisfied", "checks": self.checks,
                "pending_requests": len(pending), "held_buffers": held,
                'gaps':self.gaps,
                "scope": "observed finite native events; does not establish all paths or liveness"}
