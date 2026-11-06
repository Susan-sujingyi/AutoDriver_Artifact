"""Scalar public Tock HIL signatures, not device protocols or formulas."""
from ..contract.model import Unknown,ContractError


SCALARS={
    'temperature':('TemperatureDriver','TemperatureClient','read_temperature','Result<i32,ErrorCode>'),
    'humidity':('HumidityDriver','HumidityClient','read_humidity','usize'),
    'pressure':('PressureDriver','PressureClient','read_atmospheric_pressure','Result<u32,ErrorCode>'),
    'ambient_light':('AmbientLight','AmbientLightClient','read_light_intensity','usize'),
    'proximity':('ProximityDriver','ProximityClient','read_proximity','u8'),
    'co2':('AirQualityDriver','AirQualityClient','read_co2','Result<u32,ErrorCode>'),
    'tvoc':('AirQualityDriver','AirQualityClient','read_tvoc','Result<u32,ErrorCode>'),
}
ALIASES={'atmospheric_pressure':'pressure','equivalent_co2':'co2','equivalent_tvoc':'tvoc'}


def scalar_channels(task):
    requested=task['requirements']['functions']['channels']
    if not isinstance(requested,list) or not requested:raise ContractError('nonempty public channel list required')
    channels=[ALIASES.get(name,name) for name in requested]
    if len(set(channels))!=len(channels):raise Unknown('multiple task channels alias one native interface; explicit mapping required')
    traits=set(task['target']['interface'].get('traits',[]))
    for channel in channels:
        if channel not in SCALARS:raise Unknown('native scalar adapter does not implement the required channel: '+str(channel))
        if 'kernel::hil::sensors::'+SCALARS[channel][0] not in traits:raise Unknown('required scalar channel has no declared public trait: '+channel)
    return channels


def render_clients(channels):
    namespace='kernel::hil::sensors::';implementations=[];clients=[];arms=[];seen=set()
    def output(channel,datatype,method='callback'):
        result=datatype.startswith('Result<')
        value='value.ok().map(|n|n as i128)' if result else 'Some(value as i128)'
        failed='value.is_err()' if result else 'false'
        return ('fn '+method+'(&self,value:'+datatype+'){self.clock.borrow_mut().record_output("'+channel+'",'+value+','+failed+');}')
    for channel in channels:
        driver,client,method,datatype=SCALARS[channel]
        arms.append('"'+channel+'" => '+namespace+driver+'::'+method+'(driver)')
        if client in seen:continue
        seen.add(client);clients.append(namespace+driver+'::set_client(driver,client);')
        if client=='AirQualityClient':
            body=('fn environment_specified(&self,result:Result<(),ErrorCode>){self.clock.borrow_mut().record("native_environment",format!(r#""failed":{}"#,result.is_err()));}'
                  +output('co2','Result<u32,ErrorCode>','co2_data_available')+output('tvoc','Result<u32,ErrorCode>','tvoc_data_available'))
        else:body=output(channel,datatype)
        implementations.append('impl '+namespace+client+' for Client{'+body+'}')
    return {'implementations':'\n'.join(implementations),'set_clients':'\n'.join(clients),
            'request_match':'match *channel {'+','.join(arms)+',_=>Err(ErrorCode::NOSUPPORT)}'}
