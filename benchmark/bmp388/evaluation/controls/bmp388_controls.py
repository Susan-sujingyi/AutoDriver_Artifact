"""Independent BMP388 legal implementations and compiling fault controls."""
def once(source,old,new):
    if source.count(old)!=1:raise AssertionError(f'Expected one BMP388 control anchor: {old!r}')
    return source.replace(old,new,1)


def full_burst(source):
    source=once(source,'let (reg,len)=if pressure {(0x04,6)} else {(0x07,3)};','let (reg,len)=(0x04,6);')
    return once(source,'let offset=if pressure {3} else {0};','let offset=3;')


def pointer_reads(source):
    source=once(source,'const MAX_POLLS: u32 = 80;','const MAX_POLLS: u32 = 60;')
    source=once(source,'enum State { Off,','enum State { ReadPointer, Off,')
    source=once(source,'    coefficients: Cell<[f64;14]>,','    read_next:Cell<State>,read_len:Cell<usize>,\n    coefficients: Cell<[f64;14]>,')
    source=once(source,'            coefficients:Cell::new([0.0;14]),','            read_next:Cell::new(State::Off),read_len:Cell::new(0),coefficients:Cell::new([0.0;14]),')
    source=once(source,'buffer[0]=reg;self.state.set(next);self.i2c.enable();\n        match self.i2c.write_read(buffer,1,len)',
        'buffer[0]=reg;self.read_next.set(next);self.read_len.set(len);self.state.set(State::ReadPointer);self.i2c.enable();\n        match self.i2c.write(buffer,1)')
    return once(source,'            State::CommandReady=>{', '''            State::ReadPointer=>{
                self.state.set(self.read_next.get());
                match self.i2c.read(buffer,self.read_len.get()) {
                    Ok(())=>Ok(()),Err((e,b))=>{self.buffer.replace(b);Err(e.into())}
                }
            }
            State::CommandReady=>{''')


def paired_configuration(source):
    return once(source,'State::Calibration=>{self.parse_calibration(buffer);self.write(buffer,0x1d,0x03,State::Odr)}',
        '''State::Calibration=>{
                self.parse_calibration(buffer);
                buffer[..8].copy_from_slice(&[0x1d,0x03,0x1c,0x02,0x1f,0x00,0x1b,0x33]);
                self.state.set(State::Mode);self.i2c.enable();
                match self.i2c.write(buffer,8) {
                    Ok(())=>Ok(()),Err((e,b))=>{self.buffer.replace(b);Err(e.into())}
                }
            }''')


def calibration_chunks(source):
    source=once(source,'    coefficients: Cell<[f64;14]>,','    trims:Cell<[u8;21]>,trim_offset:Cell<usize>,\n    coefficients: Cell<[f64;14]>,')
    source=once(source,'            coefficients:Cell::new([0.0;14]),','            trims:Cell::new([0;21]),trim_offset:Cell::new(0),coefficients:Cell::new([0.0;14]),')
    source=once(source,'self.read(buffer,0x31,21,State::Calibration)','self.trim_offset.set(0);self.read(buffer,0x31,7,State::Calibration)')
    return once(source,'State::Calibration=>{self.parse_calibration(buffer);self.write(buffer,0x1d,0x03,State::Odr)}',
        '''State::Calibration=>{
                let start=self.trim_offset.get();let mut trims=self.trims.get();
                trims[start..start+7].copy_from_slice(&buffer[..7]);self.trims.set(trims);
                self.trim_offset.set(start+7);
                if start+7<21 {self.read(buffer,0x31+(start+7) as u8,7,State::Calibration)}
                else {self.parse_calibration(&trims);self.write(buffer,0x1d,0x03,State::Odr)}
            }''')


def status_data_burst(source):
    source=once(source,'self.read(buffer,0x03,1,State::Status)','self.read(buffer,0x03,7,State::Status)')
    source=once(source,'let offset=if pressure {3} else {0};','let offset=3;')
    return once(source,'''                    let (reg,len)=if pressure {(0x04,6)} else {(0x07,3)};
                    self.read(buffer,reg,len,State::Sample)''', '''                    let value=self.decoded(&buffer[1..],pressure);
                    self.buffer.replace(buffer);self.finish(Ok(value));return;''')


def shared_epoch_cache(source):
    source=full_burst(source)
    source=once(source,'enum State { Off,','enum State { Cached, Off,')
    source=once(source,'    coefficients: Cell<[f64;14]>,','    cached:Cell<Option<(bool,i64)>>,\n    coefficients: Cell<[f64;14]>,')
    source=once(source,'            coefficients:Cell::new([0.0;14]),','            cached:Cell::new(None),coefficients:Cell::new([0.0;14]),')
    source=once(source,'        let channel=self.channel.take();self.idle();','        if value.is_err(){self.cached.set(None);}\n        let channel=self.channel.take();self.idle();')
    source=once(source,'        self.channel.set(Some(pressure));self.polls.set(0);', '''        if self.cached.get().map(|(ch,_)|ch)==Some(pressure) {
            self.channel.set(Some(pressure));self.wait(buffer,State::Cached,1);return Ok(());
        }
        self.channel.set(Some(pressure));self.polls.set(0);''')
    source=once(source,'                let value=self.decoded(buffer,self.channel.get()==Some(true));', '''                let pressure=self.channel.get()==Some(true);
                let other=self.decoded(buffer,!pressure);self.cached.set(Some((!pressure,other)));
                let value=self.decoded(buffer,pressure);''')
    return once(source,'            State::Por|State::CommandWait=>', '''            State::Cached=>{
                let (_,value)=self.cached.take().unwrap();self.buffer.replace(buffer);self.finish(Ok(value));return;
            }
            State::Por|State::CommandWait=>''')


def unsynchronized_split(source):
    source=once(source,'enum State { Off,','enum State { SplitTemperature, Off,')
    source=once(source,'    coefficients: Cell<[f64;14]>,','    partial_pressure:Cell<[u8;3]>,\n    coefficients: Cell<[f64;14]>,')
    source=once(source,'            coefficients:Cell::new([0.0;14]),','            partial_pressure:Cell::new([0;3]),coefficients:Cell::new([0.0;14]),')
    source=once(source,'let (reg,len)=if pressure {(0x04,6)} else {(0x07,3)};','let (reg,len)=if pressure {(0x04,3)} else {(0x07,3)};')
    return once(source,'''            State::Sample=>{
                let value=self.decoded(buffer,self.channel.get()==Some(true));''', '''            State::SplitTemperature=>{
                buffer.copy_within(0..3,3);buffer[..3].copy_from_slice(&self.partial_pressure.get());
                let value=self.decoded(buffer,true);self.buffer.replace(buffer);self.finish(Ok(value));return;
            }
            State::Sample=>{
                if self.channel.get()==Some(true) {
                    self.partial_pressure.set([buffer[0],buffer[1],buffer[2]]);
                    if let Err(e)=self.read(buffer,0x07,3,State::SplitTemperature){self.finish(Err(e));}
                    return;
                }
                let value=self.decoded(buffer,self.channel.get()==Some(true));''')


def legal_controls(source):
    return {'legal_stable_power_no_extra_wait':once(source,
        'self.polls.set(0);self.wait(buffer,State::Por,2_000);Ok(())',
        'self.polls.set(0);self.read(buffer,0x03,1,State::CommandReady)'),
        'legal_full_six_byte_temperature':full_burst(source),
        'legal_stop_pointer_reads':pointer_reads(source),
        'legal_paired_configuration':paired_configuration(source),
        'legal_chunked_calibration':calibration_chunks(source),
        'legal_status_and_data_burst':status_data_burst(source),
        'legal_shared_epoch_cache':shared_epoch_cache(source),
        'legal_two_ms_poll':source.replace('POLL_US: u32 = 1_000;','POLL_US: u32 = 2_000;').replace('MAX_POLLS: u32 = 80;','MAX_POLLS: u32 = 40;')}


def fault_controls(source):
    pairs={
        'signed_unsigned_t1':('u16le(0)*256.0','i16le(0)*256.0'),
        'unsigned_signed_p1':('(i16le(5)-16384.0)','(u16le(5)-16384.0)'),
        'unsigned_signed_t3':('i8val(4)/281474976710656.0','b[4] as f64/281474976710656.0'),
        'omit_p2_bias':('(i16le(7)-16384.0)','i16le(7)'),
        'wrong_p5_scale':('u16le(11)*8.0','u16le(11)/8.0'),
        'wrong_p11_exponent':('i8val(20)/36893488147419103232.0','i8val(20)/281474976710656.0'),
        'temperature_raw_big_endian':('b[offset] as u32+256*b[offset+1] as u32+65536*b[offset+2] as u32','65536*b[offset] as u32+256*b[offset+1] as u32+b[offset+2] as u32'),
        'signed_temperature_adc':('let d=ut-q[0];','let ut=((ut as u32 as i32)<<8>>8) as f64;let d=ut-q[0];'),
        'temperature_wrong_units':('(t*100.0) as i64','(t*10.0) as i64'),
        'pressure_wrong_units':('(p/100.0) as i64','p as i64'),
        'pressure_ignores_temperature':('if !pressure {return (t*100.0) as i64;}','if !pressure {return (t*100.0) as i64;}let t=0.0;'),
        'pressure_without_temperature_bytes':('if pressure {(0x04,6)}','if pressure {(0x04,3)}'),
        'skip_data_ready':('if buffer[0]&mask!=0 {','if true {'),
        'reserved_power_bit':('buffer,0x1b,0x33,State::Mode','buffer,0x1b,0xb3,State::Mode'),
        'wrong_mode_forced':('buffer,0x1b,0x33,State::Mode','buffer,0x1b,0x13,State::Mode'),
        'wrong_pressure_osr':('buffer,0x1c,0x02,State::Osr','buffer,0x1c,0x04,State::Osr'),
        'wrong_odr':('buffer,0x1d,0x03,State::Odr','buffer,0x1d,0x04,State::Odr'),
        'enable_iir':('buffer,0x1f,0x00,State::Filter','buffer,0x1f,0x02,State::Filter'),
        'ignore_identity':('if buffer[0]!=0x50 {','if false {'),
        'ignore_command_ready':('if buffer[0]&0x10!=0 {','if true {'),
        'ignore_runtime_errors':('if buffer[0]&7!=0 {','if false {'),
        'ignore_initialization_errors':('if buffer[0]&7==0 {','if true {'),
        'skip_reset_wait':('State::ResetWait,2_000','State::ResetWait,0'),
        'duplicate_temperature_callback':('c.callback(value.map(|v|v as i32));','c.callback(value.map(|v|v as i32));c.callback(value.map(|v|v as i32));'),
        'omit_accepted_error_callback':('let channel=self.channel.take();self.idle();','let channel=self.channel.take().filter(|_|value.is_ok());self.idle();'),
        'unbounded_polling':('const MAX_POLLS: u32 = 80;','const MAX_POLLS: u32 = 1000;'),
    }
    result={name:once(source,old,new) for name,(old,new) in pairs.items()}
    result['lose_immediate_error_buffer']=source.replace('Err((e,b))=>{self.buffer.replace(b);Err(e.into())}','Err((e,b))=>{let _=b;Err(e.into())}')
    result['cache_survives_known_error']=once(shared_epoch_cache(source),'if value.is_err(){self.cached.set(None);}','')
    result['torn_separate_data_transactions']=unsynchronized_split(source)
    batched=paired_configuration(source)
    result['incorrect_auto_increment_write']=batched.replace('buffer[..8].copy_from_slice(&[0x1d,0x03,0x1c,0x02,0x1f,0x00,0x1b,0x33]);','buffer[..5].copy_from_slice(&[0x1b,0x33,0x02,0x03,0x00]);').replace('self.i2c.write(buffer,8)','self.i2c.write(buffer,5)')
    return result


def capability_controls(source):
    """Legal optional accesses with incomplete models, never defect controls."""
    clock = once(source, 'enum State { Off,', 'enum State { SensorClock, Off,')
    clock = once(clock, 'self.polls.set(0);self.wait(buffer,State::Por,2_000);Ok(())',
                 'self.polls.set(0);self.read(buffer,0x0c,3,State::SensorClock)')
    clock = once(clock, '            State::CommandReady=>{',
                 '            State::SensorClock=>self.read(buffer,0x03,1,State::CommandReady),\n'
                 '            State::CommandReady=>{')
    return {'unsupported_sensor_clock': clock}
