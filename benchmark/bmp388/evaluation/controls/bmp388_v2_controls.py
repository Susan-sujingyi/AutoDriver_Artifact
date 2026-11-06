"""Host-only BMP388 v2 legal freedoms, observable faults and model boundaries."""
from bmp388_controls import once, legal_controls as old_legal, fault_controls as old_fault, unsynchronized_split, shared_epoch_cache


def synchronized_split(source):
    s=unsynchronized_split(source)
    s=once(s,'enum State { SplitTemperature,','enum State { DiscardBeforeRequest, SplitTemperature,')
    s=once(s,'let result=self.read(buffer,0x02,1,State::Error);','let result=self.read(buffer,0x04,6,State::DiscardBeforeRequest);')
    return once(s,'            State::CommandReady=>{','            State::DiscardBeforeRequest=>self.read(buffer,0x02,1,State::Error),\n            State::CommandReady=>{')


def legal_controls(source):
    result=old_legal(source);old=old_fault(source)
    wait=once(source,'let result=self.read(buffer,0x02,1,State::Error);','self.wait(buffer,State::Wait,150_000);let result=Ok(());')
    wait=once(wait,'if buffer[0]&mask!=0 {','if true {')
    result.update({
        'float32_polynomial':source.replace('f64','f32'),
        'round_nearest':source.replace('(t*100.0) as i64','(t*100.0+if t>=0.0{0.5}else{-0.5}) as i64').replace('(p/100.0) as i64','(p/100.0+0.5) as i64'),
        'synchronized_separate_data':synchronized_split(source),
        'fixed_150ms_wait_without_drdy':wait,
        'known_device_no_identity_probe':once(source,'State::ResetWait=>self.read(buffer,0x00,1,State::Identity),','State::ResetWait=>self.read(buffer,0x31,21,State::Calibration),'),
        'optional_identity_validation':old['ignore_identity'],
        'retain_valid_cache_after_bus_error':old['cache_survives_known_error'],
        'no_forced_error_callback':old['omit_accepted_error_callback'],
        'no_forced_stall_timeout':old['unbounded_polling'],
        'private_constant_unit_startup':once(once(source,'pub const BUFFER_SIZE:','const BUFFER_SIZE:'),
            '    pub fn startup(&self)->Result<(),ErrorCode> {','    pub fn startup(&self) {let _=self.startup_inner();}\n    fn startup_inner(&self)->Result<(),ErrorCode> {'),
    })
    return result


def fault_controls(source):
    old=old_fault(source)
    names=('signed_unsigned_t1','unsigned_signed_p1','unsigned_signed_t3','omit_p2_bias','wrong_p5_scale',
        'wrong_p11_exponent','temperature_raw_big_endian','signed_temperature_adc','temperature_wrong_units',
        'pressure_wrong_units','pressure_ignores_temperature','pressure_without_temperature_bytes','skip_data_ready',
        'wrong_mode_forced','wrong_pressure_osr','wrong_odr','enable_iir',
        'duplicate_temperature_callback','torn_separate_data_transactions','incorrect_auto_increment_write')
    result={k:(old[k],'fail') for k in names}
    result['permanent_other_channel_cache']=(once(shared_epoch_cache(source),'self.cached.take().unwrap()','self.cached.get().unwrap()'),'not_completed')
    result['missing_temperature_callback']=(once(source,'c.callback(value.map(|v|v as i32));','let _=(c,value);'),'not_completed')
    result['wrong_pressure_channel']=(once(source,'Some(true)=>{if let Some(c)=self.pressure.get(){c.callback(value.map(|v|v as u32));}}',
        'Some(true)=>{if let Some(c)=self.temperature.get(){c.callback(value.map(|v|v as i32));}}'),'fail')
    return result


def unsupported_controls(source):
    old=old_fault(source)
    return {'earlier_than_reset_model':old['skip_reset_wait'],'reserved_power_bit':old['reserved_power_bit']}
