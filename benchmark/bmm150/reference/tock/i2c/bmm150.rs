// SPDX-License-Identifier: Apache-2.0
//! Evaluator-only BMM150 normal-mode reference, native Tock HIL.
use core::cell::Cell;
use kernel::hil::i2c::{self, I2CClient, I2CDevice};
use kernel::hil::sensors::{NineDof, NineDofClient};
use kernel::hil::time::{Alarm, AlarmClient, Frequency, Ticks};
use kernel::utilities::cells::TakeCell;
use kernel::ErrorCode;
pub const BUFFER_SIZE: usize = 21;
const POR_US: u32 = 1000;
const POWER_US: u32 = 3000;
const POLL_US: u32 = 5000;
const MAX_POLLS: u8 = 40;
const TRIM_CHUNK: usize = 21;
const SPLIT_READ: bool = false;
const SOFT_RESET: bool = false;
const STATUS_POLL: bool = false;
const FRAME_LEN: usize = 8;
const CONFIG: &[(u8,u8)] = &[(0x51,0x04),(0x52,0x0e),(0x4c,0x00)];
#[derive(Clone,Copy,PartialEq)]
enum State { Off, Ready, PorWait, Power, PowerWait, Reset, ResetWait, Id, Trim, Config, Frame, Status, SampleWait, Pointer }
#[derive(Clone,Copy)]
struct Trim { x1:i8,y1:i8,x2:i8,y2:i8,z1:u16,z2:i16,z3:i16,z4:i16,xy1:u8,xy2:i8,xyz1:u16 }
impl Trim {
 fn decode(b:&[u8;21])->Self {
  Self{x1:b[0]as i8,y1:b[1]as i8,z4:i16::from_le_bytes([b[5],b[6]]),x2:b[7]as i8,y2:b[8]as i8,
   z2:i16::from_le_bytes([b[11],b[12]]),z1:u16::from_le_bytes([b[13],b[14]]),
   xyz1:u16::from_le_bytes([b[15],b[16]&0x7f]),z3:i16::from_le_bytes([b[17],b[18]]),xy2:b[19]as i8,xy1:b[20]}
 }
 fn usable(&self)->bool { self.z1 != 0 && self.z2 != 0 && self.xyz1 != 0 }
 fn compensate(&self,b:&[u8])->Option<[i32;3]> {
  let x=(i16::from_le_bytes([b[0],b[1]])>>3)as i32;
  let y=(i16::from_le_bytes([b[2],b[3]])>>3)as i32;
  let z=(i16::from_le_bytes([b[4],b[5]])>>1)as i32;
  let hall=(u16::from_le_bytes([b[6],b[7]])>>2)as u32;
  if x == -4096 || y == -4096 || z == -16384 || hall == 0 || !self.usable() { return None; }
  let q=self.xyz1 as f64/hall as f64-1.0;
  let gain=self.xy2 as f64*q*q+self.xy1 as f64*q+256.0;
  let cx=x as f64*gain*(self.x2 as f64+160.0)/131072.0+self.x1 as f64/2.0;
  let cy=y as f64*gain*(self.y2 as f64+160.0)/131072.0+self.y1 as f64/2.0;
  let denominator=self.z2 as f64+self.z1 as f64*hall as f64/32768.0;
  if denominator == 0.0 { return None; }
  let cz=((z as f64-self.z4 as f64)*131072.0-self.z3 as f64*(hall as f64-self.xyz1 as f64))/(64.0*denominator);
  let values=[cx*1000.0,cy*1000.0,cz*1000.0];
  if values.iter().any(|v| !v.is_finite() || *v < i32::MIN as f64 || *v > i32::MAX as f64) {return None;}
  Some([values[0]as i32,values[1]as i32,values[2]as i32])
 }
}
pub struct Bmm150<'a,I:I2CDevice,A:Alarm<'a>> {
 i2c:&'a I,alarm:&'a A,buffer:TakeCell<'static,[u8]>,state:Cell<State>,requested:Cell<bool>,
 client:Cell<Option<&'a dyn NineDofClient>>,trim_bytes:Cell<[u8;21]>,trim_offset:Cell<usize>,
 config_index:Cell<usize>,polls:Cell<u8>,read_state:Cell<State>,read_length:Cell<usize>,
}
impl<'a,I:I2CDevice,A:Alarm<'a>> Bmm150<'a,I,A> {
 pub fn new(i2c:&'a I,alarm:&'a A,buffer:&'static mut[u8])->Self {
  Self{i2c,alarm,buffer:TakeCell::new(buffer),state:Cell::new(State::Off),requested:Cell::new(false),
   client:Cell::new(None),trim_bytes:Cell::new([0;21]),trim_offset:Cell::new(0),config_index:Cell::new(0),polls:Cell::new(0),read_state:Cell::new(State::Off),read_length:Cell::new(0)}
 }
 pub fn startup(&self)->Result<(),ErrorCode> {
  match self.state.get(){State::Ready=>return Ok(()),State::Off=>(),_=>return Err(ErrorCode::BUSY)}
  let b=self.buffer.take().ok_or(ErrorCode::BUSY)?;let enough=b.len()>=BUFFER_SIZE;self.buffer.replace(b);
  if !enough{return Err(ErrorCode::SIZE);}
  self.requested.set(false);self.trim_offset.set(0);self.config_index.set(0);self.wait(State::PorWait,POR_US);Ok(())
 }
 fn wait(&self,state:State,us:u32){self.i2c.disable();self.state.set(state);let dt=(A::Frequency::frequency()as u64*us as u64+999999)/1000000;self.alarm.set_alarm(self.alarm.now(),A::Ticks::from_or_max(dt).max(self.alarm.minimum_dt()));}
 fn fail(&self){self.i2c.disable();self.state.set(if self.requested.replace(false){State::Ready}else{State::Off});}
 fn write(&self,b:&'static mut[u8],reg:u8,value:u8,state:State)->Result<(),ErrorCode>{
  b[0]=reg;b[1]=value;self.state.set(state);self.i2c.enable();
  match self.i2c.write(b,2){Ok(())=>Ok(()),Err((e,b))=>{self.buffer.replace(b);self.fail();Err(e.into())}}
 }
 fn read(&self,b:&'static mut[u8],reg:u8,len:usize,state:State)->Result<(),ErrorCode>{
  b[0]=reg;self.i2c.enable();self.read_length.set(len);self.read_state.set(state);
  self.state.set(if SPLIT_READ{State::Pointer}else{state});
  let result=if SPLIT_READ{self.i2c.write(b,1)}else{self.i2c.write_read(b,1,len)};
  match result{Ok(())=>Ok(()),Err((e,b))=>{self.buffer.replace(b);self.fail();Err(e.into())}}
 }
 fn trim_read(&self,b:&'static mut[u8]){let offset=self.trim_offset.get();let len=TRIM_CHUNK.min(21-offset);let _=self.read(b,0x5d+offset as u8,len,State::Trim);}
 fn configure(&self,b:&'static mut[u8]){let (reg,val)=CONFIG[self.config_index.get()];let _=self.write(b,reg,val,State::Config);}
}
impl<'a,I:I2CDevice,A:Alarm<'a>> I2CClient for Bmm150<'a,I,A>{
 fn command_complete(&self,b:&'static mut[u8],status:Result<(),i2c::Error>){
  if status.is_err(){self.buffer.replace(b);self.fail();return;}
  match self.state.get(){
   State::Pointer=>{self.state.set(self.read_state.get());if let Err((_e,b))=self.i2c.read(b,self.read_length.get()){self.buffer.replace(b);self.fail();}},
   State::Power=>{self.buffer.replace(b);self.wait(State::PowerWait,POWER_US);},
   State::Reset=>{self.buffer.replace(b);self.wait(State::ResetWait,1000);},
   State::Id=>{if b[0]!=0x32{self.buffer.replace(b);self.fail();}else{self.trim_read(b);}},
   State::Trim=>{let offset=self.trim_offset.get();let len=self.read_length.get();let mut bytes=self.trim_bytes.get();bytes[offset..offset+len].copy_from_slice(&b[..len]);self.trim_bytes.set(bytes);self.trim_offset.set(offset+len);
    if offset+len<21{self.trim_read(b);}else if !Trim::decode(&bytes).usable(){self.buffer.replace(b);self.fail();}else{self.configure(b);}},
   State::Config=>{self.config_index.set(self.config_index.get()+1);if self.config_index.get()<CONFIG.len(){self.configure(b);}else{self.buffer.replace(b);self.i2c.disable();self.state.set(State::Ready);}},
   State::Status=>{
    if b[0]&1==0{self.buffer.replace(b);if self.polls.get()>=MAX_POLLS{self.fail();}else{self.polls.set(self.polls.get()+1);self.wait(State::SampleWait,POLL_US);}}
    else{let _=self.read(b,0x42,FRAME_LEN,State::Frame);}
   },
   State::Frame=>{
    if !STATUS_POLL && b[6]&1==0{self.buffer.replace(b);if self.polls.get()>=MAX_POLLS{self.fail();}else{self.polls.set(self.polls.get()+1);self.wait(State::SampleWait,POLL_US);}return;}
    let value=Trim::decode(&self.trim_bytes.get()).compensate(b);self.buffer.replace(b);self.i2c.disable();self.requested.set(false);self.state.set(State::Ready);
    if let Some(v)=value{if let Some(c)=self.client.get(){c.callback((v[0]as u32)as usize,(v[1]as u32)as usize,(v[2]as u32)as usize);}}
   },
   _=>{self.buffer.replace(b);self.fail();},
  }
 }
}
impl<'a,I:I2CDevice,A:Alarm<'a>> AlarmClient for Bmm150<'a,I,A>{
 fn alarm(&self){if let Some(b)=self.buffer.take(){match self.state.get(){
  State::PorWait=>{let _=self.write(b,0x4b,0x01,State::Power);},
  State::PowerWait if SOFT_RESET=>{let _=self.write(b,0x4b,0x83,State::Reset);},
  State::PowerWait|State::ResetWait=>{let _=self.read(b,0x40,1,State::Id);},
  State::SampleWait=>{let _=self.read(b,if STATUS_POLL{0x48}else{0x42},if STATUS_POLL{1}else{FRAME_LEN},if STATUS_POLL{State::Status}else{State::Frame});},
  _=>{self.buffer.replace(b);self.fail();},
 }}}
}
impl<'a,I:I2CDevice,A:Alarm<'a>> NineDof<'a> for Bmm150<'a,I,A>{
 fn set_client(&self,c:&'a dyn NineDofClient){self.client.set(Some(c));}
 fn read_magnetometer(&self)->Result<(),ErrorCode>{
  match self.state.get(){State::Ready=>(),State::Off=>return Err(ErrorCode::OFF),_=>return Err(ErrorCode::BUSY)}
  let b=self.buffer.take().ok_or(ErrorCode::BUSY)?;self.requested.set(true);self.polls.set(0);self.read(b,if STATUS_POLL{0x48}else{0x42},if STATUS_POLL{1}else{FRAME_LEN},if STATUS_POLL{State::Status}else{State::Frame})
 }
}
