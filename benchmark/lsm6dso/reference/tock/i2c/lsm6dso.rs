// SPDX-License-Identifier: Apache-2.0
//! Evaluator-only LSM6DSO acceleration reference; no method/IR dependencies.
use core::cell::Cell;
use kernel::hil::i2c::{self,I2CDevice,I2CClient};
use kernel::hil::sensors::{NineDof,NineDofClient};
use kernel::hil::time::{Alarm,AlarmClient,Frequency,Ticks};
use kernel::utilities::cells::TakeCell;
use kernel::ErrorCode;
pub const BUFFER_SIZE:usize=14;
const POLL_US:u32=1_000;
const MAX_POLLS:u32=80;
const SETUP:&[(u8,u8)]=&[(0x12,0x44),(0x13,0x08),(0x18,0xe2),(0x11,0),(0x0a,0),(0x15,0),(0x14,0),(0x17,0),(0x10,0x40)];
#[derive(Clone,Copy,PartialEq)]
enum State {Off,Bank,Identity,Reset,ResetCheck,ResetWait,Configure,Ready,Discard,Status,Waiting,Data}
pub struct Lsm6dso<'a,I:I2CDevice,A:Alarm<'a>>{
    i2c:&'a I,alarm:&'a A,buffer:TakeCell<'static,[u8]>,state:Cell<State>,config:Cell<usize>,polls:Cell<u32>,client:Cell<Option<&'a dyn NineDofClient>>,
}
impl<'a,I:I2CDevice,A:Alarm<'a>> Lsm6dso<'a,I,A>{
    pub fn new(i2c:&'a I,alarm:&'a A,buffer:&'static mut[u8])->Self{
        Self{i2c,alarm,buffer:TakeCell::new(buffer),state:Cell::new(State::Off),config:Cell::new(0),polls:Cell::new(0),client:Cell::new(None)}
    }
    fn initial(&self)->bool{matches!(self.state.get(),State::Bank|State::Identity|State::Reset|State::ResetCheck|State::ResetWait|State::Configure)}
    fn terminal(&self){let initial=self.initial();let _=self.alarm.disarm();self.i2c.disable();self.state.set(if initial{State::Off}else{State::Ready});}
    fn submit(&self,b:&'static mut[u8],w:usize,r:usize)->Result<(),ErrorCode>{
        self.i2c.enable();let result=if r==0{self.i2c.write(b,w)}else{self.i2c.write_read(b,w,r)};
        match result{Ok(())=>Ok(()),Err((e,b))=>{self.buffer.replace(b);self.terminal();Err(e.into())}}
    }
    fn wait(&self,us:u32,state:State){self.state.set(state);self.i2c.disable();let dt=(A::Frequency::frequency() as u64*us as u64+999_999)/1_000_000;self.alarm.set_alarm(self.alarm.now(),A::Ticks::from_or_max(dt).max(self.alarm.minimum_dt()));}
    pub fn startup(&self)->Result<(),ErrorCode>{
        match self.state.get(){State::Ready=>return Ok(()),State::Off=>(),_=>return Err(ErrorCode::BUSY)}
        let b=self.buffer.take().ok_or(ErrorCode::BUSY)?;if b.len()<BUFFER_SIZE{self.buffer.replace(b);return Err(ErrorCode::SIZE)}
        self.config.set(0);self.polls.set(0);b[0]=0x01;b[1]=0;self.state.set(State::Bank);self.submit(b,2,0)
    }
    fn configure(&self,b:&'static mut[u8]){
        let i=self.config.get();if i==SETUP.len(){self.buffer.replace(b);self.state.set(State::Ready);self.i2c.disable();return}
        b[0]=SETUP[i].0;b[1]=SETUP[i].1;self.config.set(i+1);self.state.set(State::Configure);let _=self.submit(b,2,0);
    }
    fn status(&self,b:&'static mut[u8])->Result<(),ErrorCode>{b[0]=0x1e;self.state.set(State::Status);self.submit(b,1,1)}
    fn request(&self)->Result<(),ErrorCode>{
        match self.state.get(){State::Ready=>(),State::Off=>return Err(ErrorCode::OFF),_=>return Err(ErrorCode::BUSY)}
        let b=self.buffer.take().ok_or(ErrorCode::BUSY)?;self.polls.set(0);
        // Discard any old registers/partial BDU pair, then observe a new ready edge.
        b[0]=0x28;self.state.set(State::Discard);self.submit(b,1,6)
    }
}
impl<'a,I:I2CDevice,A:Alarm<'a>> I2CClient for Lsm6dso<'a,I,A>{
    fn command_complete(&self,b:&'static mut[u8],status:Result<(),i2c::Error>){
        if status.is_err(){self.buffer.replace(b);self.terminal();return}
        match self.state.get(){
            State::Bank=>{b[0]=0x0f;self.state.set(State::Identity);let _=self.submit(b,1,1);},
            State::Identity=>{if b[0]!=0x6c{self.buffer.replace(b);self.terminal();return}b[0]=0x12;b[1]=0x05;self.state.set(State::Reset);let _=self.submit(b,2,0);},
            State::Reset=>{b[0]=0x12;self.state.set(State::ResetCheck);let _=self.submit(b,1,1);},
            State::ResetCheck=>{if b[0]&1==0{self.configure(b);}else if self.polls.get()>=MAX_POLLS{self.buffer.replace(b);self.terminal();}else{self.polls.set(self.polls.get()+1);self.buffer.replace(b);self.wait(50,State::ResetWait);}},
            State::Configure=>self.configure(b),
            State::Discard=>{let _=self.status(b);},
            State::Status=>{if b[0]&1!=0{b[0]=0x28;self.state.set(State::Data);let _=self.submit(b,1,6);}else if self.polls.get()>=MAX_POLLS{self.buffer.replace(b);self.terminal();}else{self.polls.set(self.polls.get()+1);self.buffer.replace(b);self.wait(POLL_US,State::Waiting);}},
            State::Data=>{let mut xyz=[0usize;3];for i in 0..3{let raw=i16::from_le_bytes([b[2*i],b[2*i+1]]) as i64;let value=(raw*61*9_806_650/1_000_000) as i32;xyz[i]=(value as u32) as usize;}self.buffer.replace(b);self.terminal();if let Some(c)=self.client.get(){c.callback(xyz[0],xyz[1],xyz[2]);}},
            _=>{self.buffer.replace(b);self.terminal();}
        }
    }
}
impl<'a,I:I2CDevice,A:Alarm<'a>> AlarmClient for Lsm6dso<'a,I,A>{
    fn alarm(&self){if let Some(b)=self.buffer.take(){match self.state.get(){State::Waiting=>{let _=self.status(b);},State::ResetWait=>{b[0]=0x12;self.state.set(State::ResetCheck);let _=self.submit(b,1,1);},_=>{self.buffer.replace(b);self.terminal();}}}}
}
impl<'a,I:I2CDevice,A:Alarm<'a>> NineDof<'a> for Lsm6dso<'a,I,A>{
    fn set_client(&self,c:&'a dyn NineDofClient){self.client.set(Some(c));}
    fn read_accelerometer(&self)->Result<(),ErrorCode>{self.request()}
    fn read_gyroscope(&self)->Result<(),ErrorCode>{Err(ErrorCode::NOSUPPORT)}
    fn read_magnetometer(&self)->Result<(),ErrorCode>{Err(ErrorCode::NOSUPPORT)}
}
