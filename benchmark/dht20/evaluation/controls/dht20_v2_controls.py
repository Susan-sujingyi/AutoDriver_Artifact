"""Host-only DHT20 v2 semantic controls; never migration input or method data."""
from dht20_controls import change, legal_controls as old_legal, fault_controls as old_fault


def legal_controls(source):
    result=old_legal(source)
    old=old_fault(source)
    result.update({
        'powered_stable_short_constructor_wait':old['fault_por_too_short'],
        'powered_stable_99900_constructor_wait':old['fault_por_boundary_99900us'],
        'retain_valid_cache_after_error':old['fault_cache_survives_integrity_error'],
        'no_forced_callback_reentry':old['fault_error_cleanup_after_callback'],
        'no_artificial_busy_timeout':old['fault_unbounded_busy_wait'],
        'long_300ms_measurement_wait':change(source,'const FIRST_WAIT_US: u32 = 80000;','const FIRST_WAIT_US: u32 = 300000;'),
        'private_constant_unit_startup':change(change(source,'pub const BUFFER_SIZE:','const BUFFER_SIZE:'),
            '    pub fn startup(&self)->Result<(),ErrorCode> {',
            '    pub fn startup(&self) {let _=self.startup_inner();}\n    fn startup_inner(&self)->Result<(),ErrorCode> {'),
    })
    return result


def fault_controls(source):
    old=old_fault(source)
    failures=('fault_negative_temperature_plus_two','fault_humidity_wrapping_callback',
        'fault_wire_address_as_register','fault_skip_calibration','fault_only_cal_bit3',
        'fault_restore_zero_pair','fault_wrong_restore_command','fault_wrong_trigger_parameter',
        'fault_skip_crc','fault_ignore_busy','fault_temperature_units','fault_humidity_units',
        'fault_shared_temperature_nibble','fault_shared_humidity_nibble','fault_temperature_unsigned',
        'fault_duplicate_callback','fault_humidity_error_fabricated')
    unfinished=('fault_exact_calibration_byte','fault_short_frame','fault_crc_initial_zero',
        'fault_crc_polynomial','fault_crc_omits_status','fault_lost_buffer','fault_pending_never_cleared','fault_cached_channel_replayed')
    result={k:(old[k],'fail') for k in failures}
    result.update({k:(old[k],'not_completed') for k in unfinished})
    result['missing_temperature_callback']=(change(source,'client.callback(Ok(value as i32));','let _=(client,value);'),'not_completed')
    result['wrong_temperature_channel']=(change(source,'self.emit(c,values[c.index()]);Ok(())','self.emit(Channel::Humidity,values[c.index()]);Ok(())'),'fail')
    return result


def unsupported_controls(source):
    old=old_fault(source)
    return {k:old[k] for k in ('fault_recovery_read_boundary_4900us','fault_recovery_write_boundary_9900us',
        'fault_skip_recovery_read_wait','fault_skip_recovery_write_wait')}
