"""v2 control witnesses: legal strategies and independently identified errors.

These sources are evaluator-only, never generation inputs or method outputs.
"""
from apds9960_controls import legal_controls as v1_legal, fault_controls as v1_fault, replace_once, threshold_window


def legal_controls(source):
    result=v1_legal(source)
    result['legal_private_buffer_constant']=source.replace('pub const BUFFER_SIZE','const BUFFER_SIZE')
    result['legal_unit_startup']=replace_once(source,'    pub fn startup(&self) -> Result<(), ErrorCode> {',
        '    pub fn startup(&self) { let _ = self.startup_inner(); }\n\n    fn startup_inner(&self) -> Result<(), ErrorCode> {')
    result['legal_other_rejection_codes']=source.replace('ErrorCode::BUSY','ErrorCode::FAIL').replace('ErrorCode::OFF','ErrorCode::FAIL')
    result['legal_powered_before_start']=source.replace('POWER_ON_WAIT_US: u32 = 6000','POWER_ON_WAIT_US: u32 = 0')
    result['legal_slow_poll']=source.replace('POLL_WAIT_US: u32 = 2000','POLL_WAIT_US: u32 = 200000')
    no_probe=replace_once(source,'    fn alarm(&self) {','''    fn alarm(&self) {
        if self.state.get() == State::PowerOnWait {
            if let Some(buffer) = self.buffer.take() {
                self.configuration_index.set(0);
                if let Err(error) = self.write_configuration(buffer) { self.fail_accepted(error); }
            }
            return;
        }''')
    result['legal_no_identity_probe']=no_probe
    background=replace_once(source,'                self.restore_idle();\n                if let Some(client)',
        '''                self.restore_idle();
                self.alarm.set_alarm(self.alarm.now(), A::Ticks::from_or_max(20000));
                if let Some(client)''')
    background=replace_once(background,'    fn alarm(&self) {','''    fn alarm(&self) {
        if self.state.get() == State::Ready {
            self.alarm.set_alarm(self.alarm.now(), A::Ticks::from_or_max(20000));
            return;
        }''')
    result['legal_background_alarm']=background
    deferred=replace_once(source,'    Status,','    Status,\n    Deferred,')
    deferred=replace_once(deferred,'        let result = self.read_register(buffer, 0x93, State::Status);',
        '        self.wait_us(buffer, State::Deferred, 100);\n        let result: Result<(), ErrorCode> = Ok(());')
    deferred=replace_once(deferred,'            State::ReadinessWait => (0x93, State::Status),',
        '            State::ReadinessWait | State::Deferred => (0x93, State::Status),')
    result['legal_deferred_submission']=deferred
    queued=replace_once(source,'    initialized: Cell<bool>,','    initialized: Cell<bool>,\n    queued: Cell<bool>,')
    queued=replace_once(queued,'            initialized: Cell::new(false),','            initialized: Cell::new(false),\n            queued: Cell::new(false),')
    queued=replace_once(queued,'    fn request(&self) -> Result<(), ErrorCode> {','''    fn request(&self) -> Result<(), ErrorCode> {
        if matches!(self.state.get(), State::Status | State::ReadinessWait | State::Sample) && !self.queued.replace(true) {
            return Ok(());
        }''')
    queued=replace_once(queued,'                    client.callback(proximity);','''                    client.callback(proximity);
                    if self.queued.replace(false) { let _ = self.request(); }''')
    result['legal_queue_second_request']=queued
    retry=replace_once(source,'''        if let Err(error) = status {
            self.buffer.replace(buffer);''','''        if let Err(error) = status {
            if matches!(self.state.get(), State::Status | State::Sample) {
                let reg = if self.state.get() == State::Status { 0x93 } else { 0x9c };
                if let Err(error) = self.read_register(buffer, reg, self.state.get()) { self.fail_accepted(error); }
                return;
            }
            self.buffer.replace(buffer);''')
    start=retry.index('    fn read_register(');end=retry.index('    fn write_configuration(')
    body=retry[start:end]
    body=replace_once(body,'''                self.buffer.replace(buffer);
                Err(error.into())''','''                let _ = error;
                match self.i2c.write_read(buffer, 1, 1) {
                    Ok(()) => Ok(()),
                    Err((error, buffer)) => { self.buffer.replace(buffer); Err(error.into()) }
                }''')
    retry=retry[:start]+body+retry[end:]
    result['legal_immediate_and_async_retry']=retry
    same=replace_once(source,'    initialized: Cell<bool>,','    initialized: Cell<bool>,\n    sampled: Cell<bool>,')
    same=replace_once(same,'            initialized: Cell::new(false),','            initialized: Cell::new(false),\n            sampled: Cell::new(false),')
    same=replace_once(same,'        let result = self.read_register(buffer, 0x93, State::Status);',
        '        let result = if self.sampled.get() { self.read_register(buffer, 0x9c, State::Sample) } else { self.read_register(buffer, 0x93, State::Status) };')
    same=replace_once(same,'                let proximity = buffer[0];','                let proximity = buffer[0];\n                self.sampled.set(true);')
    result['legal_reread_same_conversion']=same
    # PDF p22 PPERS=0 interrupts every cycle. PINT is not categorically wrong;
    # its validity depends on configuration and the requested sample semantics.
    result['legal_pint_every_cycle']=replace_once(source, 'if buffer[0] & 0x02 == 0 {', 'if buffer[0] & 0x20 == 0 {')
    return result


def fault_controls(source):
    old=v1_fault(source)
    required=('signed_proximity','wrong_raw_scaling','duplicate_callback','skip_readiness',
              'nonzero_optical_offset','enable_before_configuration','wrong_gain_4x',
              'wrong_gesture_pulse_setting','wrong_led_boost_100_percent','clear_config2_required_bit',
              'clear_config1_reserved_bits','fabricate_zero_on_failure')
    return {name:(old[name],'fail') for name in required} | {
        'never_delivers_callback':(replace_once(source, 'client.callback(proximity);', 'let _ = (client, proximity);'), 'not_completed'),
        'replay_cached_forever':(old['replay_cached_sample'],'not_completed'),
        'threshold_only_readiness':(replace_once(threshold_window(source), 'if buffer[0] & 0x02 == 0 {',
                                                'if buffer[0] & 0x20 == 0 {'), 'not_completed'),
    }
