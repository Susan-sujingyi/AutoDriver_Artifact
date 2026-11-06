"""Finite public-ABI fault probes, independent of a device-prefix witness.

No successful measurement bytes, device commands or numeric oracle are supplied.
These observations cannot validate the device protocol or primary contracts.
"""
import json
import re
from pathlib import Path

from ..contract.model import ContractError, Unknown, parse, walk
from ..contract.native import NativeMonitor
from ..document_projection import executable_view
from .plan import split_cases


SCOPE = ('finite public native ABI and injected transport errors only; device FSM, '
         'successful measurement, numerical correctness, provider/hardware and liveness not checked')


def profile(state):
    item = state['objects'].get('target_fp')
    if item is None:
        return None
    material = next(x for x in state['materials'] if x['id']==item['material_id'])
    text = material['text']
    view_id=state.get('derived_views',{}).get('target_fp')
    if view_id is not None:
        view_material=next(x for x in state['materials'] if x['id']==view_id)
        view=json.loads(view_material['text'])
        if (view.get('schema')!='autodriver.derived-executable-view/v1' or view.get('object')!='target_fp'
                or view.get('primary_material_id')!=material['id'] or view.get('primary_material_sha256')!=material['sha256']
                or view.get('normative') is not False or material['id'] not in view_material['parents']):
            raise ContractError('framework probe view does not bind its readonly primary FP')
        if view.get('content') is not None:text=view['content']
    try:
        value = json.loads(text)
    except ValueError:
        value = None
    if isinstance(value,dict) and value.get('schema')=='autodriver.semantic-document/v1':
        text = value['raw_text']
    content, _ = executable_view(text, 'fp')
    return parse(content,'fp') if content is not None else None


def tock_plan(task, candidate):
    from .channels import scalar_channels, render_clients
    interface = task['target']['interface']
    spi = bool(task['requirements']['connection'].get('spi'))
    signature = re.sub(r'\s+','',interface['constructor'])
    expected = ("pubfnnew(spi:&'aS,alarm:&'aA,tx:&'staticmut[u8],rx:&'staticmut[u8])->Self" if spi else
                "pubfnnew(i2c:&'aI,alarm:&'aA,buffer:&'staticmut[u8])->Self")
    if signature != expected:
        raise Unknown('public framework fault probe does not support this constructor')
    name = interface['type'].split('<',1)[0]
    module = task['target']['module']
    if not all(re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*',x) for x in (name,module)):
        raise ContractError('invalid public driver identifier')
    size = interface.get('buffer_bytes')
    if type(size) is not int or not 1<=size<=65536:
        raise Unknown('bounded public native buffer configuration required')
    channels = scalar_channels(task)
    clients = render_clients(channels)
    base = Path(__file__).parent
    source = (base/('tock_spi_alarm_main.rs' if spi else 'tock_i2c_alarm_main.rs')).read_text()
    start, end = source.index('fn pump('), source.index('fn main()')
    if spi:
        pump = '''fn pump(clock:&Rc<RefCell<State>>,bus:&Bus,alarm:&Alarm,_steps:&[&[u8]],_fail:bool){
 for _ in 0..128 {
  let io=bus.next_due(); let timer=clock.borrow().alarm_deadline();
  if io.is_some() && (timer.is_none() || io<=timer) {bus.complete(&[],true);}
  else if timer.is_some(){alarm.fire();}else{return;}
 }
 clock.borrow_mut().record("executor_gap","\\\"reason\\\":\\\"framework fault scheduler bound\\\"".into());
}
'''
    else:
        pump = '''fn pump(clock:&Rc<RefCell<State>>,bus:&Bus,alarm:&Alarm,driver:&impl kernel::hil::i2c::I2CClient,_steps:&[&[u8]],_fail:bool){
 for _ in 0..128 {
  let next=clock.borrow().next();
  match next {Some("io")=>bus.complete(driver,&[],true),Some("alarm")=>alarm.fire(),None=>return,_=>unreachable!()}
 }
 clock.borrow_mut().record("executor_gap","\\\"reason\\\":\\\"framework fault scheduler bound\\\"".into());
}
'''
    source = source[:start]+pump+source[end:]
    cases = [dict(case='framework_transport_failure:'+c, requested_channel=c) for c in channels]
    rows = [ '('+json.dumps(c['case'])+','+json.dumps(c['requested_channel'])+',&[],true,false,false)' for c in cases ]
    driver = (Path(candidate)/'target/capsules/extra/src'/module).with_suffix('.rs').resolve(strict=True)
    if not driver.is_relative_to((Path(candidate)/'target').resolve()):
        raise ContractError('public framework probe candidate escaped target')
    for marker, value in {'"@DRIVER_PATH@"':json.dumps(str(driver)), '@DRIVER_TYPE@':name,
            '@BUFFER_SIZE@':str(size), '@CASES@':','.join(rows), '@INITIAL_STEPS@':'',
            '@CLIENT_IMPLEMENTATIONS@':clients['implementations'], '@SET_CLIENTS@':clients['set_clients'],
            '@REQUEST_MATCH@':clients['request_match']}.items():
        source = source.replace(marker,value)
    if '@' in source:
        raise ContractError('unfilled public framework probe marker')
    return dict(schema='autodriver.framework-probe-plan/v1',source=source,
        observer=(base/'tock_i2c_mock.rs').read_text(), cases=cases, native_channels=channels,bus='spi' if spi else 'i2c',
        auxiliary_sources={'spi_observer.rs':(base/'tock_spi_mock.rs').read_text()} if spi else {},
        scope=SCOPE, contract_projection='not_checked', stimulus='all accepted native bus operations complete with an injected error')


def nuttx_plan(task, candidate):
    from .nuttx_headers import project, mock_headers
    from .nuttx_channels import scalar_channels, SCALARS, render
    if task['target']['interface'].get('framework')!='new_sensor' or not task['requirements']['connection'].get('i2c'):
        raise Unknown('public framework probe supports the declared New Sensor I2C ABI')
    module = task['target']['module']
    if not re.fullmatch(r'[a-z_][a-z_0-9]*',module):
        raise ContractError('invalid public native module')
    channels = scalar_channels(task)
    headers, audit = project(Path(candidate)/'target',sensor_structures={SCALARS[c][0] for c in channels})
    headers.update(mock_headers(module))
    headers['nuttx/sensors/'+module+'.h']=(Path(candidate)/'target/include/nuttx/sensors'/ (module+'.h')).read_text()
    cases = [dict(case='framework_read_failure:'+c,channel=c,probe=0,registration_failure=0) for c in channels]
    cases += [dict(case='framework_registration_failure:'+str(i),channel=channels[0],probe=0,registration_failure=i)
              for i in range(1,len(channels)+1)]
    rows = ['{'+','.join([json.dumps(c['case']),json.dumps(c['channel']),'NULL','0','NULL','0','0',
             str(c['registration_failure']),'0','1'])+'}' for c in cases]
    source=(Path(__file__).parent/'nuttx_i2c_fetch_main.c').read_text()
    for marker,value in {'@MODULE@':module,'@REGISTER@':module+'_register',
            '@ADDRESS@':str(task['requirements']['connection']['i2c']['address_7bit']),
            '@CASES@':','.join(rows),'@CHANNELS@':render(channels),'@FRAMEWORK_PROBE@':'true'}.items():
        source=source.replace(marker,value)
    if '@' in source:
        raise ContractError('unfilled public native probe marker')
    return dict(schema='autodriver.framework-probe-plan/v1',source=source,headers=headers,
        cases=cases,native_channels=channels,abi_projection=audit,scope=SCOPE,
        contract_projection='not_checked',stimulus='successful native writes and failed native reads; no device response bytes')


def evaluate(contract, fp, plan, stdout, bp=None):
    actual=split_cases(stdout)
    if set(actual)!={c['case'] for c in plan['cases']}:
        raise ContractError('public native fault probe case set incomplete')
    apis=[n for _,n in walk(fp) if n.name=='AsyncI2CContract'] if fp is not None and plan.get('bus','i2c')=='i2c' else []
    api=apis[0] if len(apis)==1 else None
    requires_enable=api is not None and any(n.name=='DeviceEnabled' for _,n in walk(api['before_io']))
    reports=[]
    for name,events in actual.items():
        violations=[];gaps=[];enabled=False
        monitor=NativeMonitor(api) if api is not None else None
        for event in events:
            phase=event['phase']
            if phase=='enable':enabled=True
            elif phase=='disable':enabled=False
            elif phase=='submit' and requires_enable and not enabled:
                violations.append('native I/O submitted before the FP DeviceEnabled precondition was established on this instance')
            if phase=='registration_rejected' and event.get('reason') in {'duplicate_type','invalid_type'}:
                violations.append('native registration rejected candidate metadata: '+event['reason'])
            if phase=='native_requirement_failure':
                violations.append(event['reason'])
            if phase=='registration_failure_state' and (event.get('active_nodes') or event.get('allocations')):
                violations.append('failed native registration retained observed nodes or candidate-owned allocations')
            if phase=='executor_gap':gaps.append(event.get('reason','public native probe gap'))
            if monitor is not None and phase in {'submit','accepted','rejected','callback_success','callback_failure'}:
                try:monitor.feed(operation=str(event['operation']),instance=name,phase=phase,
                    buffer=str(event['buffer']) if 'buffer' in event else None,
                    returned_buffer=str(event['returned_buffer']) if 'returned_buffer' in event else None)
                except (ContractError,Unknown) as error:violations.append(str(error))
        resources=monitor.result() if monitor is not None else dict(status='unknown',scope='no executable FP resource protocol')
        reports.append(dict(case=name,event_count=len(events),violations=violations,gaps=gaps,resources=resources))
    return dict(schema='autodriver.native-check/v1',status='violated' if any(c['violations'] for c in reports) else 'unknown',
        cases=reports,scope=SCOPE,device_contract_status='not_checked',
        unimplemented=['device FSM and response validation','successful sample and numeric behavior','all-path resource/progress proof'])
