"""DPS310 varied legal strategies and independently scored, compiling faults."""
def once(s,a,b):
    if s.count(a)!=1:raise AssertionError(f'Expected one control anchor: {a!r}')
    return s.replace(a,b,1)

def cached(s):
    return once(s,'if b[0]&7==0{self.start_temperature(b)}',
        'if b[0]&7==0{if self.channel.get()==Some(true)&&self.raw_temperature.get().is_some()&&self.alarm.now().into_u32().wrapping_sub(self.temp_started.get())<A::Frequency::frequency(){self.start_pressure(b)}else{self.start_temperature(b)}}')

def burst_configuration(s):
    return once(s,'self.temp_source.set(b[0]&0x80);self.write(b,0x06,0x03,State::PressureCfg)',
        '''self.temp_source.set(b[0]&0x80);let source=self.temp_source.get();
                b[..5].copy_from_slice(&[6,3,source|3,0,0]);self.state.set(State::CommonCfg);self.i2c.enable();
                match self.i2c.write(b,5){Ok(())=>Ok(()),Err((e,b))=>{self.buffer.replace(b);Err(e.into())}}''')

def chunks(s):
    s=once(s,'raw_temperature:Cell<Option<i32>>,temp_started:Cell<u32>,','raw_temperature:Cell<Option<i32>>,temp_started:Cell<u32>,trims:Cell<[u8;18]>,trim_offset:Cell<usize>,')
    s=once(s,'raw_temperature:Cell::new(None),temp_started:Cell::new(0),','raw_temperature:Cell::new(None),temp_started:Cell::new(0),trims:Cell::new([0;18]),trim_offset:Cell::new(0),')
    s=once(s,'self.read(b,0x10,18,State::Calibration)','self.trim_offset.set(0);self.read(b,0x10,6,State::Calibration)')
    return once(s,'State::Calibration=>{self.parse_calibration(b);self.read(b,0x28,1,State::Source)}',
        '''State::Calibration=>{let i=self.trim_offset.get();let mut trims=self.trims.get();trims[i..i+6].copy_from_slice(&b[..6]);self.trims.set(trims);
                if i<12{self.trim_offset.set(i+6);self.read(b,0x10+(i+6) as u8,6,State::Calibration)}else{self.parse_calibration(&trims);self.read(b,0x28,1,State::Source)}}''')

def pointer(s):
    s=once(s,'enum State {Off,','enum State {ReadPointer,Off,')
    s=once(s,'raw_temperature:Cell<Option<i32>>,temp_started:Cell<u32>,','raw_temperature:Cell<Option<i32>>,temp_started:Cell<u32>,read_state:Cell<State>,read_len:Cell<usize>,')
    s=once(s,'raw_temperature:Cell::new(None),temp_started:Cell::new(0),','raw_temperature:Cell::new(None),temp_started:Cell::new(0),read_state:Cell::new(State::Off),read_len:Cell::new(0),')
    s=once(s,'b[0]=reg;self.state.set(state);self.i2c.enable();\n        match self.i2c.write_read(b,1,len)',
        'b[0]=reg;self.read_state.set(state);self.read_len.set(len);self.state.set(State::ReadPointer);self.i2c.enable();\n        match self.i2c.write(b,1)')
    return once(s,'            State::Identity=>',
        '''            State::ReadPointer=>{self.state.set(self.read_state.get());match self.i2c.read(b,self.read_len.get()){Ok(())=>Ok(()),Err((e,b))=>{self.buffer.replace(b);Err(e.into())}}}
            State::Identity=>''')

def correction_bursts(s):
    s=once(s,'State::CommonCfg=>self.write(b,0x0e,0xa5,State::UnlockA),',
        '''State::CommonCfg=>{b[..3].copy_from_slice(&[0x0e,0xa5,0x96]);self.state.set(State::UnlockB);self.i2c.enable();match self.i2c.write(b,3){Ok(())=>Ok(()),Err((e,b))=>{self.buffer.replace(b);Err(e.into())}}},''')
    return once(s,'State::Fix=>self.write(b,0x0e,0x00,State::LockA),',
        '''State::Fix=>{b[..3].copy_from_slice(&[0x0e,0,0]);self.state.set(State::LockB);self.i2c.enable();match self.i2c.write(b,3){Ok(())=>Ok(()),Err((e,b))=>{self.buffer.replace(b);Err(e.into())}}},''')

def pressure_first(s):
    s=once(s,'raw_temperature:Cell<Option<i32>>,temp_started:Cell<u32>,','raw_temperature:Cell<Option<i32>>,temp_started:Cell<u32>,raw_pressure:Cell<i32>,')
    s=once(s,'raw_temperature:Cell::new(None),temp_started:Cell::new(0),','raw_temperature:Cell::new(None),temp_started:Cell::new(0),raw_pressure:Cell::new(0),')
    s=once(s,'if b[0]&7==0{self.start_temperature(b)}','if b[0]&7==0{if self.channel.get()==Some(true){self.start_pressure(b)}else{self.start_temperature(b)}}')
    s=once(s,'if self.channel.get()==Some(true){self.start_pressure(b)}else{let value=self.temperature_value(raw);',
        'if self.channel.get()==Some(true){let value=self.pressure_value(self.raw_pressure.get(),raw);self.buffer.replace(b);self.finish(Ok(value));return;}else{let value=self.temperature_value(raw);')
    return once(s,'State::PressureData=>{let value=self.pressure_value(Self::raw(b),self.raw_temperature.get().unwrap());self.buffer.replace(b);self.finish(Ok(value));return;}',
        'State::PressureData=>{self.raw_pressure.set(Self::raw(b));self.start_temperature(b)}')

def reset(s):
    s=once(s,'enum State {Off,','enum State {Reset,ResetWait,Off,')
    s=once(s,'            State::Identity=>','            State::Reset=>{self.wait(b,State::ResetWait,3_000);return;}\n            State::Identity=>')
    return once(s,'State::Por=>self.read(b,0x0d,1,State::Identity),','State::Por=>self.write(b,0x0c,9,State::Reset),State::ResetWait=>self.read(b,0x0d,1,State::Identity),')

def legal_controls(s):
    burst=s.replace('self.read(b,0x03,3,State::TempData)','self.read(b,0,6,State::TempData)').replace('let raw=Self::raw(b);','let raw=Self::raw(&b[3..]);').replace('self.read(b,0x00,3,State::PressureData)','self.read(b,0,6,State::PressureData)')
    cache=cached(s)
    return {'legal_cached_temperature':cache,'legal_burst_configuration':burst_configuration(s),'legal_chunked_calibration':chunks(s),
        'legal_stop_pointer_reads':pointer(s),'legal_full_data_bursts':burst,'legal_paired_unlock_relock':correction_bursts(s),
        'legal_inactive_rate_polarity':s.replace('b,0x06,0x03,State::PressureCfg','b,0x06,0x73,State::PressureCfg').replace('self.temp_source.get()|0x03','self.temp_source.get()|0x73').replace('b,0x09,0x00,State::CommonCfg','b,0x09,0x80,State::CommonCfg'),
        'legal_two_ms_poll':s.replace('POLL_US:u32=1_000','POLL_US:u32=2_000').replace('MAX_POLLS:u32=50','MAX_POLLS:u32=25'),
        'legal_pressure_before_temperature':pressure_first(s),'legal_optional_soft_reset':reset(s),
        'legal_startup_temperature_prefetch':once(cache,'State::LockB=>{self.initialized.set(true);self.buffer.replace(b);self.idle();return;}',
            'State::LockB=>{self.initialized.set(true);self.start_temperature(b)}')}

def fault_controls(s):
    pairs={
        'unsigned_coefficients':('let signed=|v:i32,n|((v<<(32-n))>>(32-n)) as f64;','let signed=|v:i32,_n:i32|v as f64;'),
        'unsigned_c0':('signed((b[0] as i32)*16+(b[1]>>4) as i32,12)','((b[0] as u32)*16+(b[1]>>4) as u32) as f64'),
        'wrong_shared_nibble_c1':('((b[1]&15) as i32)*256','((b[1]>>4) as i32)*256'),
        'wrong_shared_nibble_c10':('((b[5]&15) as i32)*65536','((b[5]>>4) as i32)*65536'),
        'little_endian_16bit_trim':('i16::from_be_bytes','i16::from_le_bytes'),
        'unsigned_adc':('(v<<8)>>8','v'),
        'little_endian_adc':('((b[0] as i32)<<16)|((b[1] as i32)<<8)|b[2] as i32','((b[2] as i32)<<16)|((b[1] as i32)<<8)|b[0] as i32'),
        'temperature_wrong_units':('))*100.0) as i64','))*10.0) as i64'),
        'pressure_wrong_units':('(value/100.0) as i64','value as i64'),
        'omit_cubic_pressure':('p*c[8]','p*0.0'),
        'omit_quadratic_cross':('p*c[7]','p*0.0'),
        'ignore_pressure_temperature':('let t=t as f64/7864320.0;','let t=0.0;'),
        'wrong_temperature_source':('self.temp_source.set(b[0]&0x80)','self.temp_source.set(0x80)'),
        'wrong_oversampling':('b,0x06,0x03,State::PressureCfg','b,0x06,0x04,State::PressureCfg'),
        'unnecessary_shift':('b,0x09,0x00,State::CommonCfg','b,0x09,0x0c,State::CommonCfg'),
        'background_mode':('b,0x08,0x01,State::PressureCommand','b,0x08,0x07,State::PressureCommand'),
        'wrong_correction':('b,0x62,0x02,State::Fix','b,0x62,0x00,State::Fix'),
        'omit_relock':('State::Fix=>self.write(b,0x0e,0x00,State::LockA),','State::Fix=>{self.initialized.set(true);self.buffer.replace(b);self.idle();return;}'),
        'ignore_identity':('if b[0]!=0x10','if false'),
        'ignore_coefficient_ready':('b[0]&0xc0==0xc0','b[0]&0x40==0x40'),
        'ignore_sensor_ready':('b[0]&0xc0==0xc0','b[0]&0x80==0x80'),
        'skip_temperature_ready':('if b[0]&0x20!=0','if true'),
        'skip_pressure_ready':('if b[0]&0x10!=0','if true'),
        'unbounded_polling':('MAX_POLLS:u32=50','MAX_POLLS:u32=1000'),
        'duplicate_temperature_callback':('c.callback(value.map(|v|v as i32));','c.callback(value.map(|v|v as i32));c.callback(value.map(|v|v as i32));'),
        'omit_accepted_error_callback':('let channel=self.channel.take();self.idle();','let channel=self.channel.take().filter(|_|value.is_ok());self.idle();'),
        'ignore_initialization_bus_errors':('if let Err(e)=status{self.buffer.replace(b);self.finish(Err(e.into()));return;}',
            'if let Err(e)=status{if self.initialized.get(){self.buffer.replace(b);self.finish(Err(e.into()));return;}}'),
    }
    result={name:once(s,a,b) for name,(a,b) in pairs.items()}
    result['wrong_8x_scale']=s.replace('7864320.0','524288.0')
    result['lose_immediate_error_buffer']=s.replace('self.buffer.replace(b);Err(e.into())','let _=b;Err(e.into())')
    result['cache_without_age_bound']=once(cached(s),'&&self.alarm.now().into_u32().wrapping_sub(self.temp_started.get())<A::Frequency::frequency()','')
    result['cache_survives_failed_temperature']=once(cached(s),'if value.is_err(){self.raw_temperature.set(None);}','')
    return result
