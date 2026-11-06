"""Bounded success-prefix witnesses from the single lowered contract graph.

This creates stimuli, never candidate observations. Frame values and branch
constraints come only from the current contract, with no device answer table.
"""
import time

from ..contract.model import Unknown,ContractError
from ..contract.fsm import lower


def paths(contract,*,maximum_paths=128,maximum_edges=256):
    graph=lower(contract['behavior']);adjacent={}
    for edge in graph.edges:adjacent.setdefault(edge.source,[]).append(edge)
    stack=[(graph.initial,[],{})];yielded=0;expanded=0
    while stack:
        state,path,visits=stack.pop();expanded+=1
        if expanded>20000:raise Unknown('prefix graph search bound exceeded')
        for edge in reversed(adjacent.get(state,[])):
            if edge.target in {graph.failing,graph.unknown} or edge.event and edge.event[1]=='physical_failure':continue
            if edge.action and edge.action[0]=='fail':continue
            if len(path)>=maximum_edges or visits.get(edge.target,0)>=2:continue
            if edge.guard is False:continue
            current=path+[edge]
            if edge.action and edge.action[0]=='commit':
                yield current,[item.guard for item in current];yielded+=1
                if yielded>=maximum_paths:return
            else:stack.append((edge.target,current,{**visits,edge.target:visits.get(edge.target,0)+1}))


def frame_names(operation):
    if operation.name=='ReadFrame':return [operation['frame']]
    if operation.name=='Transfer':return [s['into'] for s in operation['segments'] if s.name=='ReadSegment']
    if operation.name=='ReadRegister':return []  # Scalar output, not an implicitly declared frame.
    return []


def success_prefix(contract,bus):
    from .witness import PathInputs
    reasons=[];started=time.monotonic();solver_calls=0
    for edges,guards in paths(contract):
        if solver_calls>=16 or time.monotonic()-started>10:raise Unknown('prefix witness solver budget exhausted')
        solver_calls+=1
        try:
            inputs=PathInputs(contract,lower(contract['behavior']),edges)
            return inputs.replay(bus,inputs.encode())
        except (Unknown,ContractError,KeyError) as error:reasons.append(str(error))
    raise Unknown('no executable guarded prefix witness: '+'; '.join(reasons[:4]))


def wire_response(step,frames):
    wire=step['wire'];payload=bytearray(wire.read_length or len(wire.write))
    for name,start,length in wire.slices:
        data=step.get('register_payloads',{}).get(name)
        if data is None:
            data=step.get('frame_payloads',{}).get(name) if step['phase']=='initialization' else frames.get(name,step.get('frame_payloads',{}).get(name))
        if data is None:raise Unknown('prefix response has no current typed read input')
        if len(data)!=length:raise Unknown('complete named frame must fit its declared read segment')
        payload[start:start+length]=data
    return list(payload)
