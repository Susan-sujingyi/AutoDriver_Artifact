"""DPS310 v2 host controls; audit-only cases are not proved physically correct."""
from dps310_controls import once, cached, legal_controls as old_legal, fault_controls as old_fault


def legal_controls(source):
    old=old_fault(source);result=old_legal(source)
    result.update({
        'float32_arithmetic':source.replace('f64','f32'),
        'two_second_temperature_cache':once(cached(source),'<A::Frequency::frequency()','<2*A::Frequency::frequency()'),
        'retain_valid_temperature_after_error':old['cache_survives_failed_temperature'],
        'optional_identity_validation':old['ignore_identity'],
        'coefficient_ready_implies_normal_sensor_ready':old['ignore_sensor_ready'],
        'no_forced_error_callback':old['omit_accepted_error_callback'],
        'no_forced_stall_timeout':old['unbounded_polling'],
        'private_constant_unit_startup':once(once(source,'pub const BUFFER_SIZE:','const BUFFER_SIZE:'),
            '    pub fn startup(&self)->Result<(),ErrorCode> {','    pub fn startup(&self) {let _=self.startup_inner();}\n    fn startup_inner(&self)->Result<(),ErrorCode> {'),
    })
    return result


def fault_controls(source):
    old=old_fault(source)
    names=('unsigned_coefficients','unsigned_c0','wrong_shared_nibble_c1','wrong_shared_nibble_c10',
        'little_endian_16bit_trim','unsigned_adc','little_endian_adc','temperature_wrong_units','pressure_wrong_units',
        'omit_cubic_pressure','omit_quadratic_cross','ignore_pressure_temperature','wrong_temperature_source',
        'wrong_oversampling','unnecessary_shift','background_mode','skip_temperature_ready','skip_pressure_ready',
        'duplicate_temperature_callback','wrong_8x_scale')
    result={k:(old[k],'fail') for k in names}
    result['cache_without_age_bound']=(old['cache_without_age_bound'],'not_completed')
    result['missing_temperature_callback']=(once(source,'c.callback(value.map(|v|v as i32));','let _=(c,value);'),'not_completed')
    result['wrong_pressure_channel']=(once(source,'Some(true)=>if let Some(c)=self.pressure.get(){c.callback(value.map(|v|v as u32));},',
        'Some(true)=>if let Some(c)=self.temperature.get(){c.callback(value.map(|v|v as i32));},'),'fail')
    return result


def unsupported_controls(source):
    return {'coefficients_before_available':old_fault(source)['ignore_coefficient_ready']}


def audit_only_controls(source):
    """No digital error injected: vendor trace cannot establish silicon impact."""
    old=old_fault(source)
    return {'omit_workaround':once(source,'State::CommonCfg=>self.write(b,0x0e,0xa5,State::UnlockA),',
                'State::CommonCfg=>{self.initialized.set(true);self.buffer.replace(b);self.idle();return;}'),
            'wrong_workaround_value':old['wrong_correction'],'omit_relock':old['omit_relock']}
