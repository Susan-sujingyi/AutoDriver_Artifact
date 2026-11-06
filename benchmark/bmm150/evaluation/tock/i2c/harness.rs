//! Independent BMM150 power/register/shadowing/epoch model.
mod executor;
use kernel::hil::{i2c,sensors};
use kernel::hil::time::Alarm;
use kernel::ErrorCode;
use std::cell::{Cell,RefCell};
mod bmm150;mod support;
use support::{DeviceModel,FakeAlarm,Fault,FaultAt};
type Bus=support::FakeI2c<Model>;type Driver=bmm150::Bmm150<'static,Bus,FakeAlarm>;
const STARTUP:u64=150_000;const REQUEST:u64=500_000;const PROGRESS:u64=2_000_000;
use std::panic::{catch_unwind,AssertUnwindSafe};
#[derive(Clone,Copy,Debug)]struct Vector{id:&'static str,trim:[u8;21],bytes:[u8;8],expected:[i64;3],exact_numerator:[i128;3],exact_denominator:[i128;3]}
include!("vectors.rs");
fn within_numeric_tolerance(value:[i64;3],v:&Vector)->bool{
 // Python generation checks a bound covering every signed i32 callback and
 // all products/subtractions below. There is no floating-point approximation
 // or extra unit from rounding the mathematical acceptance center.
 (0..3).all(|i|((value[i]as i128)*v.exact_denominator[i]-v.exact_numerator[i]).abs()<=1125*v.exact_denominator[i])
}
#[derive(Clone,Copy)]struct Observation{generation:u64,vector:Vector,mask:u16,valid:bool}
struct Model{input:Vector,sample:Vector,trim:[u8;21],regs:[u8;128],pointer:u8,powered:bool,busy_until:u64,
 next:Option<u64>,period:u64,first:u64,generation:u64,ready:bool,stuck:bool,invalid:Option<&'static str>,
 id:u8,observations:Vec<Observation>,fault_register:Option<u8>}
impl Model{
 fn new(v:&Vector)->Self{let mut r=[0;128];r[0x4c]=6;r[0x4d]=0x3f;r[0x4e]=7;Self{input:*v,sample:*v,trim:v.trim,regs:r,pointer:0,powered:false,busy_until:1000,next:None,period:100000,first:9785,generation:0,ready:false,stuck:false,invalid:None,id:0x32,observations:Vec::new(),fault_register:None}}
 fn tick(&mut self,now:u64){if !self.stuck{if let Some(due)=self.next{if now>=due{let count=(now-due)/self.period+1;self.next=Some(due+count*self.period);self.generation+=count;self.sample=self.input;self.ready=true;}}}}
 fn configured(&self)->bool{self.powered && self.regs[0x4c]&0x7f==0 && self.regs[0x51]==4 && self.regs[0x52]==14 && self.regs[0x4e]&0x38==0}
 fn reset_regs(&mut self){self.regs[0x4c]=6;self.regs[0x4d]=0x3f;self.regs[0x4e]=7;self.regs[0x51]=0;self.regs[0x52]=0;self.next=None;self.ready=false;}
 fn write_reg(&mut self,now:u64,reg:u8,v:u8){
  if reg==0x4b{if v&1==0{self.powered=false;self.reset_regs();self.regs[reg as usize]=v&4;}else{
   if !self.powered{self.powered=true;self.reset_regs();self.busy_until=now+3000;}
   else if v&0x82==0x82{self.reset_regs();self.busy_until=now+1000;}
   self.regs[reg as usize]=v&5;}
   return;
  }
  if !self.powered{return;}if now<self.busy_until{panic!("MODEL_UNSUPPORTED: write during unmodeled wake/reset transition");}
  if reg<=0x4a{return;} // Read-only writes ignored by the documented silicon interface.
  if !(0x4c..=0x52).contains(&reg){panic!("MODEL_UNSUPPORTED: register write {:02x}",reg);}
  if (0x4c..=0x52).contains(&reg){let before=self.regs[reg as usize];self.regs[reg as usize]=v;
   if reg==0x4c{match (v>>1)&3{0=>{if before&6!=0||self.next.is_none(){self.next=Some(now+self.first);}},3=>self.next=None,_=>panic!("CONFIG: selected normal mode, optional idle sleep only")}}
  }
 }
 fn receive(&mut self,now:u64,reg:u8,len:usize,b:&mut[u8]){
  if (reg as usize+len)>128{panic!("MODEL_UNSUPPORTED: register range");}if self.powered && now<self.busy_until && reg!=0x4b{panic!("MODEL_UNSUPPORTED: read during wake/reset transition");}let accessible=self.powered && now>=self.busy_until;
  let mut frame=self.sample.bytes;frame[6]=(frame[6]&!1)|u8::from(self.ready);
  if let Some(kind)=self.invalid{match kind{
   "x_overflow"=>{frame[0]=0;frame[1]=0x80;},"y_overflow"=>{frame[2]=0;frame[3]=0x80;},"z_overflow"=>{frame[4]=0;frame[5]=0x80;},"hall_zero"=>{frame[6]&=3;frame[7]=0;},_=>(),}}
  let mut mask=0u16;
  for offset in 0..len{let a=reg as usize+offset;b[offset]=if a==0x4b{self.regs[a]}else if !accessible{0}else if a==0x40{self.id}else if (0x42..=0x49).contains(&a){mask|=1<<(a-0x42);frame[a-0x42]}else if (0x5d..=0x71).contains(&a){self.trim[a-0x5d]}else{self.regs[a]};}
  if mask!=0{
   let valid=self.invalid.is_none() && self.configured() && self.generation>0;
   if let Some(o)=self.observations.iter_mut().find(|o|o.generation==self.generation){o.mask|=mask;o.valid&=valid;}else{self.observations.push(Observation{generation:self.generation,vector:self.sample,mask,valid});}
   self.ready=false; // Any data read clears DRDY at STOP, including status/RHALL_LSB.
  }
 }
}
impl DeviceModel for Model{
 fn response_error(&mut self,now:u64,_tx:&[u8],_len:usize)->Option<i2c::Error>{if now.saturating_sub(100)<1000{Some(i2c::Error::AddressNak)}else{None}}
 fn exchange(&mut self,now:u64,tx:&[u8],len:usize,b:&mut[u8]){
  self.tick(now.saturating_sub(100));
  if !tx.is_empty(){self.pointer=tx[0];if tx.len()>1{assert_eq!(len,0);for(i,v)in tx[1..].iter().enumerate(){self.write_reg(now,tx[0]+i as u8,*v);}}}
  if len>0{self.receive(now.saturating_sub(100),self.pointer,len,b);self.pointer=self.pointer.wrapping_add(len as u8);}
 }
 
 fn fault_matches(&self,at:FaultAt,tx:&[u8],len:usize)->bool{
  let reg=tx.first().copied().unwrap_or(self.pointer);
  if let Some(site)=self.fault_register{return reg==site || (len>0 && reg<=site && reg as usize+len>site as usize);}
  match at{FaultAt::Any=>true,FaultAt::Configuration=>tx.len()>1,FaultAt::Data=>len>0 && (0x42..=0x49).contains(&reg)}
 }
}
#[derive(Debug)]struct Output{value:[i64;3],generation:u64}
struct Collector{model:&'static RefCell<Model>,pending:Cell<usize>,values:RefCell<Vec<Output>>}
impl sensors::NineDofClient for Collector{
 fn callback(&self,x:usize,y:usize,z:usize){
  assert!(self.pending.get()>0,"CALLBACK: unsolicited or duplicate callback");self.pending.set(self.pending.get()-1);
  // Actual libtock-rs consumer casts to i32; accept zero/sign extension on the host.
  let value=[x,y,z].map(|v|(v as u32 as i32)as i64);let m=self.model.borrow();
  assert!(m.configured(),"CONFIG: selected regular normal10Hz XYZ configuration");
  let mut generation=0;
  for axis in 0..3{
   let axis_mask=3u16<<(2*axis);
   let acquired=m.observations.iter().filter(|o|o.mask&axis_mask==axis_mask&&o.valid&&m.observations.iter().any(|h|
    h.valid&&h.mask&0xc0==0xc0&&(h.vector.bytes[6]&0xfc,h.vector.bytes[7])==(o.vector.bytes[6]&0xfc,o.vector.bytes[7]))).collect::<Vec<_>>();
   assert!(!acquired.is_empty(),"DATA: no complete acquired axis with matching RHALL");
   let matched=acquired.iter().find(|o|((value[axis]as i128)*o.vector.exact_denominator[axis]-o.vector.exact_numerator[axis]).abs()<=1125*o.vector.exact_denominator[axis])
    .expect("NUMERIC: units/sign/trim/axis or acquired compensation operands mismatch");
   generation=generation.max(matched.generation);
  }
  self.values.borrow_mut().push(Output{value,generation});
 }
}
struct Rig{clock:&'static Cell<u64>,model:&'static RefCell<Model>,bus:&'static Bus,alarm:&'static FakeAlarm,driver:&'static Driver,client:&'static Collector}
impl Rig{
 fn new(v:&Vector)->Self{let clock=Box::leak(Box::new(Cell::new(100_000)));let model=Box::leak(Box::new(RefCell::new(Model::new(v))));let bus=Box::leak(Box::new(Bus{clock,model,pending:RefCell::new(None),client:Cell::new(None),fault:Cell::new(None),fault_hit:Cell::new(false),enabled:Cell::new(false)}));let alarm=Box::leak(Box::new(FakeAlarm{clock,due:Cell::new(None),client:Cell::new(None)}));let driver=Box::leak(Box::new(bmm150::Bmm150::new(bus,alarm,Box::leak(vec![0;21].into_boxed_slice()))));let client=Box::leak(Box::new(Collector{model,pending:Cell::new(0),values:RefCell::new(vec![])}));bus.client.set(Some(driver));alarm.set_alarm_client(driver);sensors::NineDof::set_client(driver,client);Self{clock,model,bus,alarm,driver,client}}
 fn advance(&self,end:u64,complete:bool){
        executor::drive(self.clock, end, executor::EVENT_LIMIT, &[
            executor::Event { name: "bus", next: &|| self.bus.pending.borrow().as_ref().map(|p| p.due), fire: &|| self.bus.complete() },
            executor::Event { name: "alarm", next: &|| self.alarm.due.get(), fire: &|| self.alarm.fire() },
        ], || complete&&self.client.pending.get()==0, |now| self.model.borrow_mut().tick(now));
    }
 fn init(&self){let _=self.driver.startup();self.advance(self.clock.get()+STARTUP,false);}
 fn request(&self)->Result<(),ErrorCode>{let before=self.client.values.borrow().len();self.client.pending.set(self.client.pending.get()+1);
  let result=sensors::NineDof::read_magnetometer(self.driver);if result.is_err(){assert_eq!(self.client.values.borrow().len(),before,"CALLBACK: callback on rejected request");self.client.pending.set(self.client.pending.get()-1);}result}
 fn complete_until(&self,end:u64){self.advance(end,true);if self.client.pending.get()!=0{panic!("NOT_COMPLETED: healthy request has no callback");}self.advance((self.clock.get()+5000).min(end),false);}
 fn sample_until(&self,end:u64){executor::accept_request(self.clock, end, || self.request(), |until| self.advance(until, false), || self.client.values.borrow().len());self.complete_until(end.min(self.clock.get()+REQUEST));}
 fn sample(&self){self.sample_until(self.clock.get()+REQUEST);}
 fn progress(&self,v:&Vector){self.model.borrow_mut().input=*v;let end=self.clock.get()+PROGRESS;let mut seen=false;
  while self.clock.get()<end{self.sample_until(end);if self.client.values.borrow().last().map_or(false,|o|within_numeric_tolerance(o.value,v)){seen=true;break;}self.advance((self.clock.get()+10_000).min(end),false);}
  if !seen{panic!("NOT_COMPLETED: stable input change not reflected");}}
}
fn run(id:&str,scope:&str,f:impl FnOnce()){
 if std::env::args().nth(1).as_deref()!=Some(id){return;}
 let(status,message)=match catch_unwind(AssertUnwindSafe(f)){Ok(())=>("pass",String::new()),Err(e)=>{
 let s=e.downcast_ref::<String>().cloned().or_else(||e.downcast_ref::<&str>().map(|s|s.to_string())).unwrap_or_else(||"candidate panic".into());
 let status=if s.starts_with("NOT_COMPLETED:"){"not_completed"}else if s.starts_with("MODEL_UNSUPPORTED:"){"model_unsupported"}else if s.starts_with("NOT_EXERCISED:"){"not_exercised"}else{"fail"};(status,s)}};
 println!("CASE\t{}\t{}\t{}\t{}",id,scope,status,message.replace('\t'," ").replace('\n'," "));
}
fn main(){std::panic::set_hook(Box::new(|_|{}));
 run("__model/retained_data","internal",||{let mut m=Model::new(&VECTORS[1]);m.write_reg(100000,0x4b,1);for(r,v)in[(0x51,4),(0x52,14),(0x4c,0)]{m.write_reg(104000,r,v);}let mut b=[0;21];m.exchange(120000,&[0x42],8,&mut b);let first=b[..8].to_vec();assert!(!m.ready);m.exchange(120200,&[0x42],8,&mut b);assert_eq!(&b[..6],&first[..6]);assert_eq!(m.observations[0].mask,255);assert!(m.observations[0].valid);});
 run("__model/shadow_stop","internal",||{let mut m=Model::new(&VECTORS[1]);m.write_reg(100000,0x4b,1);for(r,v)in[(0x51,4),(0x52,14),(0x4c,0)]{m.write_reg(104000,r,v);}m.tick(120000);let due=m.next.unwrap();m.input=VECTORS[2];let mut b=[0;21];m.exchange(due+50,&[0x42],8,&mut b);assert_eq!(&b[..6],&VECTORS[1].bytes[..6]);m.tick(due+50);m.exchange(due+200,&[0x42],8,&mut b);assert_eq!(&b[..6],&VECTORS[2].bytes[..6]);});
 run("__model/callback_encoding","internal",||{for v in [i32::MIN,-123,0,i32::MAX]{assert_eq!(v as usize as u32 as i32,v);assert_eq!(v as u32 as usize as u32 as i32,v);}});
 for v in VECTORS{run(&format!("numeric/{}",v.id),"core",||{let r=Rig::new(v);r.init();r.sample();println!("OBS\tvalue\t{:?}",r.client.values.borrow());});}
 run("requests/first_available","core",||{let r=Rig::new(&VECTORS[1]);let _=r.driver.startup();let end=r.clock.get()+STARTUP;let mut accepted=false;
  while r.clock.get()<end{if r.request().is_ok(){accepted=true;break;}r.advance((r.clock.get()+100).min(end),false);}if !accepted{panic!("NOT_COMPLETED: initialization never accepts request");}r.complete_until(r.clock.get()+REQUEST);});
 run("requests/concurrent","core",||{let r=Rig::new(&VECTORS[1]);r.init();executor::accept_request(r.clock, r.clock.get()+REQUEST, || r.request(), |until| r.advance(until, false), || r.client.values.borrow().len());let _=r.request();r.complete_until(r.clock.get()+REQUEST);});
 run("requests/stable_changes","core",||{let r=Rig::new(&VECTORS[1]);r.init();r.sample();for v in [&VECTORS[2],&VECTORS[1],&VECTORS[5],&VECTORS[2]]{r.progress(v);}});
 for period in [92593u64,100000,108696]{run(&format!("timing/period_{}us",period),"core",||{let r=Rig::new(&VECTORS[1]);r.model.borrow_mut().period=period;r.init();r.sample();r.progress(&VECTORS[2]);});}
 for first in [9785u64,50000,108696]{run(&format!("timing/first_{}us",first),"core",||{let r=Rig::new(&VECTORS[1]);r.model.borrow_mut().first=first;r.init();r.sample();});}
 run("coherence/turnover","core",||{let r=Rig::new(&VECTORS[1]);r.init();let next=r.model.borrow().next.unwrap_or_else(||panic!("NOT_COMPLETED: conversion not configured"));r.clock.set(next-150);r.model.borrow_mut().input=VECTORS[2];r.sample();r.progress(&VECTORS[2]);});
 for kind in ["x_overflow","y_overflow","z_overflow"]{run(&format!("invalid/{}",kind),"core",||{let r=Rig::new(&VECTORS[1]);r.model.borrow_mut().invalid=Some(kind);r.init();let result=r.request();r.advance(r.clock.get()+REQUEST,false);assert!(r.client.values.borrow().is_empty(),"DATA: overflow code is not valid magnetic output");println!("OBS\toverflow\trequest={:?} pending={}",result,r.client.pending.get());});}
 for kind in ["hall_zero","zero_denominator"]{run(&format!("diagnostic/invalid/{}",kind),"diagnostic",||{let r=Rig::new(&VECTORS[1]);r.model.borrow_mut().invalid=Some(kind);if kind=="zero_denominator"{r.model.borrow_mut().trim[11..13].copy_from_slice(&(-10000i16).to_le_bytes());}r.init();let result=r.request();r.advance(r.clock.get()+REQUEST,false);println!("OBS\tinvalid\trequest={:?} pending={}",result,r.client.pending.get());});}
 for (name,offset,len) in [("z1",13,2),("z2",11,2),("xyz1",15,2)]{run(&format!("diagnostic/invalid_trim/{}",name),"diagnostic",||{let r=Rig::new(&VECTORS[1]);r.model.borrow_mut().trim[offset..offset+len].fill(0);r.init();let result=r.request();r.advance(r.clock.get()+REQUEST,false);println!("OBS\tinvalid_trim\t{:?}",result);});}
 run("diagnostic/identity","diagnostic",||{let r=Rig::new(&VECTORS[1]);r.model.borrow_mut().id=0x33;r.init();let result=r.request();r.advance(r.clock.get()+REQUEST,false);println!("OBS\tidentity\t{:?}",result);});
 run("diagnostic/stalled","diagnostic",||{let r=Rig::new(&VECTORS[1]);r.model.borrow_mut().stuck=true;r.init();let result=r.request();r.advance(r.clock.get()+REQUEST,false);println!("OBS\tstalled\t{:?} pending={}",result,r.client.pending.get());});
 for at in [FaultAt::Any,FaultAt::Data]{for asynchronous in [false,true]{run(&format!("diagnostic/request/{:?}/{}",at,asynchronous),"diagnostic",||{let r=Rig::new(&VECTORS[1]);r.init();r.bus.fault.set(Some(Fault{at,asynchronous}));let result=r.request();r.advance(r.clock.get()+REQUEST,false);if !r.bus.fault_hit.get(){panic!("NOT_EXERCISED: no matching bus operation");}println!("OBS\tfault\t{:?} pending={}",result,r.client.pending.get());});}}
 for(name,reg)in[("power",0x4b),("identity",0x40),("trim",0x5d),("configuration",0x51)]{for asynchronous in[false,true]{run(&format!("diagnostic/init/{}/{}",name,asynchronous),"diagnostic",||{let r=Rig::new(&VECTORS[1]);r.model.borrow_mut().fault_register=Some(reg);r.bus.fault.set(Some(Fault{at:FaultAt::Any,asynchronous}));r.init();if !r.bus.fault_hit.get(){panic!("NOT_EXERCISED: no matching init operation");}let result=r.request();r.advance(r.clock.get()+REQUEST,false);println!("OBS\tinit_fault\t{:?}",result);});}}
}
