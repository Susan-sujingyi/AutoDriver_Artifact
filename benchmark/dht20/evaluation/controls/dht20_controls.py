"""Predefined evaluator controls; none are inputs to migration methods."""

def change(source, old, new, count=1):
    if source.count(old) != count:
        raise ValueError(f'Control anchor expected {count} instances: {old!r}')
    return source.replace(old, new)


def legal_controls(s):
    return {
        'legal_status_then_frame': change(s, 'const STATUS_FIRST: bool = false;', 'const STATUS_FIRST: bool = true;'),
        'legal_cross_channel_cache': change(s, 'const CACHE_OTHER: bool = false;', 'const CACHE_OTHER: bool = true;'),
        'legal_reordered_recovery': change(s, '[0x1b, 0x1c, 0x1e];', '[0x1e, 0x1b, 0x1c];'),
        'legal_conservative_calibration_recovery': change(s, 'if b[0]&0x18!=0x18', 'if b[0]!=0x18'),
        'legal_manufacturer_long_por': change(s, 'const POR_US: u32 = 100000;', 'const POR_US: u32 = 500000;'),
        'legal_wait150ms': change(s, 'const FIRST_WAIT_US: u32 = 80000;', 'const FIRST_WAIT_US: u32 = 150000;'),
        'legal_early_busy_polling': change(s, 'const FIRST_WAIT_US: u32 = 80000;', 'const FIRST_WAIT_US: u32 = 1000;'),
        'legal_long_recovery_waits': change(change(s, 'const RESET_READ_US: u32 = 5000;', 'const RESET_READ_US: u32 = 8000;'), 'const RESET_WRITE_US: u32 = 10000;', 'const RESET_WRITE_US: u32 = 15000;'),
        'legal_round_nearest': change(s, '[t as i64*20000/1048576-5000,h as i64*10000/1048576]', '[(t as i64*20000+524288)/1048576-5000,(h as i64*10000+524288)/1048576]'),
        'legal_reduced_fraction': change(s, 'h as i64*10000/1048576', 'h as i64*625/65536'),
        'legal_bounded_frame_retry': bounded_frame_retry(s),
    }


def fault_controls(s):
    cached = change(s, 'const CACHE_OTHER: bool = false;', 'const CACHE_OTHER: bool = true;')
    return {
        'fault_por_too_short': change(s, 'const POR_US: u32 = 100000;', 'const POR_US: u32 = 1000;'),
        'fault_por_boundary_99900us': change(s, 'const POR_US: u32 = 100000;', 'const POR_US: u32 = 99900;'),
        'fault_recovery_read_boundary_4900us': change(s, 'const RESET_READ_US: u32 = 5000;', 'const RESET_READ_US: u32 = 4900;'),
        'fault_recovery_write_boundary_9900us': change(s, 'const RESET_WRITE_US: u32 = 10000;', 'const RESET_WRITE_US: u32 = 9900;'),
        'fault_postcal_boundary_9800us': change(s, 'self.wait(State::ReadyWait,10000)', 'self.wait(State::ReadyWait,9800)'),
        'fault_negative_temperature_plus_two': change(s, 'let c=self.pending.take().unwrap();', 'let mut values=values;if values[0]<0{values[0]+=2;}let c=self.pending.take().unwrap();'),
        'fault_humidity_wrapping_callback': change(s, 'client.callback(value as usize);', 'client.callback(if value==0 {usize::MAX} else {value as usize});'),
        'fault_same_epoch_reentry_cache': reentry_cache_fault(s),
        'fault_error_cleanup_after_callback': change(s,
            'let c=self.pending.take();self.cache.set([None;2]);self.idle();\n        if c==Some(Channel::Temperature) { if let Some(client)=self.temperature.get(){client.callback(Err(error));} }',
            'let c=self.pending.get();self.cache.set([None;2]);\n        if c==Some(Channel::Temperature) { if let Some(client)=self.temperature.get(){client.callback(Err(error));} }\n        self.pending.set(None);self.idle();'),
        'fault_wire_address_as_register': change(s, 'self.i2c.read(b,n).map_err', 'b[0]=0x71;self.i2c.write_read(b,1,n).map_err'),
        'fault_skip_calibration': change(s, 'if b[0]&0x18!=0x18', 'if false'),
        'fault_only_cal_bit3': change(s, 'if b[0]&0x18!=0x18', 'if b[0]&0x08!=0x08'),
        'fault_exact_calibration_byte': change(change(s, 'if b[0]&0x18!=0x18', 'if b[0]!=0x18'), 'let okay=b[0]&0x18==0x18;', 'let okay=b[0]==0x18;'),
        'fault_restore_zero_pair': change(s, 'self.pair.set([b[1],b[2]])', 'self.pair.set([0,0])'),
        'fault_wrong_restore_command': change(s, '0xb0|REGISTERS[self.register.get()]', '0x90|REGISTERS[self.register.get()]'),
        'fault_skip_recovery_read_wait': change(s, 'const RESET_READ_US: u32 = 5000;', 'const RESET_READ_US: u32 = 0;'),
        'fault_skip_recovery_write_wait': change(s, 'const RESET_WRITE_US: u32 = 10000;', 'const RESET_WRITE_US: u32 = 0;'),
        'fault_wrong_trigger_parameter': change(s, '&[0xac,0x33,0x00]', '&[0xac,0x08,0x00]'),
        'fault_short_frame': change(s, 'self.read(b,7,State::Frame)', 'self.read(b,6,State::Frame)', 2),
        'fault_crc_initial_zero': change(s, 'let mut crc=0xff;', 'let mut crc=0x00;'),
        'fault_crc_polynomial': change(s, '(crc<<1)^0x31', '(crc<<1)^0x07'),
        'fault_crc_omits_status': change(s, 'Self::crc(&b[..6])', 'Self::crc(&b[1..6])'),
        'fault_skip_crc': change(s, 'if Self::crc(&b[..6])!=b[6]', 'if false'),
        'fault_ignore_busy': change(s, 'if b[0]&0x80!=0', 'if false', 3),
        'fault_temperature_units': change(s, 't as i64*20000/1048576-5000', 't as i64*200/1048576-50'),
        'fault_humidity_units': change(s, 'h as i64*10000/1048576', 'h as i64*100/1048576'),
        'fault_shared_temperature_nibble': change(s, '((b[3]&15) as u32)', '(b[3] as u32)'),
        'fault_shared_humidity_nibble': change(s, '((b[3] as u32)>>4)', '(b[3] as u32)'),
        'fault_temperature_unsigned': change(s, 'client.callback(Ok(value as i32))', 'client.callback(Ok(value.abs() as i32))'),
        'fault_duplicate_callback': change(s, 'self.emit(c,values[c.index()]);Ok(())', 'self.emit(c,values[c.index()]);self.emit(c,values[c.index()]);Ok(())'),
        'fault_lost_buffer': change(s, 'self.buffer.replace(b);self.idle();self.emit(c,values[c.index()]);', 'let _=b;self.idle();self.emit(c,values[c.index()]);'),
        'fault_pending_never_cleared': change(s, 'self.buffer.replace(b);self.idle();self.emit(c,values[c.index()]);', 'self.buffer.replace(b);self.emit(c,values[c.index()]);'),
        'fault_cache_survives_integrity_error': change(cached, 'let c=self.pending.take();self.cache.set([None;2]);self.idle();', 'let c=self.pending.take();self.idle();'),
        'fault_cached_channel_replayed': change(cached, 'cache[c.index()].take()', 'cache[c.index()]'),
        'fault_ignore_async_error': change(s, 'if let Err(e)=status {', 'if let Err(e)=Ok::<(),i2c::Error>(()) {'),
        'fault_unbounded_busy_wait': change(s, 'as u64*220', 'as u64*22000'),
        'fault_humidity_error_fabricated': change(s, 'if c==Some(Channel::Temperature) { if let Some(client)=self.temperature.get(){client.callback(Err(error));} }', 'if c==Some(Channel::Temperature) { if let Some(client)=self.temperature.get(){client.callback(Err(error));} } else if let Some(client)=self.humidity.get(){client.callback(0);}'),
    }


def bounded_frame_retry(s):
    """One transparent retry after an asynchronous full-frame transport failure."""
    s=change(s,'cache: Cell<[Option<i64>;2]>,','cache: Cell<[Option<i64>;2]>, retried: Cell<bool>,')
    s=change(s,'cache:Cell::new([None;2]),','cache:Cell::new([None;2]),retried:Cell::new(false),')
    s=change(s,'self.pending.set(Some(c));self.start.set(self.alarm.now());','self.pending.set(Some(c));self.retried.set(false);self.start.set(self.alarm.now());')
    return change(s,'if let Err(e)=status {self.buffer.replace(b);self.fail(e.into());return;}',
        '''if let Err(e)=status {
            if self.state.get()==State::Frame && !self.retried.replace(true) {
                self.cache.set([None;2]);
                if let Err(e)=self.read(b,7,State::Frame){self.fail(e);}
                return;
            }
            self.buffer.replace(b);self.fail(e.into());return;
        }''')


def reentry_cache_fault(s):
    """Incorrectly make the just-delivered channel cache visible during its callback."""
    s=change(s,'cache: Cell<[Option<i64>;2]>,','cache: Cell<[Option<i64>;2]>,callback_cache:Cell<Option<(Channel,i64)>>,')
    s=change(s,'cache:Cell::new([None;2]),','cache:Cell::new([None;2]),callback_cache:Cell::new(None),')
    s=change(s,'''fn emit(&self,c:Channel,value:i64) {
        match c { Channel::Temperature=>if let Some(client)=self.temperature.get(){client.callback(Ok(value as i32));},
                  Channel::Humidity=>if let Some(client)=self.humidity.get(){client.callback(value as usize);} }
    }''', '''fn emit(&self,c:Channel,value:i64) {
        self.callback_cache.set(Some((c,value)));
        match c { Channel::Temperature=>if let Some(client)=self.temperature.get(){client.callback(Ok(value as i32));},
                  Channel::Humidity=>if let Some(client)=self.humidity.get(){client.callback(value as usize);} }
        self.callback_cache.set(None);
    }''')
    return change(s,'if CACHE_OTHER {let mut cache=self.cache.get();if let Some(v)=cache[c.index()].take()',
        '''if let Some((cached_channel,v))=self.callback_cache.get(){if c==cached_channel{self.emit(c,v);return Ok(());}}
        if CACHE_OTHER {let mut cache=self.cache.get();if let Some(v)=cache[c.index()].take()''')
