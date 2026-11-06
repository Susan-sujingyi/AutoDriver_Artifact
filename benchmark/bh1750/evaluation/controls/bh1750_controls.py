"""Evaluator-only independent BH1750FVI implementation controls."""

def replace_once(source, old, new):
    if source.count(old) != 1:
        raise AssertionError(f'BH1750 control anchor changed: {old!r}')
    return source.replace(old, new, 1)


def discard_before_wait(source):
    source = replace_once(source, 'Command, Waiting', 'Command, Discard, Waiting')
    return replace_once(source, '            State::Command => {', '''            State::Command => {
                self.state.set(State::Discard);
                if let Err((_error, buffer)) = self.i2c.read(buffer, 2) {
                    self.buffer.replace(buffer); self.fail();
                }
            }
            State::Discard => {''')


def legal_controls(source):
    result = {
        'legal_wait_200ms': replace_once(source, 'const WAIT_US: u32 = 180_000;', 'const WAIT_US: u32 = 200_000;'),
        'legal_rounded_lux': replace_once(source, 'let lux = (raw * 5 / 6) as usize;', 'let lux = ((raw * 5 + 3) / 6) as usize;'),
        'legal_alternate_integer_equation': replace_once(source, 'let lux = (raw * 5 / 6) as usize;', 'let lux = (raw * 10 / 12) as usize;'),
        'legal_discard_early_read': discard_before_wait(source),
    }
    for name, commands in {
        'power_on': '[0x01]', 'power_on_reset': '[0x01, 0x07]',
        'explicit_mtreg': '[0x42, 0x65]', 'mtreg_reverse_order': '[0x65, 0x42]',
        'powerdown_reset_and_mtreg': '[0x00, 0x01, 0x07, 0x42, 0x65]',
    }.items():
        result['legal_' + name] = replace_once(source, 'const PREPARATION: &[u8] = &[];',
                                               f'const PREPARATION: &[u8] = &{commands};')
    return result


def fault_controls(source):
    changes = {
        'lux_zero_to_usize_max': ('client.callback(lux);', 'client.callback(if lux==0 {usize::MAX}else{lux});'),
        'little_endian': ('u16::from_be_bytes([buffer[0], buffer[1]])', 'u16::from_le_bytes([buffer[0], buffer[1]])'),
        'signed_sample': ('let lux = (raw * 5 / 6) as usize;', 'let lux = ((raw as i16).max(0) as u32 * 5 / 6) as usize;'),
        'raw_is_lux': ('let lux = (raw * 5 / 6) as usize;', 'let lux = raw as usize;'),
        'high_resolution2_scaling': ('let lux = (raw * 5 / 6) as usize;', 'let lux = (raw * 5 / 12) as usize;'),
        'millilux_instead_of_lux': ('let lux = (raw * 5 / 6) as usize;', 'let lux = (raw * 5000 / 6) as usize;'),
        'typical_wait_only': ('const WAIT_US: u32 = 180_000;', 'const WAIT_US: u32 = 120_000;'),
        'no_wait': ('const WAIT_US: u32 = 180_000;', 'const WAIT_US: u32 = 0;'),
        'deadline_exceeded': ('const WAIT_US: u32 = 180_000;', 'const WAIT_US: u32 = 300_000;'),
        'continuous_mode': ('buffer[0] = 0x20;', 'buffer[0] = 0x10;'),
        'wrong_resolution_mode': ('buffer[0] = 0x20;', 'buffer[0] = 0x21;'),
        'omit_measurement_command': ('buffer[0] = 0x20;', 'buffer[0] = 0x01;'),
        'wrong_mtreg': ('const PREPARATION: &[u8] = &[];', 'const PREPARATION: &[u8] = &[0x42, 0x66];'),
        'reset_while_powered_down': ('const PREPARATION: &[u8] = &[];', 'const PREPARATION: &[u8] = &[0x07];'),
        'register_style_write': ('match self.i2c.write(buffer, 1)', 'match self.i2c.write(buffer, 2)'),
        'one_byte_read': ('self.i2c.read(buffer, 2)', 'self.i2c.read(buffer, 1)'),
        'source_style_retrigger_read': ('self.i2c.read(buffer, 2)', 'self.i2c.write_read({ buffer[0] = 0x20; buffer }, 1, 2)'),
        'duplicate_callback': ('client.callback(lux);', 'client.callback(lux); client.callback(lux);'),
        'fabricate_error_zero': ('// AmbientLightClient cannot represent an error; zero is valid light.\n        self.idle();', '// Invalid error sentinel\n        self.idle();\n        if let Some(client) = self.client.get() { client.callback(0); }'),
        'lose_submission_buffer': ('self.buffer.replace(buffer); self.idle(); Err(error.into())', 'let _ = buffer; self.idle(); Err(error.into())'),
        'busy_returns_ok': ('            _ => return Err(ErrorCode::BUSY),\n        }\n        let buffer = self.buffer.take().ok_or(ErrorCode::BUSY)?;\n        self.preparation_index', '            _ => return Ok(()),\n        }\n        let buffer = self.buffer.take().ok_or(ErrorCode::BUSY)?;\n        self.preparation_index'),
    }
    result = {n: replace_once(source, a, b) for n, (a, b) in changes.items()}
    cached = replace_once(source, '    preparation_index: Cell<usize>,', '    preparation_index: Cell<usize>,\n    cached: Cell<Option<usize>>,')
    cached = replace_once(cached, 'preparation_index: Cell::new(0),', 'preparation_index: Cell::new(0), cached: Cell::new(None),')
    cached = replace_once(cached, '        self.preparation_index.set(0);', '''        if let Some(value) = self.cached.get() {
            self.buffer.replace(buffer);
            if let Some(client) = self.client.get() { client.callback(value); }
            return Ok(());
        }
        self.preparation_index.set(0);''')
    cached = replace_once(cached, '                let lux = (raw * 5 / 6) as usize;', '                let lux = (raw * 5 / 6) as usize;\n                self.cached.set(Some(lux));')
    result['replay_cached_sample'] = cached
    return result
