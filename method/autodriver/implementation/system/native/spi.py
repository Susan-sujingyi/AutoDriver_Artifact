"""Public Tock SPI/Alarm temperature adapter, with contract-derived stimuli."""
from pathlib import Path
import json
import re

from ..contract.model import Unknown,ContractError,walk,identity
from ..contract.stimuli import frame_cases
from ..contract.resource_set import BufferSetMonitor
from .prefix import success_prefix,wire_response,frame_names
from .plan import Projection,split_cases,numeric_point,unique_findings
from ..contract.model import UpstreamGap
from .channels import scalar_channels,render_clients


def native_plan(task,contract,candidate,bp=None,fp=None):
    interface=task['target']['interface'];traits=set(interface.get('traits',[]))
    required={'kernel::hil::spi::SpiMasterClient','kernel::hil::time::AlarmClient'}
    if task['target']['os']!='tock' or not required<=traits:
        raise Unknown('SPI native adapter requires the declared public Tock SPI/Alarm ABI')
    channels=scalar_channels(task);clients=render_clients(channels)
    signature=re.sub(r'\s+','',interface['constructor'])
    if signature!="pubfnnew(spi:&'aS,alarm:&'aA,tx:&'staticmut[u8],rx:&'staticmut[u8])->Self":raise Unknown('unsupported public SPI constructor signature')
    name=interface['type'].split('<',1)[0];module=task['target']['module'];size=interface.get('buffer_bytes')
    if not all(re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*',s) for s in [name,module]) or type(size) is not int or not 1<=size<=65536:raise ContractError('invalid public SPI type/module/buffer configuration')
    driver=(Path(candidate)/'target/capsules/extra/src'/module).with_suffix('.rs').resolve(strict=True)
    if not driver.is_relative_to(Path(candidate).resolve()):raise ContractError('SPI candidate path escaped prepared copy')
    prefix=success_prefix(contract,'spi');initial=[s for s in prefix['steps'] if s['phase']=='initialization'];acquire=[s for s in prefix['steps'] if s['phase']=='acquisition']
    if any(len(s['wire'].write)>size or s['wire'].read_length>size for s in prefix['steps']):raise Unknown('contract transaction exceeds declared native buffer capacity')
    names={frame for step in acquire for frame in frame_names(step['operation'])}
    stimuli=[item for item in frame_cases(contract)['cases'] if item['frame'] in names]
    if not stimuli:raise Unknown('no acquisition frame boundary stimuli')
    first=next((s for s in stimuli if s['case']=='middle' and s['expected_valid']),next(s for s in stimuli if s['expected_valid']))
    cases=[dict(s,case=s['frame']+':'+s['case'],async_failure=False,immediate_rejection=False,reentry=False) for s in stimuli]
    numeric_gaps=[]
    if bp is not None and fp is not None:
        from .numeric_stimuli import integer_ties
        extra=integer_ties(contract,fp,bp,names);numeric_gaps=extra['gaps']
        for item in extra['cases']:
            cases.append(dict(first,case=item['case'],frame_overrides=item['frames'],rounding_predicate=item['predicate'],
                async_failure=False,immediate_rejection=False,reentry=False))
    for mode in ['async_failure','immediate_rejection','reentry']:
        cases.append(dict(first,case=mode,**{key:key==mode for key in ['async_failure','immediate_rejection','reentry']}))
    cases=[dict(item,case=item['case']+':'+channel,requested_channel=channel) for item in cases for channel in channels]
    initial_data=[wire_response(step,prefix['frames']) for step in initial]
    def rust_steps(rows):return ','.join('&['+','.join(map(str,row))+']' for row in rows)
    rendered=[]
    for case in cases:
        frames={**prefix['frames'],case['frame']:bytes(case['data']),**case.get('frame_overrides',{})};data=[wire_response(step,frames) for step in acquire]
        if 'frame_overrides' in case:case['frame_overrides']={name:list(data) for name,data in case['frame_overrides'].items()}
        rendered.append('('+json.dumps(case['case'])+','+json.dumps(case['requested_channel'])+',&['+rust_steps(data)+'],'+','.join(str(case[k]).lower() for k in ['async_failure','immediate_rejection','reentry'])+')')
    source=(Path(__file__).parent/'tock_spi_alarm_main.rs').read_text().replace('"@DRIVER_PATH@"',json.dumps(str(driver),ensure_ascii=False)).replace('@DRIVER_TYPE@',name)
    source=source.replace('@BUFFER_SIZE@',str(size)).replace('@INITIAL_STEPS@',rust_steps(initial_data)).replace('@CASES@',','.join(rendered))
    for marker,value in {'CLIENT_IMPLEMENTATIONS':clients['implementations'],'SET_CLIENTS':clients['set_clients'],'REQUEST_MATCH':clients['request_match']}.items():
        source=source.replace('@'+marker+'@',value)
    if '@' in source:raise ContractError('unfilled SPI native adapter placeholder')
    if len(source.encode())>2_000_000:raise Unknown('complete SPI native source exceeds bounded plan size')
    return {'schema':'autodriver.native-plan/v1','cases':cases,'source':source,'observer':(Path(__file__).parent/'tock_i2c_mock.rs').read_text(),
        'auxiliary_sources':{'spi_observer.rs':(Path(__file__).parent/'tock_spi_mock.rs').read_text()},'contract_sha256':identity(contract),
        'configuration':task['requirements']['connection']['spi'],'prefix':{'predicate':prefix['predicate'],'witness':prefix['witness'],'scope':prefix['scope']},
        'numeric_stimulus_gaps':numeric_gaps,
        'native_channels':channels,'environment':{'clock_hz':32768,'io_latency_ticks':1,'scheduler_step_bound':128,'virtual_cs':True},
        'scope':'actual candidate with public SPI/Alarm HIL mocks and one contract-derived guarded prefix; no physical provider, full global terminal/liveness, all-path or independent-scoring claim'}


def evaluate(contract,fp,plan,stdout,bp):
    actual=split_cases(stdout)
    if set(actual)!={c['case'] for c in plan['cases']}:raise ContractError('incomplete configured SPI case set')
    apis=[n for _,n in walk(fp) if n.name=='APIContract' and n['symbol'].split('::')[-1]=='read_write_bytes']
    reports=[];upstream=[];config=plan['configuration']
    for name,events in actual.items():
        projection=Projection(contract);monitor=BufferSetMonitor(apis[0]) if len(apis)==1 else None
        violations=[];gaps=[];numbers=[];selected=None;returns=[];outputs=[]
        for event in events:
            projection.feed(event);phase=event['phase']
            if phase=='submit':
                if event['mode']!=config['mode'] or not 0<event['rate']<=config['max_frequency_hz']:violations.append('observed SPI mode/rate violates frozen connection configuration')
            if phase=='cs_assert':
                if selected is not None:violations.append('overlapping virtual chip-select transactions')
                selected=event['operation']
            elif phase=='cs_release':
                if selected!=event['operation']:violations.append('virtual chip-select release belongs to a different transaction')
                selected=None
            if monitor and phase in {'submit','accepted','rejected','callback_success','callback_failure'}:
                resources={slot:str(event[key]) for slot,key in [('write','write_buffer'),('read','read_buffer')] if event.get(key) is not None}
                try:monitor.feed(operation='spi',instance=str(event['operation']),phase=phase,
                    **({'buffers':resources} if phase=='submit' else {'returned_buffers':resources} if phase in {'rejected','callback_success','callback_failure'} else {}))
                except Unknown as error:gaps.append(str(error))
                except ContractError as error:violations.append(str(error))
            if phase=='native_return' and event['request']!='startup':returns.append(event)
            if phase=='output':
                outputs.append(event)
                if not event['failed']:
                    try:numbers.append(numeric_point(contract,fp,bp,projection,event))
                    except UpstreamGap as error:
                        gaps.append(str(error));upstream.append(error.finding)
                    except Unknown as error:gaps.append(str(error))
                    except KeyError as error:gaps.append('numeric declaration/path missing: '+str(error))
                    except ContractError as error:violations.append(str(error))
        if selected is not None:gaps.append('virtual SPI transaction did not finish')
        resources=monitor.result() if monitor else {'status':'unknown','reason':'no unique recovered SPI API'}
        projected=projection.result();violations.extend(projected['violations'])
        reports.append({'case':name,'event_count':len(events),'contract_projection':projected,'resources':resources,'numeric_points':numbers,
            'violations':violations,'gaps':gaps,'native_returns':returns,'outputs':outputs,
            'request_terminal_status':'unknown: exactly-once and rejection/callback premises are not encoded by this FP/BP'})
    return {'schema':'autodriver.native-check/v1','status':'violated' if any(r['violations'] for r in reports) else 'unknown','cases':reports,
        'upstream_findings':unique_findings(upstream),
        'scope':plan['scope'],'unimplemented':['physical CS/wiring/bit-order/provider guarantees','unsupported global loop/terminal predicates','native request terminal guarantees','universal numerical/overflow proof','all paths and liveness']}
