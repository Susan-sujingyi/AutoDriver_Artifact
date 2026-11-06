"""SHT30 v2 scorer controls; host only, no method feedback."""
from sht3x_controls import legal_controls as old_legal, fault_controls as old_fault


def legal_controls(source):
    result=old_legal(source)
    result.update({
        'wait_120ms':source.replace('const WAIT_US: u32 = 15000;','const WAIT_US: u32 = 120000;'),
        'more_busy_retries':source.replace('const MAX_READ_NAKS: u8 = 60;','const MAX_READ_NAKS: u8 = 200;'),
        'retain_valid_old_cache_on_later_error':old_fault(source)['retain_cache_after_known_error'],
        'rated_temperature_guard':source.replace('    fn emit(&self, channel: Channel, value: i64) {','    fn emit(&self, channel: Channel, value: i64) {\n        if channel==Channel::Temperature && (value < -4000 || value > 12500){self.fail();return;}'),
        'private_constant_unit_startup':source.replace('pub const BUFFER_SIZE:','const BUFFER_SIZE:').replace('    pub fn startup(&self) -> Result<(), ErrorCode> {','    pub fn startup(&self) {let _=self.startup_inner();}\n    fn startup_inner(&self) -> Result<(), ErrorCode> {'),
    })
    return result


def fault_controls(source):
    old=old_fault(source)
    result={k:(old[k],'fail') for k in ('wrong_temperature_offset','celsius_not_centicelsius','humidity_percent_not_centipercent',
        'fault_unsigned_humidity_wrap','wrong_denominator','little_endian_temperature','signed_temperature_word','swap_channels',
        'omit_crc','wrong_command_byteorder','medium_repeatability','clock_stretching','duplicate_temperature_callback','wrong_callback_channel')}
    result.update({k:(old[k],'not_completed') for k in ('wrong_crc_polynomial','combined_measurement_read','no_wait_no_retry','humidity_only_three_bytes','busy_returns_ok','replay_cached_channel')})
    result['missing_temperature']=(source.replace('c.callback(Ok(value as i32));','let _=(c,value);'),'not_completed')
    return result
