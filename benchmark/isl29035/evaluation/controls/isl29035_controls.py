"""Evaluator-only ISL29035 controls, independent of any method or IR."""
from __future__ import annotations


def replace_once(source: str, old: str, new: str) -> str:
    if source.count(old) != 1:
        raise AssertionError(f'Expected one ISL29035 control anchor: {old!r}')
    return source.replace(old, new, 1)


def batched_configuration(source: str) -> str:
    return replace_once(source,
        '            State::Brownout => self.write_register(buffer, 0x00, 0x00, State::PowerDown),',
        '''            State::Brownout => {
                buffer[..3].copy_from_slice(&[0x00, 0x00, 0x09]);
                self.state.set(State::Resolution);
                self.i2c.enable();
                match self.i2c.write(buffer, 3) {
                    Ok(()) => Ok(()),
                    Err((error, buffer)) => {
                        self.buffer.replace(buffer);
                        Err(error.into())
                    }
                }
            }''')


def legal_controls(source: str) -> dict[str, str]:
    return {
        'legal_half_millisecond_wait': replace_once(source,
            'const SAMPLE_WAIT_US: u32 = 1000;', 'const SAMPLE_WAIT_US: u32 = 500;'),
        'legal_two_byte_sample_burst': replace_once(source,
            '            if let Err((error, buffer)) = self.i2c.write_read(buffer, 1, 1) {',
            '            if let Err((error, buffer)) = self.i2c.write_read(buffer, 1, 2) {'),
        'legal_batched_configuration': batched_configuration(source),
    }


def fault_controls(source: str) -> dict[str, str]:
    anchors = {
        'wrong_lux_range_scale': ('let lux = raw * 4000 / 256;', 'let lux = raw * 1000 / 256;'),
        'wrong_adc_denominator': ('let lux = raw * 4000 / 256;', 'let lux = raw * 4000 / 65536;'),
        'signed_adc': ('let raw = buffer[0] as usize;',
                       'let raw = (buffer[0] as i8 as i16).unsigned_abs() as usize;'),
        'fault_unsigned_scalar_wrap': ('client.callback(lux);',
                                      'client.callback(if lux==0 {usize::MAX}else{lux});'),
        'duplicate_callback': ('client.callback(lux);', 'client.callback(lux); client.callback(lux);'),
        'skip_integration_wait': ('const SAMPLE_WAIT_US: u32 = 1000;', 'const SAMPLE_WAIT_US: u32 = 0;'),
        'wrong_resolution_12bit': ('0x01, 0x09, State::Resolution', '0x01, 0x05, State::Resolution'),
        'wrong_range_1000lux': ('0x01, 0x09, State::Resolution', '0x01, 0x08, State::Resolution'),
        'wrong_mode_als_once': ('0x00, 0xa0, State::Enable', '0x00, 0x20, State::Enable'),
        'leave_brownout_set': ('0x0f, id & 0x7f, State::Brownout', '0x0f, id, State::Brownout'),
        'wrong_identity_mask': ('if id & 0x38 != 0x28 {', 'if id & 0x18 != 0x08 {'),
        'require_exact_identity_byte': ('if id & 0x38 != 0x28 {', 'if id != 0xa8 {'),
        'fabricate_darkness_on_failure': (
            '''        // callback(0) would falsely report a valid darkness measurement.
        self.restore_idle();''',
            '''        // Incorrect scalar error substitute, for evaluator rejection.
        self.restore_idle();
        if let Some(client) = self.client.get() { client.callback(0); }'''),
    }
    result = {name: replace_once(source, old, new) for name, (old, new) in anchors.items()}
    restore = 'Err((error, buffer)) => {\n                self.buffer.replace(buffer);'
    if source.count(restore) != 2:
        raise AssertionError('Immediate-error buffer restoration anchors changed')
    result['lose_buffer_on_submit_error'] = source.replace(restore,
        'Err((error, buffer)) => {\n                let _ = buffer;')
    return result
