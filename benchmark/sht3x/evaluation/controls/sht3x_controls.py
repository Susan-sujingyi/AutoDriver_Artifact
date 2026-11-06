"""Evaluator-only SHT30 positive and compiling negative controls."""
def replace_once(s, old, new):
    if s.count(old) != 1: raise AssertionError(f'SHT30 control anchor changed: {old!r}')
    return s.replace(old,new,1)

def legal_controls(source):
    changes={
        'temperature_three_bytes': ('const TEMP_ONLY: bool = false;', 'const TEMP_ONLY: bool = true;'),
        'cache_other_channel': ('const CACHE_OTHER: bool = false;', 'const CACHE_OTHER: bool = true;'),
        'poll_busy_nack': ('const WAIT_US: u32 = 15000;', 'const WAIT_US: u32 = 1000;'),
        'wait_20ms': ('const WAIT_US: u32 = 15000;', 'const WAIT_US: u32 = 20000;'),
        'explicit_clear_status': ('const PREPARATION: &[u16] = &[];', 'const PREPARATION: &[u16] = &[0x3041];'),
        'reset_and_heater_disable': ('const PREPARATION: &[u16] = &[];', 'const PREPARATION: &[u16] = &[0x30a2, 0x3066, 0x3041];'),
        'rounded_values': ('[-4500 + t * 17500 / 65535, h * 10000 / 65535]', '[-4500 + (t * 17500 + 32767) / 65535, (h * 10000 + 32767) / 65535]'),
        'full_frame_crc': ('if Self::crc(&buffer[offset..offset + 2]) != buffer[offset + 2] {', 'if Self::crc(&buffer[..2]) != buffer[2] || Self::crc(&buffer[3..5]) != buffer[5] {'),
    }
    result={'legal_'+n:replace_once(source,a,b) for n,(a,b) in changes.items()}
    status=replace_once(source,'const PREPARATION: &[u16] = &[];', 'const PREPARATION: &[u16] = &[0x3041, 0xf32d];')
    direct=replace_once(status,'Preparing, PrepareWait,','Preparing, StatusReading, PrepareWait,')
    direct=replace_once(direct,'            State::Preparing => {', """            State::Preparing if PREPARATION[self.preparation_index.get()] == 0xf32d => {
                self.state.set(State::StatusReading);
                if let Err((_e,buffer))=self.i2c.read(buffer,3) {self.buffer.replace(buffer);self.fail();}
            }
            State::Preparing | State::StatusReading => {""")
    result['legal_status_direct_read']=direct
    result['legal_status_combined_read']=replace_once(status,'match self.i2c.write(buffer, 2) {','match if command == 0xf32d { self.i2c.write_read(buffer, 2, 3) } else { self.i2c.write(buffer, 2) } {')
    return result

def fault_controls(source):
    changes={
        'wrong_temperature_offset': ('[-4500 + t * 17500 / 65535, h * 10000 / 65535]', '[t * 17500 / 65535, h * 10000 / 65535]'),
        'celsius_not_centicelsius': ('[-4500 + t * 17500 / 65535, h * 10000 / 65535]', '[(-4500 + t * 17500 / 65535) / 100, h * 10000 / 65535]'),
        'humidity_percent_not_centipercent': ('h * 10000 / 65535', 'h * 100 / 65535'),
        # Preserve exact endpoints: the original scorer still accepted usizeMAX
        # at a nonzero raw humidity that legitimately quantizes to zero.
        'fault_unsigned_humidity_wrap': ('let values = [-4500 + t * 17500 / 65535, h * 10000 / 65535];', 'let values = [-4500 + t * 17500 / 65535, if h>0 && h*10000/65535==0 {-1}else{h * 10000 / 65535}];'),
        'wrong_denominator': ('[-4500 + t * 17500 / 65535, h * 10000 / 65535]', '[-4500 + t * 17500 / 65536, h * 10000 / 65536]'),
        'little_endian_temperature': ('u16::from_be_bytes([buffer[0], buffer[1]])', 'u16::from_le_bytes([buffer[0], buffer[1]])'),
        'signed_temperature_word': ('u16::from_be_bytes([buffer[0], buffer[1]]) as i64', 'i16::from_be_bytes([buffer[0], buffer[1]]) as i64'),
        'swap_channels': ('self.emit(c, values[c.index()]);', 'self.emit(c, values[1-c.index()]);'),
        'omit_crc': ('if Self::crc(&buffer[offset..offset + 2]) != buffer[offset + 2] {', 'if false {'),
        'wrong_crc_polynomial': ('(crc << 1) ^ 0x31', '(crc << 1) ^ 0x07'),
        'wrong_command_byteorder': ('buffer[0] = (command >> 8) as u8; buffer[1] = command as u8;', 'buffer[0] = command as u8; buffer[1] = (command >> 8) as u8;'),
        'medium_repeatability': ('State::Command); 0x2400', 'State::Command); 0x240b'),
        'clock_stretching': ('State::Command); 0x2400', 'State::Command); 0x2c06'),
        'combined_measurement_read': ('self.i2c.write(buffer, 2)', 'self.i2c.write_read(buffer, 2, 6)'),
        'no_wait_no_retry': ('const WAIT_US: u32 = 15000;', 'const WAIT_US: u32 = 0;'),
        'request_deadline_exceeded': ('const WAIT_US: u32 = 15000;', 'const WAIT_US: u32 = 120000;'),
        'unbounded_busy_retry': ('const MAX_READ_NAKS: u8 = 60;', 'const MAX_READ_NAKS: u8 = 200;'),
        'humidity_only_three_bytes': ('{ 3 } else { 6 }', '{ 3 } else { 3 }'),
        'duplicate_temperature_callback': ('c.callback(Ok(value as i32));', 'c.callback(Ok(value as i32)); c.callback(Ok(value as i32));'),
        'wrong_callback_channel': ('self.emit(c, values[c.index()]);', 'self.emit(Channel::Temperature, values[c.index()]);'),
        'lose_submission_buffer': ('self.buffer.replace(buffer); self.cache.set([None; 2]); self.idle(); Err(error.into())', 'let _ = buffer; self.cache.set([None; 2]); self.idle(); Err(error.into())'),
        'busy_returns_ok': ('State::Off => return Err(ErrorCode::OFF), _ => return Err(ErrorCode::BUSY)', 'State::Off => return Err(ErrorCode::OFF), _ => return Ok(())'),
        'fabricate_humidity_error_zero': ('    fn fail(&self) {', '    fn fail(&self) {\n        if self.channel.get() == Channel::Humidity { self.emit(Channel::Humidity, 0); }'),
        'temperature_error_silenced': ('c.callback(Err(ErrorCode::FAIL));', 'let _ = c;'),
    }
    result={n:replace_once(source,a,b) for n,(a,b) in changes.items()}
    result['no_wait_no_retry']=replace_once(result['no_wait_no_retry'],'const MAX_READ_NAKS: u8 = 60;', 'const MAX_READ_NAKS: u8 = 0;')
    stale=replace_once(source, 'const CACHE_OTHER: bool = false;', 'const CACHE_OTHER: bool = true;')
    stale=replace_once(stale, 'cache[channel.index()].take()', 'cache[channel.index()]')
    result['replay_cached_channel']=stale
    keep_cache=replace_once(source, 'const CACHE_OTHER: bool = false;', 'const CACHE_OTHER: bool = true;')
    assert keep_cache.count('self.cache.set([None; 2]);')==2
    result['retain_cache_after_known_error']=keep_cache.replace('self.cache.set([None; 2]);','')
    return result
