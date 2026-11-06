"""CCS811 v2 host-only legal variations and observable faulty submissions."""
from ccs811_controls import legal_controls as previous_legal, fault_controls as previous_fault


def background_poll(source):
    source=source.replace('enum State { Off,','enum State { Background, Off,',1)
    source=source.replace('        self.state.set(if self.initialized.get() { State::Ready } else { State::Off });',
        '''        self.state.set(if self.initialized.get() { State::Ready } else { State::Off });
        if self.initialized.get() {
            let ticks=(A::Frequency::frequency() as u64*100_000+999_999)/1_000_000;
            self.alarm.set_alarm(self.alarm.now(),A::Ticks::from_or_max(ticks).max(self.alarm.minimum_dt()));
        }''',1)
    source=source.replace('            State::Reset => {',
        '            State::Background => {self.buffer.replace(buffer);self.idle();return;}\n            State::Reset => {',1)
    source=source.replace('            State::Wait => self.read(buffer, 0x00, 1, State::Poll),',
        '            State::Ready => self.read(buffer,0x00,1,State::Background),\n            State::Wait => self.read(buffer, 0x00, 1, State::Poll),',1)
    return source


def legal_controls(source):
    result=previous_legal(source)
    result.update({
        'no_identity_probe':source.replace('self.read(buffer, 0x20, 1, State::Identity)','self.read(buffer, 0x24, 2, State::Version)'),
        'already_powered_no_por_wait':source.replace('State::Por, 20_000','State::Por, 0'),
        'private_buffer_constant_unit_startup':source.replace('pub const BUFFER_SIZE:','const BUFFER_SIZE:').replace(
            '    pub fn startup(&self) -> Result<(), ErrorCode> {',
            '    pub fn startup(&self) {let _=self.startup_inner();}\n    fn startup_inner(&self) -> Result<(), ErrorCode> {'),
        'background_status':background_poll(source),
        'unconnected_dataready_flag':source.replace('&[0x01, 0x10]','&[0x01, 0x18]'),
    })
    return result


def fault_controls(source):
    previous=previous_fault(source)
    result={k:(previous[k],'fail') for k in ('little_endian','wrong_unit_scale','signed_tvoc',
        'wrong_callback_channel','duplicate_callback','skip_data_ready','wrong_drive_mode','padded_app_start')}
    # The existing range guard converts the mis-signed 32768 ppb into Err.
    # No wrong value escapes; required healthy output is missing instead.
    result['signed_tvoc']=(previous['signed_tvoc'],'not_completed')
    result['missing_co2_callback']=(source.replace('client.co2_data_available(value);','let _=value;'),'not_completed')
    result['fabricated_co2']=(source.replace('client.co2_data_available(value);','client.co2_data_available(value.map(|_|400));'),'fail')
    from ccs811_controls import cached_other_channel
    result['permanent_other_channel_cache']=(cached_other_channel(source).replace('self.cached.take().unwrap()','self.cached.get().unwrap()'),'not_completed')
    return result
