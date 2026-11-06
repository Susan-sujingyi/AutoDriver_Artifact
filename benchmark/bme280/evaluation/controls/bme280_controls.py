"""Evaluator-only BME280 legal and faulty sources, preserving the original controls."""
from __future__ import annotations

def batched_trigger(source: str, *, legal: bool) -> str:
    """Control drivers differ only in their real I2C write framing."""
    old = '''        buffer[0] = register;
        buffer[1] = value;
        self.state.set(next);
        self.i2c.enable();
        match self.i2c.write(buffer, 2) {'''
    payload = '[0xf5, 0, 0xf2, 1, 0xf4, value]' if legal else '[0xf2, 1, 0, value]'
    length = 6 if legal else 4
    new = f'''        let length = if register == 0xf4 && next == State::Trigger {{
            buffer[..{length}].copy_from_slice(&{payload});
            {length}
        }} else {{
            buffer[0] = register;
            buffer[1] = value;
            2
        }};
        self.state.set(next);
        self.i2c.enable();
        match self.i2c.write(buffer, length) {{'''
    if old not in source:
        raise AssertionError('Batched-write control anchor disappeared')
    return source.replace(old, new, 1)


def calibration_during_initial_conversion(source: str) -> str:
    """A legal startup may read NVM while an initial conversion runs."""
    source = source.replace('    ResetWait,\n', '    ResetWait,\n    StartupConfig,\n    StartupHumidity,\n    StartupTrigger,\n    StartupWait,\n', 1)
    source = source.replace('            State::CalibrationTp => {', '''            State::StartupConfig => self.write_register(buffer, 0xf2, 1, State::StartupHumidity),
            State::StartupHumidity => self.write_register(buffer, 0xf4, 0x35, State::StartupTrigger),
            State::StartupTrigger => self.read_register(buffer, 0x88, 26, State::CalibrationTp),
            State::CalibrationTp => {''', 1)
    source = source.replace('                self.write_register(buffer, 0xf5, 0, State::Config)', '''                self.wait_ms(buffer, State::StartupWait, 50);
                return;''', 1)
    source = source.replace('    fn alarm(&self) {', '''    fn alarm(&self) {
        if self.state.get() == State::ResetWait {
            if let Some(buffer) = self.buffer.take() {
                if let Err(error) = self.write_register(buffer, 0xf5, 0, State::StartupConfig) {
                    self.fail_accepted(error);
                }
            }
            return;
        }
        if self.state.get() == State::StartupWait {
            self.initialized.set(true);
            self.restore_idle();
            return;
        }''', 1)
    return source



def legal_controls(source: str) -> dict[str, str]:
    return {
        'legal_address_value_pairs': batched_trigger(source, legal=True),
        'legal_calibration_during_conversion': calibration_during_initial_conversion(source),
        'legal_slow_startup': source.replace('State::ResetWait, 2)', 'State::ResetWait, 200)'),
        'legal_ctrl_meas_readiness': source.replace('(0xf3, 1, State::ConversionStatus)',
            '(0xf4, 1, State::ConversionStatus)').replace('if buffer[0] & 0x09 != 0 {',
            'if buffer[0] & 0x03 != 0 {'),
        'legal_early_status_polling': source.replace('State::ConversionWait, 50)', 'State::ConversionWait, 1)'),
        'legal_helper_module': source.replace('0xf4, 0x35, State::Trigger',
            '0xf4, control::FORCED, State::Trigger') + '\nmod control;\n',
    }


def helper_files(source: str) -> dict[str, dict[str, str]]:
    return {'legal_helper_module': {'control.rs': 'pub const FORCED: u8 = 0x35;\n'}}


def fault_controls(source: str) -> dict[str, str]:
    mutations = {
        'humidity_zero_to_usize_max': ('client.callback(Self::humidity(c, raw_humidity, fine));',
            'let v=Self::humidity(c, raw_humidity, fine);client.callback(if v==0 {usize::MAX}else{v});'),
        'wrong_pressure_unit': ('let hpa = pressure / 25600;', 'let hpa = pressure / 256;'),
        'unsigned_packed_h4': ('c.h4 = ((h4 << 4) as i16) >> 4;', 'c.h4 = h4 as i16;'),
        'no_forced_trigger': ('0xf4, 0x35, State::Trigger', '0xf4, 0x34, State::Trigger'),
        'ignore_identity': ('if buffer[0] != 0x60 {', 'if false {'),
        'skip_reset_wait': ('State::ResetWait, 2)', 'State::ResetWait, 0)'),
        'ignore_readiness': ('if buffer[0] & 0x09 != 0 {', 'if false {'),
        'duplicate_temperature_callback': ('client.callback(Ok(temperature));',
            'client.callback(Ok(temperature)); client.callback(Ok(temperature));'),
        'lose_buffer_on_submit_error': ('self.buffer.replace(buffer);\n                Err(error.into())',
            'let _ = buffer;\n                Err(error.into())'),
    }
    results = {'illegal_auto_increment_write': batched_trigger(source, legal=False)}
    for name, (old, new) in mutations.items():
        if old not in source:
            raise AssertionError('BME280 mutation anchor disappeared: ' + name)
        results[name] = source.replace(old, new)
    return results
