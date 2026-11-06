"""Evaluator-only APDS9960 positive/negative executable controls."""
from __future__ import annotations


def replace_once(source: str, old: str, new: str) -> str:
    if source.count(old) != 1:
        raise AssertionError(f'Expected one APDS9960 control anchor: {old!r}')
    return source.replace(old, new, 1)


def batched_configuration(source: str) -> str:
    return replace_once(source, '''        buffer[0] = register;
        buffer[1] = value;
        self.state.set(State::Configure);
        self.i2c.enable();
        match self.i2c.write(buffer, 2) {''', '''        buffer[0] = register;
        buffer[1] = value;
        let length = if register == 0x8d {
            let index = self.configuration_index.get();
            buffer[2] = CONFIGURATION[index + 1].1;
            self.configuration_index.set(index + 1);
            3
        } else {
            2
        };
        self.state.set(State::Configure);
        self.i2c.enable();
        match self.i2c.write(buffer, length) {''')


def split_pointer_reads(source: str) -> str:
    source = replace_once(source, '    Identity,', '    Identity,\n    PointerIdentity,\n    PointerStatus,\n    PointerSample,')
    source = replace_once(source, '        self.state.set(next);\n        self.i2c.enable();\n        match self.i2c.write_read(buffer, 1, 1) {',
        '        self.state.set(match next { State::Identity => State::PointerIdentity, State::Status => State::PointerStatus, _ => State::PointerSample });\n        self.i2c.enable();\n        match self.i2c.write(buffer, 1) {')
    return replace_once(source, '        let result = match self.state.get() {', '''        let result = match self.state.get() {
            State::PointerIdentity | State::PointerStatus | State::PointerSample => {
                self.state.set(match self.state.get() {
                    State::PointerIdentity => State::Identity,
                    State::PointerStatus => State::Status,
                    _ => State::Sample,
                });
                match self.i2c.read(buffer, 1) {
                    Ok(()) => Ok(()),
                    Err((error, buffer)) => {
                        self.buffer.replace(buffer);
                        Err(error.into())
                    }
                }
            }''')


def clear_interrupt_command(source: str) -> str:
    source = replace_once(source, '[(u8, u8); 7]', '[(u8, u8); 8]')
    source = replace_once(source, '    (0x80, 0x05),', '    (0xe7, 0x00),\n    (0x80, 0x05),')
    return replace_once(source, 'match self.i2c.write(buffer, 2)',
                        'match self.i2c.write(buffer, if register == 0xe7 { 1 } else { 2 })')


def threshold_window(source: str) -> str:
    source = replace_once(source, '[(u8, u8); 7]', '[(u8, u8); 10]')
    return replace_once(source, '    (0x80, 0x05),',
                        '    (0x89, 0x00),\n    (0x8b, 0x01),\n    (0x8c, 0x10),\n    (0x80, 0x05),')


def discard_before_wait(source: str) -> str:
    source = replace_once(source, '    Identity,', '    Identity,\n    Discard,')
    source = replace_once(source, 'let result = self.read_register(buffer, 0x93, State::Status);',
                          'let result = self.read_register(buffer, 0x9c, State::Discard);')
    return replace_once(source, '        let result = match self.state.get() {',
                          '        let result = match self.state.get() {\n            State::Discard => self.read_register(buffer, 0x93, State::Status),')


def legal_controls(source: str) -> dict[str, str]:
    first = '    (0x8e, 0x47), // 8 us pulses, eight pulses per proximity acquisition.\n'
    second = '    (0x8f, 0x04), // PGAIN 2x, LDRIVE 100 mA; ALS gain is unused.\n'
    return {
        'legal_discard_pdata_before_wait': discard_before_wait(source),
        'legal_pointer_write_then_read': split_pointer_reads(source),
        'legal_documented_command_only_interrupt_clear': clear_interrupt_command(source),
        'legal_source_threshold_window_zero_one': threshold_window(source),
        'legal_inactive_als_saturation_interrupt': replace_once(source, '(0x90, 0x11)', '(0x90, 0x51)'),
        'legal_official_identity_only': replace_once(source, 'if id != 0xab && id != 0x9c {', 'if id != 0xab {'),
        'legal_one_millisecond_poll': replace_once(source,
            'const POLL_WAIT_US: u32 = 2000;', 'const POLL_WAIT_US: u32 = 1000;'),
        'legal_gain_before_pulse_configuration': replace_once(source, first + second, second + first),
        'legal_batched_config1_ppulse_write': batched_configuration(source),
        'legal_inactive_als_gain': replace_once(source, '(0x8f, 0x04)', '(0x8f, 0x07)'),
        'legal_inert_sleep_after_interrupt': replace_once(source, '(0x9f, 0x00)', '(0x9f, 0x10)'),
    }


def fault_controls(source: str) -> dict[str, str]:
    anchors = {
        'signed_proximity': ('let proximity = buffer[0];', 'let proximity = (buffer[0] as i8).max(0) as u8;'),
        'wrong_raw_scaling': ('let proximity = buffer[0];', 'let proximity = buffer[0] / 2;'),
        'duplicate_callback': ('client.callback(proximity);',
                               'client.callback(proximity); client.callback(proximity);'),
        'skip_readiness': ('if buffer[0] & 0x02 == 0 {', 'if false {'),
        'poll_threshold_pint': ('if buffer[0] & 0x02 == 0 {', 'if buffer[0] & 0x20 == 0 {'),
        'unbounded_readiness': ('if waits >= MAX_READY_WAITS {', 'if false {'),
        'skip_power_on_wait': ('const POWER_ON_WAIT_US: u32 = 6000;', 'const POWER_ON_WAIT_US: u32 = 0;'),
        'nonzero_optical_offset': ('(0x9f, 0x00)', '(0x9d, 0x01)'),
        'enable_before_configuration': ('(0x80, 0x00)', '(0x80, 0x05)'),
        'accept_busy_without_operation': ('return Err(if self.state.get() == State::Off { ErrorCode::OFF } else { ErrorCode::BUSY });', 'return Ok(());'),
        'wrong_gain_4x': ('(0x8f, 0x04)', '(0x8f, 0x08)'),
        'wrong_gesture_pulse_setting': ('(0x8e, 0x47)', '(0x8e, 0x89)'),
        'wrong_led_boost_100_percent': ('(0x90, 0x11)', '(0x90, 0x01)'),
        'clear_config2_required_bit': ('(0x90, 0x11)', '(0x90, 0x10)'),
        'clear_config1_reserved_bits': ('(0x8d, 0x60)', '(0x8d, 0x00)'),
        'enable_proximity_interrupt': ('(0x80, 0x05)', '(0x80, 0x25)'),
        'ignore_identity': ('if id != 0xab && id != 0x9c {', 'if false {'),
        'accept_unsupported_interrupt_request': ('Err(ErrorCode::NOSUPPORT)', 'self.request()'),
        'fabricate_zero_on_failure': (
            '''        // This HIL cannot represent an error; zero is a valid proximity value.
        self.restore_idle();''',
            '''        // Deliberately invalid scalar error notification.
        self.restore_idle();
        if let Some(client) = self.client.get() { client.callback(0); }'''),
    }
    controls = {name: replace_once(source, old, new) for name, (old, new) in anchors.items()}
    restore = 'self.buffer.replace(buffer);\n                Err(error.into())'
    if source.count(restore) != 2:
        raise AssertionError('Immediate-error buffer restoration anchors changed')
    controls['lose_buffer_on_submit_error'] = source.replace(restore,
        'let _ = buffer;\n                Err(error.into())')
    cached = replace_once(source, '    initialized: Cell<bool>,', '    initialized: Cell<bool>,\n    cached: Cell<Option<u8>>,')
    cached = replace_once(cached, '            initialized: Cell::new(false),', '            initialized: Cell::new(false),\n            cached: Cell::new(None),')
    cached = replace_once(cached, '        self.readiness_waits.set(0);', '''        if let Some(value) = self.cached.get() {
            self.buffer.replace(buffer);
            if let Some(client) = self.client.get() { client.callback(value); }
            return Ok(());
        }
        self.readiness_waits.set(0);''')
    cached = replace_once(cached, '                let proximity = buffer[0];', '                let proximity = buffer[0];\n                self.cached.set(Some(proximity));')
    controls['replay_cached_sample'] = cached
    return controls
