"""Evaluator-only LPS22HB control sources; never exposed to a candidate method.

These transforms change executable behavior, not evaluator expectations. The
caller must establish successful compilation before counting any detection.
"""
from __future__ import annotations


def replace_once(source: str, old: str, new: str) -> str:
    if source.count(old) != 1:
        raise AssertionError(f'Expected one LPS22HB control anchor: {old!r}')
    return source.replace(old, new, 1)


def split_address_read(source: str) -> str:
    """Use a pointer write followed by a read instead of combined write_read."""
    source = replace_once(source, '    Identity,\n', '    Identity,\n    ReadAddress,\n')
    source = replace_once(source, '    state: Cell<State>,\n',
                          '    state: Cell<State>,\n    read_next: Cell<State>,\n')
    source = replace_once(source, '            state: Cell::new(State::Off),\n',
                          '            state: Cell::new(State::Off),\n            read_next: Cell::new(State::Off),\n')
    source = replace_once(source, '''        buffer[0] = register;
        self.state.set(next);
        self.i2c.enable();
        match self.i2c.write_read(buffer, 1, 1) {''', '''        buffer[0] = register;
        self.read_next.set(next);
        self.state.set(State::ReadAddress);
        self.i2c.enable();
        match self.i2c.write(buffer, 1) {''')
    source = replace_once(source, '        let result = match self.state.get() {\n', '''        let result = match self.state.get() {
            State::ReadAddress => {
                self.state.set(self.read_next.get());
                match self.i2c.read(buffer, 1) {
                    Ok(()) => Ok(()),
                    Err((error, buffer)) => {
                        self.buffer.replace(buffer);
                        Err(error.into())
                    }
                }
            }
''')
    return source


def legal_controls(source: str) -> dict[str, str]:
    return {
        'legal_pressure_low_bytes_first': replace_once(source,
            '[(0x2b, 3), (0x2c, 4), (0x28, 0), (0x29, 1), (0x2a, 2)]',
            '[(0x28, 0), (0x29, 1), (0x2b, 3), (0x2c, 4), (0x2a, 2)]'),
        'legal_wait_one_cycle_before_polling': replace_once(source,
            'const FIRST_POLL_MS: u32 = 20;', 'const FIRST_POLL_MS: u32 = 1000;'),
        'legal_separate_pointer_write_read': split_address_read(source),
    }


def fault_controls(source: str) -> dict[str, str]:
    anchors = {
        'wrong_pressure_unit_pa': ('let pressure = raw_pressure / 4096;',
                                   'let pressure = raw_pressure * 100 / 4096;'),
        'unsigned_temperature': ('let temperature = i16::from_le_bytes([bytes[3], bytes[4]]) as i32;',
                                 'let temperature = u16::from_le_bytes([bytes[3], bytes[4]]) as i32;'),
        'wrong_temperature_unit_celsius': ('client.callback(Ok(temperature));',
                                          'client.callback(Ok(temperature / 100));'),
        'duplicate_temperature_callback': ('client.callback(Ok(temperature));',
                                           'client.callback(Ok(temperature)); client.callback(Ok(temperature));'),
        'ignore_readiness': ('if buffer[0] & 0x03 != 0x03 {', 'if false {'),
        'unbounded_readiness_wait': ('if self.timed_out() {', 'if false {'),
        'ignore_identity': ('if buffer[0] != 0xb1 {', 'if false {'),
        'wrong_odr_10hz': ('0x10, 0x12, State::Configure', '0x10, 0x22, State::Configure'),
        'disable_bdu': ('0x10, 0x12, State::Configure', '0x10, 0x10, State::Configure'),
        'enable_auto_increment_with_bdu': ('0x11, 0x00, State::Interface', '0x11, 0x10, State::Interface'),
        'pressure_high_not_last': (
            '[(0x2b, 3), (0x2c, 4), (0x28, 0), (0x29, 1), (0x2a, 2)]',
            '[(0x28, 0), (0x29, 1), (0x2a, 2), (0x2b, 3), (0x2c, 4)]'),
    }
    mutations = {name: replace_once(source, old, new) for name, (old, new) in anchors.items()}
    restore = 'self.buffer.replace(buffer);\n                Err(error.into())'
    if source.count(restore) != 2:
        raise AssertionError('Read and write immediate-error buffer anchors changed')
    mutations['lose_buffer_on_submit_error'] = source.replace(restore,
        'let _ = buffer;\n                Err(error.into())')
    return mutations
