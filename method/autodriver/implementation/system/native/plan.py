"""Finite public-HIL execution plans, derived from current contracts and ABI.

The virtual bus/clock is an explicit test environment, never physical hardware
or a proof of provider behavior. No benchmark evaluator or answer is imported.
"""
from fractions import Fraction
import json
from pathlib import Path
import re

from ..contract.model import ContractError,Unknown,UpstreamGap,Node,walk,unwrap,identity
from ..contract.stimuli import frame_cases
from ..contract.data import byte_sequence,check_layout,decode_frame,DataInvalid,ValidFrame,frame_environment,round_integer,round_float,float_bits_value
from ..contract.expression import scalar,quantity
from ..contract.fsm import Interpreter,InputEvent
from ..contract.native import NativeMonitor
from ..contract.wire import transaction


def tock_plan(task,contract,candidate,bp=None,fp=None):
    from .channels import scalar_channels,render_clients
    from .prefix import success_prefix,wire_response,frame_names
    target=task['target'];interface=target['interface'];candidate=Path(candidate).resolve(strict=True)
    required={'kernel::hil::i2c::I2CClient','kernel::hil::time::AlarmClient'}
    if target['os']!='tock' or not required.issubset(interface.get('traits',())):
        raise Unknown('native executor requires the declared public Tock I2C/Alarm ABI')
    channels=scalar_channels(task);clients=render_clients(channels)
    name=interface['type'].split('<',1)[0];module=target['module']
    if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*',name) or not re.fullmatch(r'[a-z_][a-z_0-9]*',module):
        raise ContractError('invalid public native type/module identifier')
    signature=re.sub(r'\s+','',interface['constructor'])
    if signature!="pubfnnew(i2c:&'aI,alarm:&'aA,buffer:&'staticmut[u8])->Self":
        raise Unknown('native constructor adapter does not support this declared signature')
    size=interface.get('buffer_bytes')
    if type(size) is not int or not 1<=size<=65536:raise ContractError('bounded public buffer size required')
    driver=(candidate/'target/capsules/extra/src'/module).with_suffix('.rs').resolve(strict=True)
    if not driver.is_relative_to(candidate/'target'):raise ContractError('native module escaped prepared target')
    prefix=success_prefix(contract,'i2c')
    initial=[step for step in prefix['steps'] if step['phase']=='initialization']
    acquire=[step for step in prefix['steps'] if step['phase']=='acquisition']
    if any(len(step['wire'].write)>size or step['wire'].read_length>size for step in prefix['steps']):
        raise Unknown('typed contract transaction exceeds the actual target buffer capacity')
    names={frame for step in acquire for frame in frame_names(step['operation'])}
    stimuli=[item for item in frame_cases(contract)['cases'] if item['frame'] in names]
    if not stimuli:raise Unknown('native adapter has no current acquisition frame stimuli')
    first=next((item for item in stimuli if item['case']=='middle' and item['expected_valid']),next(item for item in stimuli if item['expected_valid']))
    cases=[dict(item,case=item['frame']+':'+item['case'],async_failure=False,immediate_rejection=False,reentry=False) for item in stimuli]
    numeric_gaps=[]
    if bp is not None and fp is not None:
        from .numeric_stimuli import integer_ties
        extra=integer_ties(contract,fp,bp,names);numeric_gaps=extra['gaps']
        for item in extra['cases']:
            cases.append(dict(first,case=item['case'],frame_overrides=item['frames'],rounding_predicate=item['predicate'],
                async_failure=False,immediate_rejection=False,reentry=False))
    for mode in ('async_failure','immediate_rejection','reentry'):
        cases.append(dict(first,case=mode,**{key:key==mode for key in ('async_failure','immediate_rejection','reentry')}))
    cases=[dict(item,case=item['case']+':'+channel,requested_channel=channel) for item in cases for channel in channels]
    def rust_steps(rows):return ','.join('&['+','.join(map(str,row))+']' for row in rows)
    rows=[]
    for item in cases:
        frames={**prefix['frames'],item['frame']:bytes(item['data']),**item.get('frame_overrides',{})}
        responses=[wire_response(step,frames) for step in acquire]
        if 'frame_overrides' in item:item['frame_overrides']={key:list(value) for key,value in item['frame_overrides'].items()}
        rows.append('('+json.dumps(item['case'])+','+json.dumps(item['requested_channel'])+',&['+rust_steps(responses)+'],'+
            ','.join(str(item[key]).lower() for key in ('async_failure','immediate_rejection','reentry'))+')')
    source=(Path(__file__).parent/'tock_i2c_alarm_main.rs').read_text()
    source=source.replace('"@DRIVER_PATH@"',json.dumps(str(driver),ensure_ascii=False)).replace('@DRIVER_TYPE@',name).replace('@BUFFER_SIZE@',str(size))
    source=source.replace('@CASES@',','.join(rows)).replace('@INITIAL_STEPS@',rust_steps([wire_response(step,prefix['frames']) for step in initial]))
    for marker,value in {'CLIENT_IMPLEMENTATIONS':clients['implementations'],'SET_CLIENTS':clients['set_clients'],'REQUEST_MATCH':clients['request_match']}.items():
        source=source.replace('@'+marker+'@',value)
    if '@' in source or len(source.encode())>2000000:raise Unknown('native source placeholders or complete-source size bound unresolved')
    return {'schema':'autodriver.native-plan/v1','contract_sha256':identity(contract),'cases':cases,'source':source,
        'observer':(Path(__file__).parent/'tock_i2c_mock.rs').read_text(),
        'prefix':{key:prefix[key] for key in ('predicate','witness','scope')},'numeric_stimulus_gaps':numeric_gaps,
        'native_channels':channels,'environment':{'clock_hz':32768,'io_latency_ticks':1,'scheduler_step_bound':128},
        'scope':'actual candidate, public scalar I2C/Alarm HILs and one graph-derived input prefix; finite observations only, no hardware/provider/all-path/liveness/independent-scoring claim'}


def split_cases(stdout):
    cases={};current=None
    for line in stdout.splitlines():
        try:event=json.loads(line)
        except ValueError:raise ContractError('native stdout contains a non-event line') from None
        if not isinstance(event,dict) or 'phase' not in event:raise ContractError('invalid native event')
        if event['phase']=='case_begin':
            if current is not None or event['case'] in cases:raise ContractError('duplicate/unclosed native case')
            current=event['case'];cases[current]=[]
        elif event['phase']=='case_end':
            if current is None:raise ContractError('native case end has no start')
            current=None
        else:
            if current is None:raise ContractError('native observation outside a case')
            cases[current].append(event)
        if sum(map(len,cases.values()))>10000:raise Unknown('native observation bound exceeded')
    if current is not None:raise ContractError('native case did not finish')
    return cases


def io_matches(contract,operation,submitted):
    connection=unwrap(contract['connection'])
    if connection.name not in {'I2C','SPI'}:raise Unknown('native wire event projection requires typed I2C/SPI connection')
    wire=transaction(contract,operation,connection.name.lower());wire.check(submitted)
    return wire


class Projection:
    def __init__(self,contract):
        self.contract=contract;self.machine=Interpreter(contract);self.pending={};self.submitted={};self.errors=[];self.gaps=[];self.checked=0
        self.disabled=False;self.started=False;self.instance='initialization'

    def advance_waits(self,time):
        machine=self.machine;machine.settle()
        for _ in range(128):
            waits=[edge for edge in machine.graph.edges if edge.source==machine.state and edge.action and edge.action[0]=='wait' and machine.guard(edge.guard)]
            if not waits:return
            if len(waits)!=1:raise Unknown('ambiguous native wait projection')
            machine.feed(InputEvent(*waits[0].event,self.instance,time))
        raise Unknown('native wait-chain bound exceeded')

    def feed(self,event):
        phase=event['phase'];time=Fraction(event['tick'],event['frequency'])
        machine=self.machine
        if phase=='submit':self.submitted[event['operation']]=event
        if self.disabled:return
        try:
            if not self.started:
                machine.feed(InputEvent('initialize','logical_success',self.instance,time));self.started=True
            if phase=='request_boundary':
                if machine.state==machine.graph.accepting:
                    machine.feed(InputEvent('next_request','logical_success',self.instance,time))
                self.advance_waits(time)
                self.instance=event.get('instance','acquisition');machine.feed(InputEvent('acquire','logical_success',self.instance,time))
            elif phase=='physical_start':
                machine.settle()
                # Contract waits are observed at the next physical I/O start.
                # An ignored/stale alarm callback alone cannot violate a wait.
                self.advance_waits(time)
                edges=[edge for edge in machine.graph.edges if edge.source==machine.state and edge.event and edge.event[1]=='physical_start' and machine.guard(edge.guard)]
                if not edges:raise ContractError('candidate started I/O disabled by the current global DBC state')
                if len(edges)!=1:raise Unknown('ambiguous native I/O projection')
                edge=edges[0]
                operations=[e.action[1] for e in machine.graph.edges if e.source==edge.target and e.action and e.action[0]=='io_success']
                if len(operations)!=1:raise Unknown('no unique typed I/O action at this state')
                wire=io_matches(self.contract,operations[0],self.submitted[event['operation']])
                machine.feed(InputEvent(*edge.event,self.instance,time));self.pending[event['operation']]=(edge.event[0],self.instance,wire)
                self.checked+=1
            elif phase in {'physical_success','physical_failure'}:
                operation,instance,_=self.pending[event['operation']]
                # Actual bytes are captured immediately before the public HIL
                # callback; projection defers read completion to that record.
                if phase=='physical_failure':machine.feed(InputEvent(operation,phase,instance,time))
            elif phase in {'callback_success','return_success'}:
                operation,instance,wire=self.pending.pop(event['operation'])
                machine.feed(InputEvent(operation,'physical_success',instance,time,wire.project(event['data'])))
            elif phase=='callback_failure':
                self.pending.pop(event['operation'],None)
            elif phase=='output' and not event['failed']:
                if machine.sample is None or any(frame.instance!=machine.instance for frame in machine.sample.values()):
                    raise ContractError('candidate published success before a current complete DBC sample committed')
            elif phase=='executor_gap':raise Unknown('native scheduler or frame adapter coverage gap')
        except Unknown as error:self.gaps.append(str(error));self.disabled=True
        except (ContractError,KeyError,TypeError) as error:self.errors.append(str(error));self.disabled=True

    def result(self):
        try:state=self.machine.result()
        except Unknown as error:state={'safety':'unknown','reason':str(error)}
        return {'status':'violated' if self.errors else 'unknown' if self.gaps or state.get('safety')=='unknown' else 'satisfied',
            'checked_io':self.checked,'violations':self.errors,'gaps':self.gaps,'fsm':state,
            'scope':'finite observed I/O/order/waits and commit-before-publication; no numerical-output or native-request terminal proof'}


def parent_path(root,path,alias):
    for component in path.removeprefix(alias+'.').split('.'):
        root=root[component]
    return root


def numeric_point(contract,fp,bp,projection,event,*,derive=False):
    channel=event['channel'];native_name=event.get('native_structure',channel)
    obligations=[item for _,item in walk(bp) if item.name=='NumericEncoding'
        and getattr(item['physical_relation'],'name',None)=='FromDBC' and getattr(item['encoding'],'name',None)=='FromFP'
        and item['encoding']['path'].removeprefix('fp.').startswith('outputs.'+native_name+'.')]
    if not obligations:
        from ..contract.model import node
        deliveries=[item for _,item in walk(bp) if item.name=='DeliverRequestedQuantity'
            and getattr(item['physical_relation'],'name',None)=='FromDBC' and getattr(item['encoding'],'name',None)=='FromFP'
            and item['encoding']['path'].removeprefix('fp.').startswith('outputs.'+native_name+'.')]
        obligations=[node('NumericEncoding',physical_relation=item['physical_relation'],encoding=item['encoding'],rounding=item['rounding'],
            error_bound=node('Unspecified',reason='delivery binding supplies no numeric error bound')) for item in deliveries]
    derived=False;relation_name=None
    if not obligations and derive:
        from ..contract.model import node
        if not any(item.name=='DeriveFromReferencedContractsAndBindings' for _,item in walk(bp)):
            raise Unknown('BP neither declares nor requests derived numeric obligations')
        relation=contract['quantities'][channel];encoding=fp['outputs'][native_name]['encoding']
        relation_name=channel
        obligations=[node('NumericEncoding',rounding=node('Rounding',mode='nearest_ties_even',overflow='report_gap'),error_bound=node('Unspecified'))]
        derived=True
    elif len(obligations)==1:
        relation=parent_path(contract,obligations[0]['physical_relation']['path'],'dbc')
        encoding=parent_path(fp,obligations[0]['encoding']['path'],'fp')
        path=obligations[0]['physical_relation']['path'].removeprefix('dbc.')
        if path.startswith('quantities.'):relation_name=path[len('quantities.'):]
    if len(obligations)!=1:raise UpstreamGap('bp','numeric_obligations','native_channel_mapping','no unique BP numeric obligation for this native channel')
    if relation.name!='PhysicalQuantity' or encoding.name!='OutputEncoding':raise Unknown('typed physical relation and output encoding required')
    obligation=obligations[0];rounding=obligation['rounding']
    if rounding.name!='Rounding':raise Unknown('typed rounding obligation required')
    machine=projection.machine
    if machine.sample is None:raise ContractError('native numeric output has no current committed sample')
    from collections.abc import Mapping
    if not isinstance(machine.sample,Mapping):raise Unknown('numeric comparison requires the actual committed frame snapshot')
    if not machine.sample or any(not isinstance(frame,ValidFrame) or frame.instance!=machine.instance for frame in machine.sample.values()):
        raise ContractError('numeric success has an old or unvalidated committed sample')
    state=getattr(machine,'sample_variables',None)
    environment=frame_environment(machine.sample,machine.instance,machine.variables if state is None else state,declared_frames=contract['frames'])
    validity_environment=frame_environment(machine.sample,machine.instance,machine.variables,declared_frames=contract['frames'])
    try:
        if relation_name in contract['quantities']:
            from ..contract.relations import Relations
            physical=Relations(contract['quantities'],external_names=set(environment)|set(validity_environment)).value(relation_name,environment,
                validity_environment=validity_environment)
        else:
            from ..contract.relations import finite_value
            physical=finite_value(relation,environment,validity_values=validity_environment)
    except DataInvalid as error:
        raise ContractError('native numeric success violates a recovered validity requirement: '+str(error)) from None
    except Unknown as error:
        raise UpstreamGap('dbc','quantities.'+str(relation_name),'executable_physical_relation',str(error)) from None
    except ContractError as error:
        # An undefined/unsupported normative formula is an upstream gap, not
        # evidence that the candidate's numerical result is wrong.
        raise UpstreamGap('dbc','quantities.'+str(relation_name),'executable_physical_relation',
            'current DBC numerical relation cannot be evaluated: '+str(error)) from None
    source=quantity(1,relation['unit']);native=quantity(1,encoding['unit'])
    if source.dimensions!=native.dimensions:raise ContractError('native numeric output uses an incompatible physical unit')
    physical=Fraction(physical)*source.value/native.value;scale=Fraction(encoding['scale']);offset=Fraction(encoding['offset'])
    if not scale:raise ContractError('native numeric encoding has zero scale')
    storage=encoding['storage_type'];details={};encoding_status='satisfied'
    if storage in {'f32','f64'}:
        rounded=round_float((physical-offset)/scale,storage,rounding['mode'],rounding['overflow'])
        if event.get('storage_type')!=storage or 'value_bits' not in event:raise Unknown('exact native float bits/width required')
        actual=float_bits_value(event['value_bits'],storage);expected=rounded['exact_stored']
        details={'expected_bits':rounded['stored_bits'],'observed_bits':event['value_bits'],'derived_obligation':derived}
        if event['value_bits']!=rounded['stored_bits']:
            if not derived:raise ContractError('native float output differs from the explicit BP encoding/rounding choice')
            encoding_status='unknown'  # Intermediate arithmetic/precision bound not specified.
        stored=actual
    else:
        expected=round_integer((physical-offset)/scale,rounding['mode']);stored=expected
        if type(event['value']) is not int or event['value']!=expected:raise ContractError('native numeric output differs from current DBC/FP/BP encoding: expected '+str(expected)+', observed '+str(event['value']))
    error=abs(stored*scale+offset-physical);bound=obligation['error_bound']
    precision='unknown'
    if type(bound) in {int,Fraction}:
        if bound<0 or error>bound*source.value/native.value:raise ContractError('native numeric output exceeds declared physical error bound')
        precision='satisfied'
    return {'channel':channel,'encoding_value_status':encoding_status,'stored':str(stored) if storage in {'f32','f64'} else stored,'physical_error':str(error),**details,
        'precision_requirement_status':precision,'scope':'this committed frame/state snapshot, encoded validity and formula/scale/rounding only; no overflow-domain or universal precision proof'}


def evaluate(contract,fp,plan,stdout,bp=None):
    actual=split_cases(stdout);expected={item['case'] for item in plan['cases']}
    if set(actual)!=expected:raise ContractError('native execution did not cover the complete configured case set')
    apis=[value for _,value in walk(fp) if value.name=='AsyncI2CContract']
    reports=[];upstream=[]
    for case,events in actual.items():
        projection=Projection(contract);monitor=NativeMonitor(apis[0]) if len(apis)==1 else None;resource_errors=[];numbers=[];numeric_errors=[];numeric_gaps=[]
        for event in events:
            projection.feed(event)
            if event['phase']=='output' and not event['failed']:
                try:
                    if bp is None:raise Unknown('native numeric check requires current BP')
                    numbers.append(numeric_point(contract,fp,bp,projection,event))
                except UpstreamGap as error:
                    numeric_gaps.append(str(error));upstream.append(error.finding)
                except Unknown as error:numeric_gaps.append(str(error))
                except KeyError as error:numeric_gaps.append('native channel/encoding declaration missing: '+str(error))
                except ContractError as error:numeric_errors.append(str(error))
            if monitor and event['phase'] in {'submit','accepted','rejected','callback_success','callback_failure'}:
                try:monitor.feed(operation='i2c',instance=str(event['operation']),phase=event['phase'],
                    buffer=str(event['buffer']) if 'buffer' in event else None,
                    returned_buffer=str(event['returned_buffer']) if 'returned_buffer' in event else None)
                except ContractError as error:resource_errors.append(str(error))
        resources=monitor.result() if monitor else {'status':'unknown','gaps':['no unique typed async I2C contract']}
        if resource_errors:resources.update(status='violated',violations=resource_errors)
        numeric={'status':'violated' if numeric_errors else 'unknown' if not numbers or numeric_gaps or any(n['precision_requirement_status']=='unknown' for n in numbers) else 'satisfied',
            'points':numbers,'violations':numeric_errors,'gaps':numeric_gaps,'scope':'observed successful outputs only; absent output is checked separately'}
        reports.append({'case':case,'event_count':len(events),'contract_projection':projection.result(),'resources':resources,'numeric':numeric})
    status='violated' if any(r[key]['status']=='violated' for r in reports for key in ('contract_projection','resources','numeric')) else 'unknown'
    return {'schema':'autodriver.native-check/v1','status':status,'cases':reports,'upstream_findings':unique_findings(upstream),
        'scope':plan['scope'],'unimplemented':['native accepted request terminal coverage','unsupported global failure/recovery primitives','universal numerical/overflow proof','all paths and physical provider validation']}


def unique_findings(findings):
    """Keep typed checker findings; do not infer a root from arbitrary text."""
    return list({json.dumps(item,sort_keys=True):item for item in findings}.values())
