// SPDX-License-Identifier: Apache-2.0
//! Evaluator-only DHT20 reference; not a method implementation or input.
use core::cell::Cell;
use kernel::hil::i2c::{self, I2CClient, I2CDevice};
use kernel::hil::sensors::{TemperatureClient, TemperatureDriver, HumidityClient, HumidityDriver};
use kernel::hil::time::{Alarm, AlarmClient, Frequency, Ticks};
use kernel::utilities::cells::TakeCell;
use kernel::ErrorCode;

pub const BUFFER_SIZE: usize = 7;
const POR_US: u32 = 100000;
const FIRST_WAIT_US: u32 = 80000;
const POLL_US: u32 = 10000;
const RESET_READ_US: u32 = 5000;
const RESET_WRITE_US: u32 = 10000;
const REGISTERS: [u8; 3] = [0x1b, 0x1c, 0x1e];
const STATUS_FIRST: bool = false;
const CACHE_OTHER: bool = false;
#[derive(Clone, Copy, PartialEq)]
enum State { Off, PowerWait, Status, ResetCommand, ResetWaitRead, ResetRead,
    ResetWaitWrite, ResetWrite, VerifyWait, Verify, ReadyWait, Ready,
    BeforeTrigger, Trigger, MeasureWait, PollStatus, Frame }
#[derive(Clone, Copy, PartialEq)]
enum Channel { Temperature, Humidity }
impl Channel { fn index(self) -> usize { if self == Self::Temperature { 0 } else { 1 } } }
pub struct Dht20<'a, I: I2CDevice, A: Alarm<'a>> {
    i2c: &'a I, alarm: &'a A, buffer: TakeCell<'static, [u8]>,
    state: Cell<State>, initialized: Cell<bool>, pending: Cell<Option<Channel>>,
    register: Cell<usize>, pair: Cell<[u8;2]>, start: Cell<A::Ticks>,
    cache: Cell<[Option<i64>;2]>,
    temperature: Cell<Option<&'a dyn TemperatureClient>>, humidity: Cell<Option<&'a dyn HumidityClient>>,
}
impl<'a, I:I2CDevice, A:Alarm<'a>> Dht20<'a,I,A> {
    pub fn new(i2c:&'a I, alarm:&'a A, buffer:&'static mut[u8])->Self {
        Self { i2c,alarm,buffer:TakeCell::new(buffer),state:Cell::new(State::Off),
            initialized:Cell::new(false),pending:Cell::new(None),register:Cell::new(0),
            pair:Cell::new([0;2]),start:Cell::new(A::Ticks::from(0)),cache:Cell::new([None;2]),
            temperature:Cell::new(None),humidity:Cell::new(None) }
    }
    pub fn startup(&self)->Result<(),ErrorCode> {
        match self.state.get() { State::Ready=>return Ok(()),State::Off=>(),_=>return Err(ErrorCode::BUSY) }
        let b=self.buffer.take().ok_or(ErrorCode::BUSY)?;let enough=b.len()>=BUFFER_SIZE;self.buffer.replace(b);
        if !enough { return Err(ErrorCode::SIZE); }
        self.register.set(0);self.wait(State::PowerWait,POR_US);Ok(())
    }
    fn wait(&self,state:State,us:u32) {
        self.i2c.disable();self.state.set(state);
        let ticks=(A::Frequency::frequency() as u64*us as u64+999999)/1000000;
        self.alarm.set_alarm(self.alarm.now(),A::Ticks::from_or_max(ticks).max(self.alarm.minimum_dt()));
    }
    fn idle(&self) { self.i2c.disable();let _=self.alarm.disarm();self.state.set(if self.initialized.get(){State::Ready}else{State::Off}); }
    fn fail(&self,error:ErrorCode) {
        let c=self.pending.take();self.cache.set([None;2]);self.idle();
        if c==Some(Channel::Temperature) { if let Some(client)=self.temperature.get(){client.callback(Err(error));} }
    }
    fn read(&self,b:&'static mut[u8],n:usize,state:State)->Result<(),ErrorCode> {
        self.state.set(state);self.i2c.enable();
        self.i2c.read(b,n).map_err(|(e,b)|{self.buffer.replace(b);e.into()})
    }
    fn write(&self,b:&'static mut[u8],bytes:&[u8],state:State)->Result<(),ErrorCode> {
        b[..bytes.len()].copy_from_slice(bytes);self.state.set(state);self.i2c.enable();
        self.i2c.write(b,bytes.len()).map_err(|(e,b)|{self.buffer.replace(b);e.into()})
    }
    fn reset_command(&self,b:&'static mut[u8])->Result<(),ErrorCode> {
        self.write(b,&[REGISTERS[self.register.get()],0,0],State::ResetCommand)
    }
    fn emit(&self,c:Channel,value:i64) {
        match c { Channel::Temperature=>if let Some(client)=self.temperature.get(){client.callback(Ok(value as i32));},
                  Channel::Humidity=>if let Some(client)=self.humidity.get(){client.callback(value as usize);} }
    }
    fn request(&self,c:Channel)->Result<(),ErrorCode> {
        match self.state.get(){State::Ready=>(),State::Off=>return Err(ErrorCode::OFF),_=>return Err(ErrorCode::BUSY)}
        if CACHE_OTHER {let mut cache=self.cache.get();if let Some(v)=cache[c.index()].take(){self.cache.set(cache);self.emit(c,v);return Ok(());}}
        let b=self.buffer.take().ok_or(ErrorCode::BUSY)?;
        self.pending.set(Some(c));self.start.set(self.alarm.now());
        let r=self.read(b,1,State::BeforeTrigger);
        if r.is_err(){self.pending.set(None);self.cache.set([None;2]);self.idle();}r
    }
    fn crc(data:&[u8])->u8 {
        let mut crc=0xff;
        for byte in data {crc^=byte;for _ in 0..8 {crc=if crc&0x80!=0{(crc<<1)^0x31}else{crc<<1};}}crc
    }
    fn busy(&self,b:&'static mut[u8]) {
        self.buffer.replace(b);
        let elapsed=self.alarm.now().wrapping_sub(self.start.get()).into_u32() as u64;
        if elapsed*1000>=A::Frequency::frequency() as u64*220 {self.fail(ErrorCode::FAIL);}
        else{self.wait(State::MeasureWait,POLL_US);}
    }
}
impl<'a,I:I2CDevice,A:Alarm<'a>> I2CClient for Dht20<'a,I,A> {
    fn command_complete(&self,b:&'static mut[u8],status:Result<(),i2c::Error>) {
        if let Err(e)=status {self.buffer.replace(b);self.fail(e.into());return;}
        let result=match self.state.get() {
            State::Status=>{
                if b[0]&0x18!=0x18 {self.reset_command(b)}
                else {self.buffer.replace(b);self.wait(State::ReadyWait,10000);Ok(())}
            }
            State::ResetCommand=>{self.buffer.replace(b);self.wait(State::ResetWaitRead,RESET_READ_US);Ok(())}
            State::ResetRead=>{self.pair.set([b[1],b[2]]);self.buffer.replace(b);self.wait(State::ResetWaitWrite,RESET_WRITE_US);Ok(())}
            State::ResetWrite=>{
                self.register.set(self.register.get()+1);
                if self.register.get()<3 {self.reset_command(b)}
                else {self.buffer.replace(b);self.wait(State::VerifyWait,10000);Ok(())}
            }
            State::Verify=>{
                let okay=b[0]&0x18==0x18;self.buffer.replace(b);
                if okay {self.initialized.set(true);self.idle();}else{self.fail(ErrorCode::FAIL);}Ok(())
            }
            // A failed earlier read may leave a conversion active. Its later complete
            // frame is a valid new acquisition; do not retrigger the busy sensor.
            State::BeforeTrigger=>{if b[0]&0x80!=0 {self.busy(b);Ok(())}else{self.write(b,&[0xac,0x33,0x00],State::Trigger)}}
            State::Trigger=>{self.buffer.replace(b);self.wait(State::MeasureWait,FIRST_WAIT_US);Ok(())}
            State::PollStatus=>{if b[0]&0x80!=0 {self.busy(b);Ok(())}else{self.read(b,7,State::Frame)}}
            State::Frame=>{
                if b[0]&0x80!=0 {self.busy(b);return;}
                if Self::crc(&b[..6])!=b[6] {self.buffer.replace(b);self.fail(ErrorCode::FAIL);return;}
                let h=((b[1] as u32)<<12)|((b[2] as u32)<<4)|((b[3] as u32)>>4);
                let t=(((b[3]&15) as u32)<<16)|((b[4] as u32)<<8)|b[5] as u32;
                let values=[t as i64*20000/1048576-5000,h as i64*10000/1048576];
                let c=self.pending.take().unwrap();
                if CACHE_OTHER {let mut cache=self.cache.get();cache[1-c.index()]=Some(values[1-c.index()]);self.cache.set(cache);}
                self.buffer.replace(b);self.idle();self.emit(c,values[c.index()]);Ok(())
            }
            _=>{self.buffer.replace(b);self.fail(ErrorCode::FAIL);Ok(())}
        };
        if let Err(e)=result {self.fail(e);}
    }
}
impl<'a,I:I2CDevice,A:Alarm<'a>> AlarmClient for Dht20<'a,I,A> {
    fn alarm(&self) {
        if self.state.get()==State::ReadyWait {self.initialized.set(true);self.idle();return;}
        let Some(b)=self.buffer.take() else{return};
        let result=match self.state.get(){
            State::PowerWait=>self.read(b,1,State::Status),
            State::ResetWaitRead=>self.read(b,3,State::ResetRead),
            State::ResetWaitWrite=>{let pair=self.pair.get();self.write(b,&[0xb0|REGISTERS[self.register.get()],pair[0],pair[1]],State::ResetWrite)}
            State::VerifyWait=>self.read(b,1,State::Verify),
            State::MeasureWait=>if STATUS_FIRST{self.read(b,1,State::PollStatus)}else{self.read(b,7,State::Frame)},
            _=>{self.buffer.replace(b);Ok(())}
        };if let Err(e)=result{self.fail(e);}
    }
}
impl<'a,I:I2CDevice,A:Alarm<'a>> TemperatureDriver<'a> for Dht20<'a,I,A>{
    fn set_client(&self,c:&'a dyn TemperatureClient){self.temperature.set(Some(c));}
    fn read_temperature(&self)->Result<(),ErrorCode>{self.request(Channel::Temperature)}
}
impl<'a,I:I2CDevice,A:Alarm<'a>> HumidityDriver<'a> for Dht20<'a,I,A>{
    fn set_client(&self,c:&'a dyn HumidityClient){self.humidity.set(Some(c));}
    fn read_humidity(&self)->Result<(),ErrorCode>{self.request(Channel::Humidity)}
}
