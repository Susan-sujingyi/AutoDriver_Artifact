"""ISL29035 v2 semantic controls; host-only, no method results."""
from isl29035_controls import legal_controls as old_legal, fault_controls as old_fault


def two_transactions(source):
    source=source.replace('    Sample,','    SampleMsb,\n    Sample,',1)
    source=source.replace('    initialized: Cell<bool>,','    initialized: Cell<bool>,\n    upper: Cell<u8>,',1)
    source=source.replace('            initialized: Cell::new(false),','            initialized: Cell::new(false),\n            upper: Cell::new(0),',1)
    source=source.replace('            State::Sample => {','''            State::SampleMsb => {
                self.upper.set(buffer[0]);self.read_register(buffer,0x02,State::Sample)
            }
            State::Sample => {''',1)
    source=source.replace('let raw = buffer[0] as usize;','let raw = ((self.upper.get() as usize)<<8) | buffer[0] as usize;',1)
    source=source.replace('            self.state.set(State::Sample);','            self.state.set(State::SampleMsb);',1)
    source=source.replace('            buffer[0] = 0x02;','            buffer[0] = 0x03;',1)
    return source


def only_initial_wait(source):
    source=source.replace('    Sample,','    InitWait,\n    Sample,',1)
    source=source.replace('const SAMPLE_WAIT_US: u32 = 1000;','const SAMPLE_WAIT_US: u32 = 0;',1)
    source=source.replace('''            State::Enable => {
                self.initialized.set(true);''','''            State::Enable => {
                self.buffer.replace(buffer);self.state.set(State::InitWait);
                let ticks=(A::Frequency::frequency() as u64+999)/1000;
                self.alarm.set_alarm(self.alarm.now(),A::Ticks::from_or_max(ticks).max(self.alarm.minimum_dt()));return;
            }
            State::InitWait => {
                self.initialized.set(true);''',1)
    source=source.replace('    fn alarm(&self) {','''    fn alarm(&self) {
        if self.state.get()==State::InitWait {self.initialized.set(true);self.restore_idle();return;}
''',1)
    return source


def legal_controls(source):
    result=old_legal(source)
    result.update({
        'separate_MSB_LSB_full_word':two_transactions(source),
        'initial_wait_then_immediate_reads':only_initial_wait(source),
        'round_to_nearest_lux':source.replace('let lux = raw * 4000 / 256;','let lux = (raw * 4000 + 128) / 256;'),
        'unused_interrupt_persistence':source.replace('0x00, 0xa0, State::Enable','0x00, 0xa3, State::Enable'),
        'no_identity_probe':source.replace('let result = self.read_register(buffer, 0x0f, State::Identity);','let result = self.write_register(buffer,0x00,0,State::PowerDown);'),
        'private_constant_unit_startup':source.replace('pub const BUFFER_SIZE:','const BUFFER_SIZE:').replace('    pub fn startup(&self) -> Result<(), ErrorCode> {','    pub fn startup(&self) {let _=self.startup_inner();}\n    fn startup_inner(&self) -> Result<(), ErrorCode> {'),
    })
    return result


def fault_controls(source):
    prior=old_fault(source)
    result={k:(prior[k],'fail') for k in ('wrong_lux_range_scale','wrong_adc_denominator','signed_adc',
        'fault_unsigned_scalar_wrap','duplicate_callback','wrong_resolution_12bit','wrong_range_1000lux','wrong_mode_als_once')}
    result['missing_callback']=(source.replace('client.callback(lux);','let _=(client,lux);'),'not_completed')
    result['fabricated_constant_lux']=(source.replace('client.callback(lux);','client.callback(0);'),'fail')
    return result
