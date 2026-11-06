// SPDX-License-Identifier: Apache-2.0
//! Evaluator-only DPS310 reference; independent oracle uses exact fractions.
use core::cell::Cell;
use kernel::hil::i2c::{self,I2CClient,I2CDevice};
use kernel::hil::sensors::{TemperatureClient,TemperatureDriver,PressureClient,PressureDriver};
use kernel::hil::time::{Alarm,AlarmClient,Frequency,Ticks};
use kernel::utilities::cells::TakeCell;
use kernel::ErrorCode;
pub const BUFFER_SIZE:usize=18;
const POLL_US:u32=1_000;
const MAX_POLLS:u32=50;
#[derive(Clone,Copy,PartialEq)]
enum State {Off,Por,Identity,InitStatus,InitWait,Calibration,Source,PressureCfg,TemperatureCfg,
    CommonCfg,UnlockA,UnlockB,Fix,LockA,LockB,Ready,Before,BeforeWait,TempCommand,TempStatus,
    TempWait,TempData,PressureCommand,PressureStatus,PressureWait,PressureData}
pub struct Dps310<'a,I:I2CDevice,A:Alarm<'a>> {
    i2c:&'a I,alarm:&'a A,buffer:TakeCell<'static,[u8]>,state:Cell<State>,initialized:Cell<bool>,
    channel:Cell<Option<bool>>,polls:Cell<u32>,coefficients:Cell<[f64;9]>,temp_source:Cell<u8>,
    raw_temperature:Cell<Option<i32>>,temp_started:Cell<u32>,
    temperature:Cell<Option<&'a dyn TemperatureClient>>,pressure:Cell<Option<&'a dyn PressureClient>>,
}
impl<'a,I:I2CDevice,A:Alarm<'a>> Dps310<'a,I,A> {
    pub fn new(i2c:&'a I,alarm:&'a A,buffer:&'static mut [u8])->Self {
        Self{i2c,alarm,buffer:TakeCell::new(buffer),state:Cell::new(State::Off),initialized:Cell::new(false),
            channel:Cell::new(None),polls:Cell::new(0),coefficients:Cell::new([0.0;9]),temp_source:Cell::new(0),
            raw_temperature:Cell::new(None),temp_started:Cell::new(0),temperature:Cell::new(None),pressure:Cell::new(None)}
    }
    pub fn startup(&self)->Result<(),ErrorCode> {
        match self.state.get(){State::Ready=>return Ok(()),State::Off=>(),_=>return Err(ErrorCode::BUSY)}
        let b=self.buffer.take().ok_or(ErrorCode::BUSY)?;
        if b.len()<BUFFER_SIZE{self.buffer.replace(b);return Err(ErrorCode::SIZE);}
        self.polls.set(0);self.raw_temperature.set(None);self.wait(b,State::Por,3_000);Ok(())
    }
    fn idle(&self){let _=self.alarm.disarm();self.i2c.disable();self.state.set(if self.initialized.get(){State::Ready}else{State::Off});}
    fn finish(&self,value:Result<i64,ErrorCode>){
        if value.is_err(){self.raw_temperature.set(None);}
        let channel=self.channel.take();self.idle();
        match channel {
            Some(false)=>if let Some(c)=self.temperature.get(){c.callback(value.map(|v|v as i32));},
            Some(true)=>if let Some(c)=self.pressure.get(){c.callback(value.map(|v|v as u32));},
            None=>(),
        }
    }
    fn read(&self,b:&'static mut [u8],reg:u8,len:usize,state:State)->Result<(),ErrorCode>{
        b[0]=reg;self.state.set(state);self.i2c.enable();
        match self.i2c.write_read(b,1,len){Ok(())=>Ok(()),Err((e,b))=>{self.buffer.replace(b);Err(e.into())}}
    }
    fn write(&self,b:&'static mut [u8],reg:u8,value:u8,state:State)->Result<(),ErrorCode>{
        b[0]=reg;b[1]=value;self.state.set(state);self.i2c.enable();
        match self.i2c.write(b,2){Ok(())=>Ok(()),Err((e,b))=>{self.buffer.replace(b);Err(e.into())}}
    }
    fn wait(&self,b:&'static mut [u8],state:State,us:u32){
        self.buffer.replace(b);self.i2c.disable();self.state.set(state);
        let ticks=(A::Frequency::frequency() as u64*us as u64+999_999)/1_000_000;
        self.alarm.set_alarm(self.alarm.now(),A::Ticks::from_or_max(ticks).max(self.alarm.minimum_dt()));
    }
    fn retry(&self,b:&'static mut [u8],state:State){
        if self.polls.get()<MAX_POLLS{self.polls.set(self.polls.get()+1);self.wait(b,state,POLL_US);}
        else{self.buffer.replace(b);self.finish(Err(ErrorCode::FAIL));}
    }
    fn request(&self,pressure:bool)->Result<(),ErrorCode>{
        if self.state.get()!=State::Ready{return Err(if self.state.get()==State::Off{ErrorCode::OFF}else{ErrorCode::BUSY});}
        let b=self.buffer.take().ok_or(ErrorCode::BUSY)?;self.channel.set(Some(pressure));self.polls.set(0);
        let result=self.read(b,0x08,1,State::Before);
        if result.is_err(){self.raw_temperature.set(None);self.channel.set(None);self.idle();}result
    }
    fn start_temperature(&self,b:&'static mut [u8])->Result<(),ErrorCode>{
        self.temp_started.set(self.alarm.now().into_u32());self.polls.set(0);
        self.write(b,0x08,0x02,State::TempCommand)
    }
    fn start_pressure(&self,b:&'static mut [u8])->Result<(),ErrorCode>{self.polls.set(0);self.write(b,0x08,0x01,State::PressureCommand)}
    fn parse_calibration(&self,b:&[u8]){
        let signed=|v:i32,n|((v<<(32-n))>>(32-n)) as f64;
        let be16=|i|i16::from_be_bytes([b[i],b[i+1]]) as f64;
        self.coefficients.set([signed((b[0] as i32)*16+(b[1]>>4) as i32,12),
            signed(((b[1]&15) as i32)*256+b[2] as i32,12),
            signed((b[3] as i32)*4096+(b[4] as i32)*16+(b[5]>>4) as i32,20),
            signed(((b[5]&15) as i32)*65536+(b[6] as i32)*256+b[7] as i32,20),
            be16(8),be16(10),be16(12),be16(14),be16(16)]);
    }
    fn raw(b:&[u8])->i32{let v=((b[0] as i32)<<16)|((b[1] as i32)<<8)|b[2] as i32;(v<<8)>>8}
    fn temperature_value(&self,t:i32)->i64{let c=self.coefficients.get();((c[0]*0.5+c[1]*(t as f64/7864320.0))*100.0) as i64}
    fn pressure_value(&self,p:i32,t:i32)->i64{
        let c=self.coefficients.get();let p=p as f64/7864320.0;let t=t as f64/7864320.0;
        let value=c[2]+p*(c[3]+p*(c[6]+p*c[8]))+t*c[4]+t*p*(c[5]+p*c[7]);(value/100.0) as i64
    }
}
impl<'a,I:I2CDevice,A:Alarm<'a>> I2CClient for Dps310<'a,I,A>{
    fn command_complete(&self,b:&'static mut [u8],status:Result<(),i2c::Error>){
        if let Err(e)=status{self.buffer.replace(b);self.finish(Err(e.into()));return;}
        let result=match self.state.get(){
            State::Identity=>{if b[0]!=0x10{self.buffer.replace(b);self.finish(Err(ErrorCode::NODEVICE));return;}self.read(b,0x08,1,State::InitStatus)}
            State::InitStatus=>{if b[0]&0xc0==0xc0{self.read(b,0x10,18,State::Calibration)}else{self.retry(b,State::InitWait);return;}}
            State::Calibration=>{self.parse_calibration(b);self.read(b,0x28,1,State::Source)}
            State::Source=>{self.temp_source.set(b[0]&0x80);self.write(b,0x06,0x03,State::PressureCfg)}
            State::PressureCfg=>self.write(b,0x07,self.temp_source.get()|0x03,State::TemperatureCfg),
            State::TemperatureCfg=>self.write(b,0x09,0x00,State::CommonCfg),
            State::CommonCfg=>self.write(b,0x0e,0xa5,State::UnlockA),
            State::UnlockA=>self.write(b,0x0f,0x96,State::UnlockB),
            State::UnlockB=>self.write(b,0x62,0x02,State::Fix),
            State::Fix=>self.write(b,0x0e,0x00,State::LockA),
            State::LockA=>self.write(b,0x0f,0x00,State::LockB),
            State::LockB=>{self.initialized.set(true);self.buffer.replace(b);self.idle();return;}
            State::Before=>{if b[0]&7==0{self.start_temperature(b)}else{self.retry(b,State::BeforeWait);return;}}
            State::TempCommand=>self.read(b,0x08,1,State::TempStatus),
            State::TempStatus=>{if b[0]&0x20!=0{self.read(b,0x03,3,State::TempData)}else{self.retry(b,State::TempWait);return;}}
            State::TempData=>{let raw=Self::raw(b);self.raw_temperature.set(Some(raw));
                if self.channel.get()==Some(true){self.start_pressure(b)}else{let value=self.temperature_value(raw);self.buffer.replace(b);self.finish(Ok(value));return;}}
            State::PressureCommand=>self.read(b,0x08,1,State::PressureStatus),
            State::PressureStatus=>{if b[0]&0x10!=0{self.read(b,0x00,3,State::PressureData)}else{self.retry(b,State::PressureWait);return;}}
            State::PressureData=>{let value=self.pressure_value(Self::raw(b),self.raw_temperature.get().unwrap());self.buffer.replace(b);self.finish(Ok(value));return;}
            _=>{self.buffer.replace(b);self.finish(Err(ErrorCode::FAIL));return;}
        };
        if let Err(e)=result{self.finish(Err(e));}
    }
}
impl<'a,I:I2CDevice,A:Alarm<'a>> AlarmClient for Dps310<'a,I,A>{
    fn alarm(&self){let Some(b)=self.buffer.take() else{self.finish(Err(ErrorCode::NOMEM));return;};
        let result=match self.state.get(){State::Por=>self.read(b,0x0d,1,State::Identity),
            State::InitWait=>self.read(b,0x08,1,State::InitStatus),State::BeforeWait=>self.read(b,0x08,1,State::Before),
            State::TempWait=>self.read(b,0x08,1,State::TempStatus),State::PressureWait=>self.read(b,0x08,1,State::PressureStatus),
            _=>{self.buffer.replace(b);self.finish(Err(ErrorCode::FAIL));return;}};
        if let Err(e)=result{self.finish(Err(e));}
    }
}
impl<'a,I:I2CDevice,A:Alarm<'a>> TemperatureDriver<'a> for Dps310<'a,I,A>{
    fn set_client(&self,c:&'a dyn TemperatureClient){self.temperature.set(Some(c));}
    fn read_temperature(&self)->Result<(),ErrorCode>{self.request(false)}
}
impl<'a,I:I2CDevice,A:Alarm<'a>> PressureDriver<'a> for Dps310<'a,I,A>{
    fn set_client(&self,c:&'a dyn PressureClient){self.pressure.set(Some(c));}
    fn read_atmospheric_pressure(&self)->Result<(),ErrorCode>{self.request(true)}
}
