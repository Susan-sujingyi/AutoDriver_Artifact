// SPDX-License-Identifier: Apache-2.0
//! Evaluator-only MPU6050 reference; no method-specific extraction/IR rules.
use core::cell::Cell;
use kernel::hil::i2c::{self, I2CDevice, I2CClient};
use kernel::hil::sensors::{NineDof, NineDofClient};
use kernel::hil::time::{Alarm, AlarmClient, Frequency, Ticks};
use kernel::utilities::cells::TakeCell;
use kernel::ErrorCode;
pub const BUFFER_SIZE: usize = 14;
const POLL_US: u32 = 1_000;
const MAX_POLLS: u32 = 50;
const SETUP: &[(u8,u8)] = &[(0x6b,0), (0x1c,0), (0x1b,0), (0x19,9), (0x38,1)];
#[derive(Clone,Copy,PartialEq)]
enum State { Off, Identity, Configure, Ready, Status, Waiting, Data, Flush }
pub struct Mpu6050<'a,I:I2CDevice,A:Alarm<'a>> {
    i2c: &'a I, alarm: &'a A, buffer: TakeCell<'static,[u8]>, state: Cell<State>,
    config: Cell<usize>, polls: Cell<u32>, result: Cell<[usize;3]>, client: Cell<Option<&'a dyn NineDofClient>>,
}
impl<'a,I:I2CDevice,A:Alarm<'a>> Mpu6050<'a,I,A> {
    pub fn new(i2c:&'a I,alarm:&'a A,buffer:&'static mut[u8])->Self {
        Self { i2c,alarm,buffer:TakeCell::new(buffer),state:Cell::new(State::Off),
            config:Cell::new(0),polls:Cell::new(0),result:Cell::new([0;3]),client:Cell::new(None) }
    }
    fn terminal(&self) {
        let _=self.alarm.disarm(); self.i2c.disable();
        let initial=matches!(self.state.get(),State::Identity|State::Configure);
        self.state.set(if initial {State::Off} else {State::Ready});
    }
    fn submit(&self,b:&'static mut[u8],w:usize,r:usize)->Result<(),ErrorCode> {
        self.i2c.enable();
        let result=if r==0 {self.i2c.write(b,w)} else {self.i2c.write_read(b,w,r)};
        match result {Ok(())=>Ok(()),Err((e,b))=>{self.buffer.replace(b);self.terminal();Err(e.into())}}
    }
    pub fn startup(&self)->Result<(),ErrorCode> {
        match self.state.get() {State::Ready=>return Ok(()),State::Off=>(),_=>return Err(ErrorCode::BUSY)}
        let b=self.buffer.take().ok_or(ErrorCode::BUSY)?;
        if b.len()<BUFFER_SIZE {self.buffer.replace(b);return Err(ErrorCode::SIZE)}
        b[0]=0x75;self.state.set(State::Identity);self.config.set(0);self.submit(b,1,1)
    }
    fn configure(&self,b:&'static mut[u8]) {
        let index=self.config.get();
        if index==SETUP.len() { self.buffer.replace(b);self.state.set(State::Ready);self.i2c.disable();return }
        b[0]=SETUP[index].0;b[1]=SETUP[index].1;self.config.set(index+1);self.state.set(State::Configure);
        let _=self.submit(b,2,0);
    }
    fn status(&self,b:&'static mut[u8])->Result<(),ErrorCode> {
        b[0]=0x3a;self.state.set(State::Status);self.submit(b,1,1)
    }
    fn request(&self)->Result<(),ErrorCode> {
        match self.state.get() {State::Ready=>(),State::Off=>return Err(ErrorCode::OFF),_=>return Err(ErrorCode::BUSY)}
        let b=self.buffer.take().ok_or(ErrorCode::BUSY)?;
        self.polls.set(0);self.status(b)
    }
}
impl<'a,I:I2CDevice,A:Alarm<'a>> I2CClient for Mpu6050<'a,I,A> {
    fn command_complete(&self,b:&'static mut[u8],status:Result<(),i2c::Error>) {
        if status.is_err() {self.buffer.replace(b);self.terminal();return}
        match self.state.get() {
            State::Identity=>{if b[0]!=0x68 && b[0]!=0x70 {self.buffer.replace(b);self.terminal();}else{self.configure(b);}},
            State::Configure=>self.configure(b),
            State::Status=>{
                if b[0]&1!=0 {b[0]=0x3b;self.state.set(State::Data);let _=self.submit(b,1,6);}
                else if self.polls.get()>=MAX_POLLS {self.buffer.replace(b);self.terminal();}
                else {self.polls.set(self.polls.get()+1);self.buffer.replace(b);self.state.set(State::Waiting);self.i2c.disable();
                    let ticks=(A::Frequency::frequency() as u64 * POLL_US as u64+999_999)/1_000_000;
                    self.alarm.set_alarm(self.alarm.now(),A::Ticks::from_or_max(ticks).max(self.alarm.minimum_dt()));}
            },
            State::Data=>{
                let mut xyz=[0usize;3];
                for i in 0..3 {let raw=i16::from_be_bytes([b[2*i],b[2*i+1]]) as i64;
                    let value=(raw*9_806_650/16_384) as i32;xyz[i]=(value as u32) as usize;}
                self.result.set(xyz);b[0]=0x3a;self.state.set(State::Flush);let _=self.submit(b,1,1);
            },
            State::Flush=>{
                let xyz=self.result.get();self.buffer.replace(b);self.terminal();
                if let Some(c)=self.client.get() {c.callback(xyz[0],xyz[1],xyz[2]);}
            },
            _=>{self.buffer.replace(b);self.terminal();}
        }
    }
}
impl<'a,I:I2CDevice,A:Alarm<'a>> AlarmClient for Mpu6050<'a,I,A> {
    fn alarm(&self) {if self.state.get()==State::Waiting {if let Some(b)=self.buffer.take(){let _=self.status(b);}else{self.terminal();}}}
}
impl<'a,I:I2CDevice,A:Alarm<'a>> NineDof<'a> for Mpu6050<'a,I,A> {
    fn set_client(&self,c:&'a dyn NineDofClient){self.client.set(Some(c));}
    fn read_accelerometer(&self)->Result<(),ErrorCode>{self.request()}
    fn read_magnetometer(&self)->Result<(),ErrorCode>{Err(ErrorCode::NOSUPPORT)}
    fn read_gyroscope(&self)->Result<(),ErrorCode>{Err(ErrorCode::NOSUPPORT)}
}
