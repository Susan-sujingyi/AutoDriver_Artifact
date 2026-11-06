// SPDX-License-Identifier: Apache-2.0
//! Evaluator-only BMP388 reference, Bosch DS001-07 polynomial and native HIL.
use core::cell::Cell;
use kernel::hil::i2c::{self, I2CClient, I2CDevice};
use kernel::hil::sensors::{TemperatureClient, TemperatureDriver, PressureClient, PressureDriver};
use kernel::hil::time::{Alarm, AlarmClient, Frequency, Ticks};
use kernel::utilities::cells::TakeCell;
use kernel::ErrorCode;
pub const BUFFER_SIZE: usize = 21;
const POLL_US: u32 = 1_000;
const MAX_POLLS: u32 = 80;
#[derive(Clone, Copy, PartialEq)]
enum State { Off, Por, CommandReady, CommandWait, Reset, ResetWait, Identity, Calibration,
    Odr, Osr, Filter, Mode, InitError, Ready, Error, Status, Wait, Sample }
pub struct Bmp388<'a, I: I2CDevice, A: Alarm<'a>> {
    i2c: &'a I, alarm: &'a A, buffer: TakeCell<'static,[u8]>, state: Cell<State>,
    initialized: Cell<bool>, channel: Cell<Option<bool>>, polls: Cell<u32>,
    coefficients: Cell<[f64;14]>, temperature: Cell<Option<&'a dyn TemperatureClient>>,
    pressure: Cell<Option<&'a dyn PressureClient>>,
}
impl<'a,I:I2CDevice,A:Alarm<'a>> Bmp388<'a,I,A> {
    pub fn new(i2c:&'a I,alarm:&'a A,buffer:&'static mut [u8])->Self {
        Self { i2c,alarm,buffer:TakeCell::new(buffer),state:Cell::new(State::Off),
            initialized:Cell::new(false),channel:Cell::new(None),polls:Cell::new(0),
            coefficients:Cell::new([0.0;14]),temperature:Cell::new(None),pressure:Cell::new(None) }
    }
    pub fn startup(&self)->Result<(),ErrorCode> {
        match self.state.get() {State::Ready=>return Ok(()),State::Off=>(),_=>return Err(ErrorCode::BUSY)}
        let buffer=self.buffer.take().ok_or(ErrorCode::BUSY)?;
        if buffer.len()<BUFFER_SIZE {self.buffer.replace(buffer);return Err(ErrorCode::SIZE);}
        self.polls.set(0);self.wait(buffer,State::Por,2_000);Ok(())
    }
    fn idle(&self) {
        let _=self.alarm.disarm();self.i2c.disable();
        self.state.set(if self.initialized.get() {State::Ready} else {State::Off});
    }
    fn finish(&self,value:Result<i64,ErrorCode>) {
        let channel=self.channel.take();self.idle();
        match channel {
            Some(false)=>{if let Some(c)=self.temperature.get(){c.callback(value.map(|v|v as i32));}},
            Some(true)=>{if let Some(c)=self.pressure.get(){c.callback(value.map(|v|v as u32));}},
            None=>(),
        }
    }
    fn read(&self,buffer:&'static mut [u8],reg:u8,len:usize,next:State)->Result<(),ErrorCode> {
        buffer[0]=reg;self.state.set(next);self.i2c.enable();
        match self.i2c.write_read(buffer,1,len) {
            Ok(())=>Ok(()),Err((e,b))=>{self.buffer.replace(b);Err(e.into())}
        }
    }
    fn write(&self,buffer:&'static mut [u8],reg:u8,value:u8,next:State)->Result<(),ErrorCode> {
        buffer[0]=reg;buffer[1]=value;self.state.set(next);self.i2c.enable();
        match self.i2c.write(buffer,2) {
            Ok(())=>Ok(()),Err((e,b))=>{self.buffer.replace(b);Err(e.into())}
        }
    }
    fn wait(&self,buffer:&'static mut [u8],next:State,us:u32) {
        self.buffer.replace(buffer);self.i2c.disable();self.state.set(next);
        let ticks=(A::Frequency::frequency() as u64*us as u64+999_999)/1_000_000;
        self.alarm.set_alarm(self.alarm.now(),A::Ticks::from_or_max(ticks).max(self.alarm.minimum_dt()));
    }
    fn request(&self,pressure:bool)->Result<(),ErrorCode> {
        if self.state.get()!=State::Ready {
            return Err(if self.state.get()==State::Off {ErrorCode::OFF} else {ErrorCode::BUSY});
        }
        let buffer=self.buffer.take().ok_or(ErrorCode::BUSY)?;
        self.channel.set(Some(pressure));self.polls.set(0);
        let result=self.read(buffer,0x02,1,State::Error);
        if result.is_err(){self.channel.set(None);self.idle();}result
    }
    fn parse_calibration(&self,b:&[u8]) {
        let u16le=|i|u16::from_le_bytes([b[i],b[i+1]]) as f64;
        let i16le=|i|i16::from_le_bytes([b[i],b[i+1]]) as f64;
        let i8val=|i|b[i] as i8 as f64;
        self.coefficients.set([u16le(0)*256.0,u16le(2)/1073741824.0,i8val(4)/281474976710656.0,
            (i16le(5)-16384.0)/1048576.0,(i16le(7)-16384.0)/536870912.0,
            i8val(9)/4294967296.0,i8val(10)/137438953472.0,u16le(11)*8.0,u16le(13)/64.0,
            i8val(15)/256.0,i8val(16)/32768.0,i16le(17)/281474976710656.0,
            i8val(19)/281474976710656.0,i8val(20)/36893488147419103232.0]);
    }
    fn decoded(&self,b:&[u8],pressure:bool)->i64 {
        let q=self.coefficients.get();let offset=if pressure {3} else {0};
        let ut=(b[offset] as u32+256*b[offset+1] as u32+65536*b[offset+2] as u32) as f64;
        let d=ut-q[0];let t=d*q[1]+d*d*q[2];
        if !pressure {return (t*100.0) as i64;}
        let up=(b[0] as u32+256*b[1] as u32+65536*b[2] as u32) as f64;
        let offset=q[7]+t*(q[8]+t*(q[9]+t*q[10]));
        let sensitivity=q[3]+t*(q[4]+t*(q[5]+t*q[6]));
        let p=offset+up*sensitivity+up*up*(q[11]+t*q[12])+up*up*up*q[13];
        (p/100.0) as i64
    }
}
impl<'a,I:I2CDevice,A:Alarm<'a>> I2CClient for Bmp388<'a,I,A> {
    fn command_complete(&self,buffer:&'static mut [u8],status:Result<(),i2c::Error>) {
        if let Err(e)=status {self.buffer.replace(buffer);self.finish(Err(e.into()));return;}
        let result=match self.state.get() {
            State::CommandReady=>{
                if buffer[0]&0x10!=0 {self.write(buffer,0x7e,0xb6,State::Reset)}
                else if self.polls.get()<MAX_POLLS {self.polls.set(self.polls.get()+1);self.wait(buffer,State::CommandWait,POLL_US);return;}
                else {self.buffer.replace(buffer);self.finish(Err(ErrorCode::FAIL));return;}
            }
            State::Reset=>{self.wait(buffer,State::ResetWait,2_000);return;}
            State::Identity=>{
                if buffer[0]!=0x50 {self.buffer.replace(buffer);self.finish(Err(ErrorCode::NODEVICE));return;}
                self.read(buffer,0x31,21,State::Calibration)
            }
            State::Calibration=>{self.parse_calibration(buffer);self.write(buffer,0x1d,0x03,State::Odr)}
            State::Odr=>self.write(buffer,0x1c,0x02,State::Osr),
            State::Osr=>self.write(buffer,0x1f,0x00,State::Filter),
            State::Filter=>self.write(buffer,0x1b,0x33,State::Mode),
            State::Mode=>self.read(buffer,0x02,1,State::InitError),
            State::InitError=>{
                if buffer[0]&7==0 {self.initialized.set(true);}
                self.buffer.replace(buffer);self.idle();return;
            }
            State::Error=>{
                if buffer[0]&7!=0 {self.buffer.replace(buffer);self.finish(Err(ErrorCode::FAIL));return;}
                self.read(buffer,0x03,1,State::Status)
            }
            State::Status=>{
                let pressure=self.channel.get()==Some(true);let mask=if pressure {0x20} else {0x40};
                if buffer[0]&mask!=0 {
                    let (reg,len)=if pressure {(0x04,6)} else {(0x07,3)};
                    self.read(buffer,reg,len,State::Sample)
                } else if self.polls.get()<MAX_POLLS {
                    self.polls.set(self.polls.get()+1);self.wait(buffer,State::Wait,POLL_US);return;
                } else {self.buffer.replace(buffer);self.finish(Err(ErrorCode::FAIL));return;}
            }
            State::Sample=>{
                let value=self.decoded(buffer,self.channel.get()==Some(true));
                self.buffer.replace(buffer);self.finish(Ok(value));return;
            }
            _=>{self.buffer.replace(buffer);self.finish(Err(ErrorCode::FAIL));return;}
        };
        if let Err(e)=result {self.finish(Err(e));}
    }
}
impl<'a,I:I2CDevice,A:Alarm<'a>> AlarmClient for Bmp388<'a,I,A> {
    fn alarm(&self) {
        let Some(buffer)=self.buffer.take() else {self.finish(Err(ErrorCode::NOMEM));return;};
        let result=match self.state.get() {
            State::Por|State::CommandWait=>self.read(buffer,0x03,1,State::CommandReady),
            State::ResetWait=>self.read(buffer,0x00,1,State::Identity),
            State::Wait=>self.read(buffer,0x02,1,State::Error),
            _=>{self.buffer.replace(buffer);self.finish(Err(ErrorCode::FAIL));return;}
        };
        if let Err(e)=result {self.finish(Err(e));}
    }
}
impl<'a,I:I2CDevice,A:Alarm<'a>> TemperatureDriver<'a> for Bmp388<'a,I,A> {
    fn set_client(&self,client:&'a dyn TemperatureClient){self.temperature.set(Some(client));}
    fn read_temperature(&self)->Result<(),ErrorCode>{self.request(false)}
}
impl<'a,I:I2CDevice,A:Alarm<'a>> PressureDriver<'a> for Bmp388<'a,I,A> {
    fn set_client(&self,client:&'a dyn PressureClient){self.pressure.set(Some(client));}
    fn read_atmospheric_pressure(&self)->Result<(),ErrorCode>{self.request(true)}
}
