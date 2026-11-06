"""Derive binding obligations from pinned parents, without duplicating device data."""
from collections.abc import Mapping

from .model import Node,Unknown,ContractError,References,Pinned,walk,identity,unwrap
from .check import Report
from .timing import check_tick_choice,check_parametric_tick_choice
from .data import integer,check_layout


ACTIONS={'WriteBytes','Transfer','ReadRegister','WriteRegister','UpdateRegister','ReadFrame','DriveSignal','SampleSignal',
         'WaitAfterCompletion','WaitFrom','CommitValidatedSample'}


def binding_inventory(dbc,fp,support):
    """Exact read-only paths, not a prewritten mapping or device answer."""
    def leaves(value,path):
        if isinstance(value,dict):
            return [item for key,v in value.items() for item in leaves(v,path+'.'+key)]
        if isinstance(value,(list,tuple)):
            return [item for i,v in enumerate(value) for item in leaves(v,path+'.'+str(i))]
        return [{'path':path,'value':value}]
    return {'device_actions':[{'path':'dbc.'+path,'primitive':value.name,'name':value.get('name')}
                              for path,value in walk(dbc['behavior'],'behavior') if value.name in ACTIONS],
            'framework_nodes':[{'path':'fp.'+path,'constructor':value.name,'symbol':value.get('symbol')}
                               for path,value in walk(fp) if value.name not in {'Unspecified','Unresolved','Constraint'}],
            'support_fields':leaves(support,'support'),
            'scope':'exact existing paths only; a path inventory establishes neither event guarantees nor a valid binding'}


def check_binding(dbc,fp,bp,support,*,parent_hashes,parent_ids=None):
    objects={'dbc':Pinned(dbc,identity(dbc)),'fp':Pinned(fp,identity(fp)),
             'support':Pinned(support,identity(support))}
    expected=dict(parent_hashes)
    parent_ids=parent_ids or {'dbc':'dbc','fp':'fp','support':'support'}
    for alias,material_id in parent_ids.items():
        objects[material_id]=objects[alias];expected[material_id]=expected[alias]
    references=References(objects,expected)
    report=Report('bp',identity(bp))
    tick_proofs={}
    for key,alias in [('device_contract','dbc'),('framework_profile','fp'),('target_support','support')]:
        def parent():
            reference=bp[key]
            if not isinstance(reference,Node) or reference.name!='Ref':raise ContractError('parent must be an explicit pinned Ref')
            if reference['identifier']!=parent_ids[alias]:raise Unknown('BP parent ID needs its exact declared material mapping')
            references.resolve(reference)
        report.attempt(key,'pinned_parent',parent)
    actions=[(path,value) for path,value in walk(dbc['behavior'],'behavior') if value.name in ACTIONS]
    mapping_unknown=False
    try:
        bindings=unwrap(bp['primitive_bindings'])
    except Unknown as error:
        report.add('primitive_bindings','unknown','binding_map_recovered',str(error))
        bindings=None;mapping_unknown=True
    if not mapping_unknown and not isinstance(bindings,Mapping):
        report.add('primitive_bindings','violated','binding_map_type','primitive bindings must be a mapping')
        bindings={}
    try:connection=unwrap(dbc['connection'])
    except Unknown:connection=None
    for path,operation in actions:
        if mapping_unknown:
            report.add(path,'unknown','action_coverage','the primitive binding map remains explicitly unresolved')
            continue
        binding=bindings.get('dbc.'+path,bindings.get(path,bindings.get(operation.name)))
        if binding is None:
            report.add(path,'violated','action_coverage','no exact path or generic primitive binding')
            continue
        try:
            binding=unwrap(binding)
        except Unknown as error:
            report.add(path,'unknown','binding_recovered',str(error))
            continue
        if not isinstance(binding,Node):
            report.add(path,'violated','binding_type','binding must be a typed mechanism')
            continue
        length=binding.get('length')
        if getattr(length,'name',None)=='FromDBCFrameLayout' and getattr(connection,'name',None)=='SPI':
            def check_spi_length():
                from .wire import transaction
                if operation.name=='ReadFrame':frames=[operation['frame']]
                elif operation.name=='Transfer':frames=[s['into'] for s in operation['segments'] if s.name=='ReadSegment']
                else:frames=[]
                if len(frames)!=1 or frames[0] not in dbc['frames']:raise ContractError('FromDBCFrameLayout needs one declared read frame')
                wire=transaction(dbc,operation,'spi');frame=check_layout(dbc['frames'][frames[0]])
                if len(wire.write)!=frame['length']:
                    raise ContractError('SPI native length includes command/header plus read clocks; FromDBCFrameLayout omits those bytes. Use FromDBCOperation for the complete transaction length')
            report.attempt(path,'spi_native_transaction_length',check_spi_length)
        if operation.name in {'WaitFrom','WaitAfterCompletion'}:
            if binding.name=='BindSyncDelay':
                from .delay import check_binding as check_sync_delay
                def check_delay():tick_proofs[path]=check_sync_delay(operation,path,binding,references)
                report.attempt(path,'synchronous_delay_lower_bound',check_delay)
                continue
            if binding.name!='BindAlarm':
                report.add(path,'unknown','wait_mechanism','typed BindAlarm or BindSyncDelay lower-bound obligation required')
                continue
            choice=binding['tick_choice']
            if not isinstance(choice,Node) or choice.name!='CheckedPhysicalLowerBound':
                report.add(path,'violated','tick_choice','typed physical lower-bound obligation required')
                continue
            def check_wait():
                ticks=choice['ticks'];bounds=unwrap(choice['clock_bounds']);phase=unwrap(choice['phase_loss'])
                if bounds is None or ticks is None or phase is None:raise Unknown('clock/tick/phase parameters not recovered')
                if not hasattr(bounds,'keys') or set(bounds)!={'frequency_max','minimum_ticks','maximum_ticks','reference_not_before_anchor','callback_not_before_deadline'}:
                    raise Unknown('clock bounds need frequency/range/reference/callback premises')
                arguments=dict(minimum_ticks=bounds['minimum_ticks'],maximum_ticks=bounds['maximum_ticks'],
                    reference_not_before_anchor=bounds['reference_not_before_anchor'],callback_not_before_deadline=bounds['callback_not_before_deadline'])
                if choice['variables']:
                    proof=check_parametric_tick_choice(unwrap(operation['minimum']),ticks,bounds['frequency_max'],phase,
                        variables=choice['variables'],assuming=choice['assuming'],**arguments)
                    tick_proofs[path]=proof
                    if proof['status']=='violated':raise ContractError('parametric tick choice violates a declared lower/range/integer bound: '+str(proof.get('counterexample')))
                    if proof['status']!='satisfied':raise Unknown(proof.get('reason','parametric clock arithmetic unresolved'))
                else:
                    tick_proofs[path]=check_tick_choice(unwrap(operation['minimum']),ticks,bounds['frequency_max'],phase,**arguments)
                if operation['maximum'] is not None:raise Unknown('physical upper-bound scheduling latency is not established')
            report.attempt(path,'tick_physical_lower_bound',check_wait)
            # Arithmetic does not establish truth of claimed provider premises.
            report.add(path,'unknown','timing_provider_evidence','clock and callback premises require source-backed assurance')
        elif operation.name=='CommitValidatedSample':
            if binding.name!='BindSampleCommit':report.add(path,'violated','sample_binding','logical commit requires an explicit sample binding')
            else:report.add(path,'satisfied','sample_binding_structure','sample validity and candidate publication order checked separately')
        elif binding.name in {'BindAsyncCall','BindSyncCall'}:
            def resolve_call():
                call=binding['call']
                if isinstance(call,Node) and call.name=='FromFP':call='fp.'+call['path'].removeprefix('fp.')
                resolved=references.resolve(call)
                if resolved is None:raise Unknown('native call not described by FP')
                if binding.name=='BindAsyncCall':
                    completion=binding['completion']
                    if isinstance(completion,Node) and completion.name=='EventRef':
                        raise ContractError('BindAsyncCall.completion must reference the parent FP Callback with FromFP(path), not EventRef; use EventRef inside EventBinding')
                    if isinstance(completion,Node) and completion.name=='FromFP':completion='fp.'+completion['path'].removeprefix('fp.')
                    resolved=unwrap(references.resolve(completion))
                    if isinstance(resolved,Node) and resolved.name!='Callback':raise ContractError('native async completion must reference the FP Callback field, not '+resolved.name)
                    if not isinstance(resolved,Node) or resolved.name!='Callback':raise Unknown('native completion reference does not resolve to a recovered FP Callback')
            report.attempt(path,'native_call_resolution',resolve_call)
        else:
            report.add(path,'unknown','operation_mapping','mechanism is not executable for this operation: '+binding.name)
    for path,value in walk(bp):
        if value.name=='EventRef' and value['instance']!='current':
            report.add(path,'violated','current_event_instance','EventRef.instance must be current; operation names belong in operation, not instance')
        if value.name in {'FromFP','FromDBC','FromTargetSupport'}:
            alias={'FromFP':'fp','FromDBC':'dbc','FromTargetSupport':'support'}[value.name]
            report.attempt(path,'exact_parent_path',lambda:references.resolve(alias+'.'+value['path'].removeprefix(alias+'.')))
    if bp['io_buffer'] is not None:
        try:
            buffer=unwrap(bp['io_buffer'])
        except Unknown as error:
            report.add('io_buffer','unknown','buffer_recovered',str(error))
            buffer=None
        if isinstance(buffer,Node) and buffer.name=='ReuseTargetBuffer':
            def check_capacity():
                capacity=unwrap(buffer['capacity'])
                if isinstance(capacity,Node) and capacity.name=='FromTargetSupport':capacity=references.resolve('support.'+capacity['path'].removeprefix('support.'))
                integer(capacity,0,65536)
                for layout in dbc['frames'].values():
                    if check_layout(layout)['length']>capacity:raise ContractError('device frame exceeds actual target buffer capacity')
            report.attempt('io_buffer','buffer_capacity',check_capacity)
    for alias,parent in [('dbc',dbc),('fp',fp)]:
        for path,value in walk(parent):
            if value.name in {'Unspecified','Unresolved','ProviderConformanceRequired'}:
                report.add(alias+'.'+path,'unknown','inherited_parent_gap',str(value.get('reason',value.get('relation'))))
        for gap in parent['unresolved']:
            report.add(alias+'.unresolved','unknown','inherited_parent_gap',str(gap))
    report.add('candidate','unknown','implementation_conformance','binding plan checks do not execute or prove the generated native implementation')
    result=report.as_dict();result['tick_proofs']=tick_proofs;return result
