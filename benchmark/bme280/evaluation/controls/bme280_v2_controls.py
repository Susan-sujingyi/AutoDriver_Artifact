"""Host-only semantic controls; never generation materials or method results."""
from __future__ import annotations


def replace(source, old, new, count=1):
    if old not in source: raise AssertionError('Control anchor missing: '+old[:80])
    return source.replace(old,new,count)


def lazy_calibration(source):
    source=replace(source,'    initialized: Cell<bool>,','    initialized: Cell<bool>,\n    calibration_loaded: Cell<bool>,')
    source=replace(source,'            initialized: Cell::new(false),','            initialized: Cell::new(false),\n            calibration_loaded: Cell::new(false),')
    source=replace(source,'let result = self.write_register(buffer, 0xf4, 0x35, State::Trigger);',
                   'let result = if self.calibration_loaded.get() {self.write_register(buffer, 0xf4, 0x35, State::Trigger)} else {self.read_register(buffer,0x88,26,State::CalibrationTp)};')
    source=replace(source,'                self.write_register(buffer, 0xf5, 0, State::Config)',
                   '                self.calibration_loaded.set(true);\n                self.write_register(buffer, 0xf4, 0x35, State::Trigger)')
    source=replace(source,'    fn alarm(&self) {','''    fn alarm(&self) {
        if self.state.get()==State::ResetWait {
            if let Some(buffer)=self.buffer.take() {
                if let Err(error)=self.write_register(buffer,0xf5,0,State::Config){self.fail_accepted(error);}
            }
            return;
        }''')
    return source


def split_sample(source):
    source=replace(source,'    Sample,','    SampleTp,\n    Sample,')
    source=replace(source,'    initialized: Cell<bool>,','    initialized: Cell<bool>,\n    sample_prefix: Cell<[u8;6]>,')
    source=replace(source,'            initialized: Cell::new(false),','            initialized: Cell::new(false),\n            sample_prefix: Cell::new([0;6]),')
    source=replace(source,'self.read_register(buffer, 0xf7, 8, State::Sample)','self.read_register(buffer, 0xf7, 6, State::SampleTp)')
    source=replace(source,'            State::Sample => {','''            State::SampleTp => {
                let mut prefix=[0u8;6];prefix.copy_from_slice(&buffer[..6]);self.sample_prefix.set(prefix);
                self.read_register(buffer,0xfd,2,State::Sample)
            }
            State::Sample => {
                let h=[buffer[0],buffer[1]];buffer[..6].copy_from_slice(&self.sample_prefix.get());
                buffer[6]=h[0];buffer[7]=h[1];''')
    return source


def channel_cache(source):
    source=replace(source,'    initialized: Cell<bool>,','''    initialized: Cell<bool>,
    sample_cache: Cell<Option<[u8;8]>>,
    cache_served: Cell<u8>,
    cache_replay: Cell<bool>,''')
    source=replace(source,'            initialized: Cell::new(false),','''            initialized: Cell::new(false),
            sample_cache: Cell::new(None),cache_served: Cell::new(0),cache_replay: Cell::new(false),''')
    source=replace(source,'        self.pending.set(Some(channel));','''        self.pending.set(Some(channel));
        let bit=match channel {Channel::Temperature=>1,Channel::Humidity=>2,Channel::Pressure=>4};
        if self.cache_served.get()&bit==0 {
            if let Some(bytes)=self.sample_cache.get() {
                buffer[..8].copy_from_slice(&bytes);self.cache_replay.set(true);self.finish_sample(buffer);return Ok(());
            }
        }''')
    source=replace(source,'    fn finish_sample(&self, buffer:', '    fn finish_sample(&self, buffer:') # signature guarded below
    anchor='        let raw_pressure ='
    source=replace(source,anchor,'''        if !self.cache_replay.replace(false) {
            let mut bytes=[0u8;8];bytes.copy_from_slice(&buffer[..8]);self.sample_cache.set(Some(bytes));self.cache_served.set(0);
        }
        if let Some(channel)=self.pending.get() {
            let bit=match channel {Channel::Temperature=>1,Channel::Humidity=>2,Channel::Pressure=>4};
            self.cache_served.set(self.cache_served.get()|bit);
        }
'''+anchor)
    return source


def float_temperature_pressure(source):
    start=source.index('    fn temperature(');end=source.index('    fn humidity(',start)
    return source[:start]+'''    fn temperature(c: Calibration, raw: i64) -> (i32, i64) {
        let a=(raw as f64/16384.0-c.t1 as f64/1024.0)*c.t2 as f64;
        let b=raw as f64/131072.0-c.t1 as f64/8192.0;
        let fine=a+b*b*c.t3 as f64;
        ((fine/5120.0*100.0) as i32,fine as i64)
    }
    fn pressure(c: Calibration, raw: i64, fine: i64) -> Result<u32, ErrorCode> {
        let x=fine as f64/2.0-64000.0;
        let y=(x*x*c.p6 as f64/32768.0+x*c.p5 as f64*2.0)/4.0+c.p4 as f64*65536.0;
        let d=(1.0+(c.p3 as f64*x*x/524288.0+c.p2 as f64*x)/524288.0/32768.0)*c.p1 as f64;
        if d==0.0{return Err(ErrorCode::FAIL);}
        let p=(1048576.0-raw as f64-y/4096.0)*6250.0/d;
        let p=p+(c.p9 as f64*p*p/2147483648.0+p*c.p8 as f64/32768.0+c.p7 as f64)/16.0;
        Ok((p/100.0) as u32)
    }

'''+source[end:]


def pressure32(source):
    start=source.index('    fn pressure(');end=source.index('    fn humidity(',start)
    return source[:start]+'''    fn pressure(c: Calibration, raw: i64, fine: i64) -> Result<u32, ErrorCode> {
        let mut a=(fine as i32>>1)-64000;
        let mut b=(((a>>2)*(a>>2))>>11)*c.p6 as i32;
        b+= (a*c.p5 as i32)<<1;b=(b>>2)+((c.p4 as i32)<<16);
        a=(((c.p3 as i32*(((a>>2)*(a>>2))>>13))>>3)+((c.p2 as i32*a)>>1))>>18;
        a=((32768+a)*c.p1 as i32)>>15;
        if a==0{return Err(ErrorCode::FAIL);}
        let mut p=((1048576-raw as i32)-(b>>12)) as u32*3125;
        p=if p<0x80000000 {(p<<1)/a as u32} else {(p/a as u32)*2};
        a=(c.p9 as i32*(((p>>3)*(p>>3))>>13) as i32)>>12;
        b=((p>>2) as i32*c.p8 as i32)>>13;
        p=(p as i32+((a+b+c.p7 as i32)>>4)) as u32;
        Ok(p/100)
    }

'''+source[end:]


def legal_controls(source):
    from bme280_controls import batched_trigger, calibration_during_initial_conversion
    return {
        'documented_double_temperature_pressure':float_temperature_pressure(source),
        'documented_32bit_pressure':pressure32(source),
        'lazy_calibration':lazy_calibration(source),
        'synchronized_split_read':split_sample(source),
        'cross_channel_cache':channel_cache(source),
        'startup_measurement':calibration_during_initial_conversion(source),
        'bosch_address_value_pairs':batched_trigger(source,legal=True),
        'fixed_maximum_wait_without_status_poll':replace(source,'(0xf3, 1, State::ConversionStatus)','(0xf7, 8, State::Sample)'),
        'no_identity_probe':replace(source,'let result = self.read_register(buffer, 0xd0, 1, State::Id);',
                                     'let result = self.write_register(buffer, 0xe0, 0xb6, State::Reset);'),
        'delayed_initialization':replace(source,'State::ResetWait, 2)','State::ResetWait, 200)'),
        'different_busy_error':source.replace('ErrorCode::BUSY','ErrorCode::NOMEM'),
    }


def fault_controls(source):
    changes={
        'wrong_pressure_unit':('let hpa = pressure / 25600;','let hpa = pressure / 256;'),
        'unsigned_humidity_trim':('c.h4 = ((h4 << 4) as i16) >> 4;','c.h4 = h4 as i16;'),
        'wrong_physical_oversampling':('0xf4, 0x35, State::Trigger','0xf4, 0x31, State::Trigger'),
        'duplicate_temperature':('client.callback(Ok(temperature));','client.callback(Ok(temperature)); client.callback(Ok(temperature));'),
        'missing_temperature':('client.callback(Ok(temperature));','let _ = (client,temperature);'),
        'fabricated_temperature':('client.callback(Ok(temperature));','client.callback(Ok(0));'),
        'short_frame':('self.read_register(buffer, 0xf7, 8, State::Sample)','self.read_register(buffer, 0xf7, 7, State::Sample)'),
        'no_conversion':('0xf4, 0x35, State::Trigger','0xf4, 0x34, State::Trigger'),
    }
    result={name:(replace(source,old,new),'not_completed' if name=='missing_temperature' else 'fail')
            for name,(old,new) in changes.items()}
    result['permanent_sample_cache']=(channel_cache(source).replace('self.cache_served.set(self.cache_served.get()|bit);','let _=bit;'),'not_completed')
    return result
