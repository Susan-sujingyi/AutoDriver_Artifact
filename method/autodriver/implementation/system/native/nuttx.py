"""Finite NuttX public New Sensor I2C fetch adapter, independent of scoring."""
from fractions import Fraction
import json
from pathlib import Path
import re

from ..contract.model import Unknown,ContractError,walk,identity
from ..contract.stimuli import frame_cases
from ..contract.expression import seconds
from .nuttx_headers import project,mock_headers
from .plan import Projection,split_cases,numeric_point,unique_findings
from ..contract.model import UpstreamGap
from .prefix import success_prefix,wire_response,frame_names
from .nuttx_channels import scalar_channels,SCALARS,render


def native_plan(task,contract,candidate):
    candidate=Path(candidate);interface=task['target']['interface'];module=task['target']['module']
    if task['target']['os']!='nuttx' or interface.get('framework')!='new_sensor' or not task['requirements']['connection'].get('i2c'):
        raise Unknown('NuttX executor currently supports the declared New Sensor I2C ABI')
    if not re.fullmatch('[a-z_][a-z0-9_]*',module):raise ContractError('invalid native public module identifier')
    channels=scalar_channels(task)
    prefix=success_prefix(contract,'i2c')
    initial=[step for step in prefix['steps'] if step['phase']=='initialization']
    acquire=[step for step in prefix['steps'] if step['phase']=='acquisition']
    names={frame for step in acquire for frame in frame_names(step['operation'])}
    stimuli=[item for item in frame_cases(contract)['cases'] if item['frame'] in names]
    if not stimuli:raise Unknown('native NuttX adapter has no current acquisition frame stimuli')
    headers,audit=project(candidate/'target',sensor_structures={SCALARS[channel][0] for channel in channels});headers.update(mock_headers(module))
    public_header='include/nuttx/sensors/'+module+'.h'
    headers['nuttx/sensors/'+module+'.h']=(candidate/'target'/public_header).read_text()
    cases=[];first=next((case for case in stimuli if case['case']=='middle' and case['expected_valid']),next(case for case in stimuli if case['expected_valid']))
    for channel in channels:
        for item in stimuli:cases.append(dict(item,case=item['frame']+':'+item['case']+':'+channel,channel=channel,bus_failure=0,registration_failure=0,probe=0))
        probes=[(0,'io_failure'),(1,'disabled_fetch'),(2,'null_buffer'),(3,'short_buffer'),(5,'interrupted_sleep')]
        if len(channels)>1:probes.append((4,'other_node_disabled'))
        for probe,name in probes:
            cases.append(dict(first,case=name+':'+channel,channel=channel,bus_failure=int(probe==0),registration_failure=0,probe=probe))
    for position in range(1,len(channels)+1):
        cases.append(dict(first,case='registration_failure:'+str(position),channel=channels[0],bus_failure=0,registration_failure=position,probe=0))
    def responses(steps,frames):
        rows=[wire_response(step,frames) if step['wire'].read_length else [] for step in steps]
        # ISO C permits no empty compound array; the zero-length dummy is
        # unreachable because the separate count remains zero.
        return '(const struct response[]){'+(','.join('{(const uint8_t[]){'+(','.join(map(str,data)) or '0')+'},'+str(len(data))+'}' for data in rows) or '{NULL,0}')+'}'
    initial_data=responses(initial,prefix['frames']);rows=[]
    for item in cases:
        frames={**prefix['frames'],item['frame']:bytes(item['data'])}
        rows.append('{'+','.join([json.dumps(item['case']),json.dumps(item['channel']),initial_data,str(len(initial)),responses(acquire,frames),str(len(acquire)),
            str(item['bus_failure']),str(item['registration_failure']),str(item['probe']),
            str(2 if item['expected_valid'] and not(item['bus_failure'] or item['probe'] or item['registration_failure']) else 1)])+'}')
    code=(Path(__file__).parent/'nuttx_i2c_fetch_main.c').read_text().replace('@MODULE@',module).replace('@REGISTER@',module+'_register')
    code=code.replace('@ADDRESS@',str(task['requirements']['connection']['i2c']['address_7bit'])).replace('@CASES@',','.join(rows)).replace('@CHANNELS@',render(channels)).replace('@FRAMEWORK_PROBE@','false')
    if '@' in code:raise ContractError('unfilled native C plan marker')
    if len(code.encode())>2_000_000:raise Unknown('complete native NuttX plan exceeds source bound')
    return {'schema':'autodriver.native-plan/v1','contract_sha256':identity(contract),'cases':cases,'source':code,'headers':headers,'abi_projection':audit,
        'native_channels':channels,'prefix':{key:prefix[key] for key in ('predicate','witness','scope')},
        'scope':'actual candidate, public extracted NuttX scalar ABI, explicit service/clock mocks and one graph-derived input prefix; finite observations only, no physical hardware, liveness, all-path or independent-scoring claim'}


def evaluate(contract,fp,bp,plan,stdout):
    actual=split_cases(stdout);configured={case['case']:case for case in plan['cases']}
    if set(actual)!=set(configured):raise ContractError('native NuttX case set is incomplete')
    reports=[];upstream=[]
    for name,events in actual.items():
        case=configured[name];projection=Projection(contract);violations=[];gaps=[];numbers=[];fetches=[];last_physical_end=0;requests={};locks=0;interrupted=False
        for event in events:
            projection.feed(event);phase=event['phase']
            if phase=='submit':requests[event['operation']]=True
            elif phase in {'return_success','return_failure'}:requests.pop(event['operation'],None)
            elif phase=='physical_success':last_physical_end=event['tick']
            elif phase=='mutex_lock':locks+=1
            elif phase=='mutex_unlock':locks-=1
            elif phase=='sleep_interrupted':interrupted=True
            elif phase=='executor_gap':gaps.append(event.get('reason','native adapter gap'))
            elif phase=='native_requirement_failure':violations.append(event['reason'])
            elif phase=='registration_failure_state':
                if event['active_nodes'] or event['allocations']:violations.append('failed registration retained nodes or candidate-owned allocations')
            elif phase=='native_return' and event['request']=='register':
                if case['registration_failure'] and event['result']>=0:violations.append('registration reported success after an injected native registration failure')
            elif phase=='native_return' and event['request']=='fetch':
                fetches.append(event)
                if event['result']>0 and event['result']!=event['bytes']:violations.append('successful native fetch returned an incomplete event size')
                if case['probe'] in {1,2,3} and event['result']>=0:violations.append('disabled or invalid-buffer fetch did not return a negative errno')
                if case['probe']==4 and event['result']<0:violations.append('disabling another native node prevented a still-enabled node from fetching')
                if (case['bus_failure'] or not case['expected_valid']) and event['result']>=0:violations.append('failed I2C or invalid complete frame became a successful fetch')
            elif phase=='output':
                if event['timestamp']<last_physical_end:violations.append('native timestamp predates completion of its acquisition')
                try:numbers.append(numeric_point(contract,fp,bp,projection,event,derive=True))
                except UpstreamGap as error:
                    gaps.append(str(error));upstream.append(error.finding)
                except Unknown as error:gaps.append(str(error))
                except KeyError as error:gaps.append('native numeric declaration/path missing: '+str(error))
                except ContractError as error:violations.append(str(error))
        if requests or locks:violations.append('observed native service request or mutex was left pending')
        if case['probe']==5:
            policy=any(item.name=='BindSyncDelay' and item['on_interruption']=='fail_current_operation' for _,item in walk(bp)) if bp is not None else False
            if interrupted and policy and any(event['result']>=0 for event in fetches):violations.append('native fetch continued successfully after a delay interruption despite its BP failure binding')
            if not interrupted:gaps.append('candidate did not invoke the interruptible sleep service in this finite probe')
            elif not policy:gaps.append('sleep interruption observed; no typed current BP interruption policy was available')
        bounds=[]
        for _,item in walk(contract):
            if item.name=='Deadline':
                try:
                    anchor=item['anchor']
                    if getattr(anchor,'name',None)=='EventRef' and anchor['operation']=='acquire' and anchor['phase']=='logical_success':bounds.append(seconds(item['maximum']))
                except (Unknown,ContractError,TypeError,KeyError):pass
        # Only an executable recovered deadline can supply a numeric bound;
        # no hand-written device/task timing constant is added by this adapter.
        if not bounds:gaps.append('no executable recovered per-fetch deadline for numeric bound checking')
        else:
            for event in fetches:
                if Fraction(event['elapsed_us'],1000000)>min(bounds):violations.append('native fetch exceeded a recovered deadline')
        p=projection.result();violations+=p['violations']
        reports.append({'case':name,'event_count':len(events),'contract_projection':p,'numeric_points':numbers,'violations':violations,'gaps':gaps,
            'observed_fetch_returns':[{'result':event['result'],'elapsed_us':event['elapsed_us'],'probe_mode':event['probe_mode']} for event in fetches],
            'native_requests_pending':len(requests),'mutex_balance':locks})
    return {'schema':'autodriver.native-check/v1','status':'violated' if any(case['violations'] for case in reports) else 'unknown','cases':reports,
        'upstream_findings':unique_findings(upstream),
        'scope':plan['scope'],'unimplemented':['provider physical guarantees','full concurrent request interleavings','universal precision/overflow domain','unsupported failure/recovery binding semantics']}
