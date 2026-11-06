"""Evaluator-only source controls; never supplied as candidate materials."""

def replace_once(source, old, new):
    if source.count(old) != 1:
        raise AssertionError(f'Expected one CCS811 control anchor: {old!r}')
    return source.replace(old, new, 1)


def pointer_reads(source):
    source = replace_once(source, 'enum State { Off,', 'enum State { ReadPointer, Off,')
    source = replace_once(source, '    client: Cell<Option<&\'a dyn AirQualityClient>>,',
        '    read_next: Cell<State>, read_len: Cell<usize>,\n    client: Cell<Option<&\'a dyn AirQualityClient>>,')
    source = replace_once(source, '            client: Cell::new(None) }',
        '            read_next: Cell::new(State::Off), read_len: Cell::new(0), client: Cell::new(None) }')
    source = replace_once(source, '''        self.state.set(next);
        self.i2c.enable();
        match self.i2c.write_read(buffer, 1, len) {''', '''        self.read_next.set(next); self.read_len.set(len);
        self.state.set(State::ReadPointer);
        self.i2c.enable();
        match self.i2c.write(buffer, 1) {''')
    return replace_once(source, '        let result = match self.state.get() {\n            State::Reset =>',
        '''        let result = match self.state.get() {
            State::ReadPointer => {
                self.state.set(self.read_next.get());
                match self.i2c.read(buffer, self.read_len.get()) {
                    Ok(()) => Ok(()),
                    Err((error, buffer)) => { self.buffer.replace(buffer); Err(error.into()) }
                }
            }
            State::Reset =>''')


def five_byte_poll(source):
    source = replace_once(source, 'let result = self.read(buffer, 0x00, 1, State::Poll);',
        'let result = self.read(buffer, 0x02, 5, State::Poll);')
    source = replace_once(source, 'State::Wait => self.read(buffer, 0x00, 1, State::Poll),',
        'State::Wait => self.read(buffer, 0x02, 5, State::Poll),')
    source = replace_once(source, 'State::Poll => { let status = buffer[0]; self.poll_status(buffer, status) }',
        'State::Poll => { let status = buffer[4]; self.poll_status(buffer, status) }')
    return replace_once(source,
        '''            let len = if self.channel.get() == Some(true) { 4 } else { 2 };
            return self.read(buffer, 0x02, len, State::Sample);''',
        '''            self.sample(buffer);
            return Ok(());''')


def cached_other_channel(source):
    source = replace_once(source, 'enum State { Off,', 'enum State { Cached, Off,')
    source = replace_once(source, '    client: Cell<Option<&\'a dyn AirQualityClient>>,',
        '    cached: Cell<Option<(bool,u32)>>,\n    client: Cell<Option<&\'a dyn AirQualityClient>>,')
    source = replace_once(source, '            client: Cell::new(None) }',
        '            cached: Cell::new(None), client: Cell::new(None) }')
    source = replace_once(source, '        self.channel.set(Some(tvoc));',
        '''        if self.cached.get().map(|(ch,_)| ch) == Some(tvoc) {
            self.channel.set(Some(tvoc)); self.wait(buffer, State::Cached, 1); return Ok(());
        }
        self.channel.set(Some(tvoc));''')
    source = replace_once(source, 'let len = if self.channel.get() == Some(true) { 4 } else { 2 };',
        'let len = 4;')
    source = replace_once(source, '        self.finish(if valid { Ok(value) } else { Err(ErrorCode::INVAL) });',
        '        self.finish(if valid { Ok(value) } else { Err(ErrorCode::INVAL) });')
    source = replace_once(source, '''        self.buffer.replace(buffer);
        self.finish(if valid { Ok(value) } else { Err(ErrorCode::INVAL) });''',
        '''        let other = if tvoc { 0 } else { 2 };
        let other_value = 256 * buffer[other] as u32 + buffer[other+1] as u32;
        if valid { self.cached.set(Some((!tvoc,other_value))); }
        self.buffer.replace(buffer);
        self.finish(if valid { Ok(value) } else { Err(ErrorCode::INVAL) });''')
    source = replace_once(source, '        let channel = self.channel.take();',
        '        if value.is_err() { self.cached.set(None); }\n        let channel = self.channel.take();')
    return replace_once(source, '            State::Por => self.write(',
        '''            State::Cached => {
                let (_,value) = self.cached.take().unwrap(); self.buffer.replace(buffer);
                self.finish(Ok(value)); return;
            }
            State::Por => self.write(''')


def legal_controls(source):
    return {
        'legal_full_eight_byte_sample': replace_once(source,
            'let len = if self.channel.get() == Some(true) { 4 } else { 2 };', 'let len = 8;'),
        'legal_five_byte_embedded_poll': five_byte_poll(source),
        'legal_stop_pointer_reads': pointer_reads(source),
        'legal_fifty_ms_poll': source.replace('POLL_US: u32 = 100_000;', 'POLL_US: u32 = 50_000;').replace('MAX_POLLS: u32 = 20;', 'MAX_POLLS: u32 = 40;'),
        'legal_shared_epoch_cache': cached_other_channel(source),
    }


def fault_controls(source):
    pairs = {
        'little_endian': ('256 * buffer[offset] as u32 + buffer[offset + 1] as u32',
                         'buffer[offset] as u32 + 256 * buffer[offset + 1] as u32'),
        'wrong_unit_scale': ('self.finish(if valid { Ok(value) }', 'self.finish(if valid { Ok(value * 1000) }'),
        'signed_tvoc': ('let value = 256 * buffer[offset] as u32 + buffer[offset + 1] as u32;',
                       'let value = (256 * buffer[offset] as u32 + buffer[offset + 1] as u32) as u16 as i16 as u32;'),
        'wrong_callback_channel': ('if tvoc { client.tvoc_data_available(value); } else { client.co2_data_available(value); }',
                                  'if tvoc { client.co2_data_available(value); } else { client.tvoc_data_available(value); }'),
        'duplicate_callback': ('client.co2_data_available(value);', 'client.co2_data_available(value); client.co2_data_available(value);'),
        'skip_data_ready': ('if status & 8 != 0 {', 'if true {'),
        'wrong_drive_mode': ('&[0x01, 0x10]', '&[0x01, 0x01]'),
        'skip_por_wait': ('State::Por, 20_000', 'State::Por, 0'),
        'skip_reset_wait': ('State::ResetWait, 2_000', 'State::ResetWait, 0'),
        'skip_application_wait': ('State::StartWait, 1_000', 'State::StartWait, 0'),
        'padded_app_start': ('&[0xf4]', '&[0xf4, 0]'),
        'ignore_app_valid': ('if status & 0x10 == 0 {', 'if false {'),
        'ignore_fw_mode': ('if buffer[0] & 0x80 == 0 {', 'if false {'),
        'ignore_identity': ('if buffer[0] != 0x81 {', 'if false {'),
        'ignore_sensor_error': ('if status & 1 != 0 {', 'if false {'),
        'omit_error_ack': ('return self.read(buffer, 0xe0, 1, State::Error);', 'return self.read(buffer, 0x00, 1, State::Error);'),
        'ignore_output_range': ('let valid = if tvoc { value <= 32768 } else { (400..=29206).contains(&value) };', 'let valid = true;'),
        'unbounded_ready_wait': ('const MAX_POLLS: u32 = 20;', 'const MAX_POLLS: u32 = 500;'),
        'omit_accepted_error_callback': ('let channel = self.channel.take();', 'let channel = self.channel.take().filter(|_| value.is_ok());'),
    }
    result = {name: replace_once(source, old, new) for name, (old,new) in pairs.items()}
    result['lose_immediate_error_buffer'] = source.replace(
        'Err((error, buffer)) => { self.buffer.replace(buffer); Err(error.into()) }',
        'Err((error, buffer)) => { let _ = buffer; Err(error.into()) }')
    return result
