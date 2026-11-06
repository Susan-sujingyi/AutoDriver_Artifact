//! Independent finite open-drain wire and pulse source. No reference-driver calls.
use kernel::hil::{gpio,time};
use kernel::hil::time::{Ticks,Time};
use kernel::ErrorCode;
use std::cell::{Cell,RefCell};
use std::collections::VecDeque;
#[derive(Clone,Copy,Debug)]
pub struct Sample {pub temperature:u8,pub humidity:u8}
#[derive(Clone,Copy,Debug,PartialEq)]
pub enum Fault {None,NoResponse,Truncate,Checksum,StuckLow}
pub struct Wire {
 pub clock:&'static Cell<u64>,pub sample:Cell<Sample>,pub acquired:RefCell<Vec<Sample>>,
 pub fault:Cell<Fault>,pub zero_us:Cell<u64>,pub one_us:Cell<u64>,pub edge_delay:Cell<u64>,
 events:RefCell<VecDeque<(u64,bool,bool)>>,level:Cell<bool>,out:Cell<bool>,latch:Cell<bool>,pull:Cell<gpio::FloatingState>,
 low_start:Cell<Option<u64>>,last_release:Cell<Option<u64>>,frame:Cell<Sample>,
 pub starts:Cell<usize>,pub releases:Cell<usize>,pub edges:Cell<usize>,
 irq:Cell<Option<gpio::InterruptEdge>>,client:Cell<Option<&'static dyn gpio::Client>>,
}
impl Wire {
 pub fn new(clock:&'static Cell<u64>,sample:Sample)->Self{Self{clock,sample:Cell::new(sample),acquired:RefCell::new(Vec::new()),fault:Cell::new(Fault::None),zero_us:Cell::new(27),one_us:Cell::new(70),edge_delay:Cell::new(0),events:RefCell::new(VecDeque::new()),level:Cell::new(true),out:Cell::new(false),latch:Cell::new(false),pull:Cell::new(gpio::FloatingState::PullNone),low_start:Cell::new(None),last_release:Cell::new(None),frame:Cell::new(sample),starts:Cell::new(0),releases:Cell::new(0),edges:Cell::new(0),irq:Cell::new(None),client:Cell::new(None)}}
 fn release(&self){
  let Some(start)=self.low_start.take() else{return};let now=self.clock.get();self.releases.set(self.releases.get()+1);
  assert!(now-start>=18000,"PROTOCOL:start pulse shorter than 18ms");
  assert!(start>=1000000,"PROTOCOL:start before power settling");
  if let Some(last)=self.last_release.get(){assert!(now-last>=1000000,"PROTOCOL:physical acquisitions closer than 1 second");}
  self.last_release.set(Some(now));self.starts.set(self.starts.get()+1);
  assert!(self.events.borrow().is_empty(),"PROTOCOL:new start while response is active");
  self.level.set(true);let f=self.fault.get();if f==Fault::NoResponse{return;}
  let s=self.sample.get();self.frame.set(s);let mut bytes=[s.humidity,0,s.temperature,0,0];bytes[4]=bytes[..4].iter().fold(0u8,|a,b|a.wrapping_add(*b));if f==Fault::Checksum{bytes[4]^=1;}
  let mut events=self.events.borrow_mut();let mut t=now+30;events.push_back((t,false,false));if f==Fault::StuckLow{return;}
  t+=80;events.push_back((t,true,false));t+=80;events.push_back((t,false,false));
  for n in 0..40{t+=50;events.push_back((t,true,false));let bit=(bytes[n/8]>>(7-n%8))&1;t+=if bit==0{self.zero_us.get()}else{self.one_us.get()};events.push_back((t,false,n==39&&f==Fault::None));if f==Fault::Truncate&&n==17{return;}}
  t+=50;events.push_back((t,true,false));
 }
 pub fn due(&self)->Option<u64>{self.events.borrow().front().map(|e|e.0)}
 pub fn edge(&self){
  let(t,high,complete)=self.events.borrow_mut().pop_front().unwrap();self.clock.set(t.max(self.clock.get()));let old=self.level.replace(high);self.edges.set(self.edges.get()+1);
  if complete{self.acquired.borrow_mut().push(self.frame.get());}
  if old!=high{let notify=match self.irq.get(){Some(gpio::InterruptEdge::EitherEdge)=>true,Some(gpio::InterruptEdge::RisingEdge)=>high,Some(gpio::InterruptEdge::FallingEdge)=>!high,None=>false};if notify{self.clock.set((t+self.edge_delay.get()).max(self.clock.get()));if let Some(c)=self.client.get(){c.fired();}}}
 }
 fn drive(&self,high:bool){self.latch.set(high);if self.out.get(){if high{assert!(self.events.borrow().is_empty(),"PROTOCOL:push-pull high during sensor response");self.release();}else if self.low_start.get().is_none(){self.low_start.set(Some(self.clock.get()));}}}
}
impl gpio::Configure for Wire{
 fn configuration(&self)->gpio::Configuration{if self.out.get(){gpio::Configuration::Output}else{gpio::Configuration::Input}}
 fn make_output(&self)->gpio::Configuration{self.out.set(true);self.drive(self.latch.get());self.configuration()}
 fn disable_output(&self)->gpio::Configuration{self.make_input()}
 fn make_input(&self)->gpio::Configuration{self.out.set(false);self.release();self.configuration()}
 fn disable_input(&self)->gpio::Configuration{self.configuration()}
 fn deactivate_to_low_power(&self){self.make_input();}
 fn set_floating_state(&self,s:gpio::FloatingState){assert!(!matches!(s,gpio::FloatingState::PullDown),"CONFIG:pull-down conflicts with board pull-up");self.pull.set(s);}
 fn floating_state(&self)->gpio::FloatingState{self.pull.get()}
}
impl gpio::Output for Wire{fn set(&self){self.drive(true)}fn clear(&self){self.drive(false)}fn toggle(&self)->bool{let h=!self.latch.get();self.drive(h);h}}
impl gpio::Input for Wire{fn read(&self)->bool{if self.out.get(){self.latch.get()}else{self.level.get()}}}
impl gpio::Interrupt<'static> for Wire{
 fn set_client(&self,c:&'static dyn gpio::Client){self.client.set(Some(c))}
 fn enable_interrupts(&self,e:gpio::InterruptEdge){self.irq.set(Some(e))}
 fn disable_interrupts(&self){self.irq.set(None)}fn is_pending(&self)->bool{false}
}
pub struct ClockAlarm{pub clock:&'static Cell<u64>,pub due:Cell<Option<u64>>,pub client:Cell<Option<&'static dyn time::AlarmClient>>}
impl Time for ClockAlarm{type Frequency=time::Freq1MHz;type Ticks=time::Ticks32;fn now(&self)->Self::Ticks{Self::Ticks::from(self.clock.get()as u32)}}
impl time::Alarm<'static> for ClockAlarm{
 fn set_alarm_client(&self,c:&'static dyn time::AlarmClient){self.client.set(Some(c))}
 fn set_alarm(&self,reference:Self::Ticks,dt:Self::Ticks){let now=self.now();let end=reference.wrapping_add(dt);let delay=if now.within_range(reference,end){end.wrapping_sub(now).into_u32().max(10)}else{10};self.due.set(Some(self.clock.get()+delay as u64));}
 fn get_alarm(&self)->Self::Ticks{Self::Ticks::from(self.due.get().unwrap_or(self.clock.get())as u32)}
 fn disarm(&self)->Result<(),ErrorCode>{self.due.set(None);Ok(())}
 fn is_armed(&self)->bool{self.due.get().is_some()}fn minimum_dt(&self)->Self::Ticks{Self::Ticks::from(10)}
}
impl ClockAlarm{pub fn fire(&self){let t=self.due.take().unwrap();self.clock.set(t.max(self.clock.get()));self.client.get().unwrap().alarm();}}

// Independent asynchronous platform model. Generates samples from the physical
// wire event schedule, not from the reference decoder or its implementation.
pub struct Sampler {
 pub wire:&'static Wire, pub due:Cell<Option<u64>>,pub delay:Cell<u64>,
 pub fail:Cell<bool>,pub short:Cell<bool>, pub delivered:Cell<usize>,
 client:Cell<Option<&'static dyn kernel::hil::waveform::Client>>,
 buffer:RefCell<Option<&'static mut [u8]>>,status:Cell<kernel::hil::waveform::Status>,
 generation:Cell<u32>,
}
impl Sampler {
 pub fn new(wire:&'static Wire)->Self{Self{wire,due:Cell::new(None),delay:Cell::new(0),fail:Cell::new(false),short:Cell::new(false),delivered:Cell::new(0),client:Cell::new(None),buffer:RefCell::new(None),status:Cell::new(kernel::hil::waveform::Status::Complete),generation:Cell::new(0)}}
 pub fn fire(&self){
  let t=self.due.take().unwrap();self.wire.clock.set(t.max(self.wire.clock.get()));
  let b=self.buffer.borrow_mut().take().unwrap();let len=b.len();
  self.delivered.set(self.delivered.get()+1);
  self.client.get().unwrap().captured(b,kernel::hil::waveform::Report{status:self.status.get(),bytes:if self.short.get(){len-1}else{len},sample_period_ns:1000,elapsed_us:(len*8)as u32,generation:self.generation.get()});
 }
}
impl kernel::hil::waveform::Capture for Sampler {
 fn set_client(&self,c:&'static dyn kernel::hil::waveform::Client){self.client.set(Some(c));}
 fn busy(&self)->bool{self.buffer.borrow().is_some()}
 fn start(&self,b:&'static mut[u8])->Result<(),(ErrorCode,&'static mut[u8])>{
  if self.busy(){return Err((ErrorCode::BUSY,b));}
  if self.client.get().is_none(){return Err((ErrorCode::OFF,b));}
  if b.len()<2||b.len()>2048{return Err((ErrorCode::SIZE,b));}
  let start=self.wire.clock.get();
  gpio::Configure::make_input(self.wire);
  let events=self.wire.events.borrow();let mut pos=0;let mut high=true;
  b.fill(0);
  for sample in 0..b.len()*8 {
   while pos<events.len()&&events[pos].0<=start+sample as u64{high=events[pos].1;pos+=1;}
   if high{b[sample/8]|=0x80>>(sample%8);}
  }
  drop(events);
  self.status.set(if self.fail.get(){kernel::hil::waveform::Status::Hardware}else{kernel::hil::waveform::Status::Complete});
  self.due.set(Some(start+(b.len()*8)as u64+self.delay.get()));
  self.generation.set(self.generation.get().wrapping_add(1));self.buffer.replace(Some(b));
  assert_eq!(self.wire.clock.get(),start,"MODEL:submission advanced physical time");
  Ok(())
 }
 fn cancel(&self)->Result<(),ErrorCode>{
  if !self.busy(){return Err(ErrorCode::OFF);}
  self.status.set(kernel::hil::waveform::Status::Cancelled);self.due.set(Some(self.wire.clock.get()+1));Ok(())
 }
}
