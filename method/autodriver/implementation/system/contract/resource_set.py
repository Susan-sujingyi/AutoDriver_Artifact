"""Finite checks for a native call that transfers several named buffers.

Resource identities are observed pointer tokens. API effects still need their
own recovered evidence; a pair of pointers never establishes those effects.
"""
from collections.abc import Mapping
from dataclasses import dataclass,field

from .model import ContractError,Unknown
from .native import NativeMonitor


@dataclass
class BufferSetMonitor:
    api: object
    owners: dict=field(default_factory=dict)
    monitors: dict=field(default_factory=dict)
    requests: dict=field(default_factory=dict)

    def feed(self,*,operation,instance,phase,buffers=None,returned_buffers=None):
        key=(operation,instance)
        if phase=='submit':
            if key in self.requests:raise ContractError('request identity reused')
            if not isinstance(buffers,Mapping) or not buffers or len(buffers)>128:raise ContractError('bounded named submitted buffers required')
            if len(self.requests)>=10000:raise Unknown('finite resource-set request bound exceeded')
            if any(not isinstance(name,str) or not name or not isinstance(token,str) or not token for name,token in buffers.items()):
                raise ContractError('nonempty resource names and observed buffer tokens required')
            if len(set(buffers.values()))!=len(buffers):raise ContractError('exclusive submitted buffers alias one another')
            self.requests[key]=dict(buffers)
            for slot,token in buffers.items():
                monitor=self.monitors.setdefault(slot,NativeMonitor(self.api,buffer_owners=self.owners))
                monitor.feed(operation=operation,instance=instance,phase='submit',buffer=token)
            return
        if key not in self.requests:raise ContractError('event belongs to unknown/stale request')
        if phase not in {'accepted','rejected','callback_success','callback_failure'}:raise Unknown('unsupported buffer-set phase')
        current=self.requests[key]
        returned={} if returned_buffers is None else returned_buffers
        if not isinstance(returned,Mapping):raise ContractError('returned buffers require a named map')
        if set(returned)-set(current):raise ContractError('callback/rejection names a resource absent from this request')
        for slot,token in current.items():
            self.monitors[slot].feed(operation=operation,instance=instance,phase=phase,returned_buffer=returned.get(slot))

    def result(self):
        resources={slot:monitor.result() for slot,monitor in self.monitors.items()}
        pending={key for monitor in self.monitors.values() for key,request in monitor.requests.items() if request.status in {'submitted','accepted'}}
        return {'status':'unknown' if not resources or any(r['status']!='satisfied' for r in resources.values()) else 'satisfied',
                'resources':resources,'pending_requests':len(pending),
                'scope':'observed named buffers and recovered API effects only; no provider evidence or all-path/liveness proof'}
