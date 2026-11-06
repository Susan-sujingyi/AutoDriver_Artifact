//! ADT7310 RevA finite digital model, independent of source/reference arithmetic.
mod executor;
mod task_config;
use kernel::hil::sensors::{TemperatureClient, TemperatureDriver};
use kernel::hil::spi::{ClockPolarity, ClockPhase, SpiMasterDevice, SpiMasterClient};
use kernel::hil::time::Alarm;
use kernel::ErrorCode;
use std::cell::{Cell, RefCell};
use std::panic::{catch_unwind,AssertUnwindSafe};
mod adt7310; mod support; mod spi_support;
use support::{FakeAlarm,Fault,FaultAt};
use spi_support::{FakeSpi,SpiDeviceModel};
type Bus=FakeSpi<Model>;
type Driver=adt7310::Adt7310<'static,Bus,FakeAlarm>;
#[derive(Clone,Copy)]
struct Vector{id:&'static str,raw:i16,lower:i64,upper:i64}
include!("vectors.rs");
struct Model{config:u8,input:Vector,sample:Vector,started:u64,first:u64,generation:u64,ready:bool,stall:bool,reset_until:u64,acquired:Vec<Vector>,thresholds:[[u8;2];4]}
impl Model{
 fn new(v:Vector)->Self{Self{config:0,input:v,sample:VECTORS[0],started:0,first:6000,generation:0,ready:false,stall:false,reset_until:0,acquired:Vec::new(),thresholds:[[0x49,0x80],[0,5],[0x20,0],[5,0]]}}
 fn tick(&mut self,now:u64){if !self.stall&&self.config&0x60==0&&now>=self.started+self.first&&now>=self.reset_until{let g=1+(now-self.started-self.first)/240000;if g>self.generation{self.sample=self.input;self.generation=g;self.ready=true;}}}
 fn required(&self){assert_eq!(self.config&0xe0,0x80,"CONFIG:16-bit continuous conversion required");}
}
impl SpiDeviceModel for Model{
 fn validate_bus(&self,p:ClockPolarity,h:ClockPhase,rate:u32){task_config::validate_spi(p, h, rate);}
 fn fault_matches(&self,at:FaultAt,tx:&[u8])->bool{match at{FaultAt::Any=>true,FaultAt::Data=>tx.first().map_or(false,|x|x&0x78==0x50),FaultAt::Configuration=>tx.first().map_or(false,|x|x&0x78==8)}}
 fn exchange(&mut self,now:u64,tx:&[u8],rx:&mut[u8]){
  self.tick(now);rx.fill(0);
  if tx.len()>=4&&tx.iter().all(|x|*x==0xff){self.config=0;self.ready=false;self.generation=0;self.started=now;self.reset_until=now+500;return}
  if now<self.reset_until{panic!("MODEL_UNSUPPORTED:access during reset settling");}
  assert!(!tx.is_empty(),"PROTOCOL:empty command");let cmd=tx[0];
  if cmd&4!=0{panic!("MODEL_UNSUPPORTED:continuous SPI read mode");}
  if cmd&0x83!=0{panic!("MODEL_UNSUPPORTED:reserved command bits");}
  let reg=(cmd>>3)&7;let read=cmd&0x40!=0;
  if read{
   if tx.len()<2{return}
   match reg{
    0=>rx[1]=if self.ready{0}else{0x80},
    1=>rx[1]=self.config,
    2=>{let raw=if self.config&0x80!=0{self.sample.raw}else{self.sample.raw& !7};let bytes=raw.to_be_bytes();rx[1]=bytes[0];if tx.len()>=3{rx[2]=bytes[1];self.ready=false;if self.generation>0{self.acquired.push(self.sample);}}},
    3=>rx[1]=0xc3,
    4..=7=>{let v=self.thresholds[(reg-4)as usize];if reg==5{rx[1]=v[1]}else{rx[1]=v[0];if tx.len()>=3{rx[2]=v[1]}}},
    _=>unreachable!(),
   }
  }else if tx.len()>=2{match reg{1=>{self.config=tx[1];if self.config&0x60!=0{panic!("MODEL_UNSUPPORTED:non-continuous conversion mode");}},4..=7=>{if reg==5{self.thresholds[1]=[0,tx[1]&15]}else if tx.len()>=3{self.thresholds[(reg-4)as usize]=[tx[1],tx[2]]}},_=>{}}}
 }
}
struct Client{values:RefCell<Vec<Result<i32,ErrorCode>>>,model:&'static RefCell<Model>}
impl TemperatureClient for Client{
 fn callback(&self,v:Result<i32,ErrorCode>){if let Ok(x)=v{let m=self.model.borrow();m.required();assert!(m.acquired.iter().any(|s|x as i64>=s.lower&&x as i64<=s.upper),"NUMERIC: result must match acquired sample: {x}");}self.values.borrow_mut().push(v);}
}
struct Rig{clock:&'static Cell<u64>,model:&'static RefCell<Model>,bus:&'static Bus,alarm:&'static FakeAlarm,driver:&'static Driver,client:&'static Client}
impl Rig{
 fn new(v:Vector)->Self{let clock=Box::leak(Box::new(Cell::new(0)));let model=Box::leak(Box::new(RefCell::new(Model::new(v))));let bus=Box::leak(Box::new(Bus::new(clock,model)));let alarm=Box::leak(Box::new(FakeAlarm{clock,due:Cell::new(None),client:Cell::new(None)}));let driver=Box::leak(Box::new(Driver::new(bus,alarm,Box::leak(Box::new([0;16])),Box::leak(Box::new([0;16])))));bus.set_client(driver);alarm.set_alarm_client(driver);let client=Box::leak(Box::new(Client{values:RefCell::new(Vec::new()),model}));driver.set_client(client);Self{clock,model,bus,alarm,driver,client}}
 fn advance(&self,end:u64){
        executor::drive(self.clock, end, executor::EVENT_LIMIT, &[
            executor::Event { name: "bus", next: &|| self.bus.pending.borrow().as_ref().map(|p| p.due), fire: &|| self.bus.complete() },
            executor::Event { name: "alarm", next: &|| self.alarm.due.get(), fire: &|| self.alarm.fire() },
        ], || false, |now| self.model.borrow_mut().tick(now));
    }
 fn init(&self){let _=self.driver.startup();self.advance(250000);assert_eq!(self.count(),0,"CALLBACK:unsolicited initialization result");}
 fn count(&self)->usize{self.client.values.borrow().len()}
 fn request(&self)->i32{let n=self.count();let end=self.clock.get()+1000000;executor::accept_request(self.clock,end,||self.driver.read_temperature(),|until|self.advance(until),||self.count());self.advance(end);if self.count()==n{panic!("NOT_COMPLETED:missing callback");}assert_eq!(self.count(),n+1,"CALLBACK:duplicate completion");self.client.values.borrow()[n].expect("DATA:successful sensor conversion expected")}
}
struct SpiClient{calls:Cell<usize>,len:Cell<usize>,tx:Cell<usize>,rx:Cell<usize>}
impl SpiMasterClient for SpiClient{fn read_write_done(&self,t:&'static mut[u8],r:Option<&'static mut[u8]>,n:usize,s:Result<(),ErrorCode>){assert!(s.is_ok());self.calls.set(self.calls.get()+1);self.len.set(n);self.tx.set(t.as_ptr() as usize);self.rx.set(r.map_or(0,|b|b.as_ptr()as usize));}}
fn run(id:&str){
 if let Some(n)=id.strip_prefix("numeric/"){let v=*VECTORS.iter().find(|v|v.id==n).unwrap();let r=Rig::new(v);r.init();r.request();return}
 if id.starts_with("model/"){let v=VECTORS[2];let mut m=Model::new(v);m.config=0x80;let mut rx=[0;3];match id{
 "model/signed_data"=>{m.exchange(10000,&[0x50,0,0],&mut rx);assert_eq!(i16::from_be_bytes([rx[1],rx[2]]),v.raw);assert_eq!(m.acquired.len(),1);},
 "model/status_clear"=>{m.exchange(10000,&[0x40,0],&mut rx[..2]);assert_eq!(rx[1],0);m.exchange(10000,&[0x50,0,0],&mut rx);m.exchange(10000,&[0x40,0],&mut rx[..2]);assert_eq!(rx[1],0x80);},
 "model/reset"=>{m.exchange(10000,&[255;4],&mut[0;4]);assert_eq!(m.config,0);assert!(!m.ready);assert_eq!(m.reset_until,10500);},
 "model/spi_buffers"=>{let clock=Box::leak(Box::new(Cell::new(10000)));let model=Box::leak(Box::new(RefCell::new(m)));let bus=FakeSpi::new(clock,model);let client=Box::leak(Box::new(SpiClient{calls:Cell::new(0),len:Cell::new(0),tx:Cell::new(0),rx:Cell::new(0)}));bus.set_client(client);bus.configure(task_config::SPI_POLARITY,task_config::SPI_PHASE,1000000).unwrap();let tx=Box::leak(Box::new([0x40,0,0,0]));let rx=Box::leak(Box::new([0;2]));let tp=tx.as_ptr()as usize;let rp=rx.as_ptr()as usize;bus.read_write_bytes(tx,Some(rx),16).unwrap();assert_eq!(bus.set_rate(500000),Err(ErrorCode::BUSY));let t2=Box::leak(Box::new([0x40,0]));let p2=t2.as_ptr()as usize;let (e,t2,_)=bus.read_write_bytes(t2,None,2).unwrap_err();assert_eq!(e,ErrorCode::BUSY);assert_eq!(t2.as_ptr()as usize,p2);bus.complete();assert_eq!((client.calls.get(),client.len.get(),client.tx.get(),client.rx.get()),(1,2,tp,rp));bus.read_write_bytes(t2,None,2).unwrap();bus.complete();assert_eq!(client.rx.get(),0);assert_eq!(client.calls.get(),2);},
 _=>panic!("unknown selfcheck")};return}
 let r=Rig::new(VECTORS[2]);if id=="transport/async_config"{r.bus.fault.set(Some(Fault{at:FaultAt::Configuration,asynchronous:true}));}if id=="protocol/delayed_ready"{r.model.borrow_mut().first=600000;}
 if id=="requests/early"{let _=r.driver.startup();let a=r.driver.read_temperature();r.advance(1000000);if a.is_err(){assert_eq!(r.count(),0,"CALLBACK:after rejected request");}else{if r.count()==0{panic!("NOT_COMPLETED:early request");}assert_eq!(r.count(),1,"CALLBACK:early duplicate");}r.request();return}
 r.init();match id{
 "requests/repeated"=>{for _ in 0..5{r.request();}},
 "requests/concurrent"=>{r.request();let n=r.count();let a=r.driver.read_temperature();let b=r.driver.read_temperature();let accepted=usize::from(a.is_ok())+usize::from(b.is_ok());assert!(accepted>0,"NOT_COMPLETED:all requests rejected");r.advance(r.clock.get()+2000000);if r.count()-n<accepted{panic!("NOT_COMPLETED:concurrent request");}assert_eq!(r.count()-n,accepted,"CALLBACK:duplicate");r.request();},
 "requests/no_unsolicited"=>{r.advance(500000);assert_eq!(r.count(),0,"CALLBACK:unsolicited");r.request();let n=r.count();r.advance(r.clock.get()+500000);assert_eq!(r.count(),n,"CALLBACK:unsolicited");},
 "data/stable_change"=>{r.request();r.model.borrow_mut().input=VECTORS[1];let end=r.clock.get()+2000000;let mut changed=false;while r.clock.get()<end{let x=r.request()as i64;if x>=VECTORS[1].lower&&x<=VECTORS[1].upper{changed=true;break}}assert!(changed,"DATA:unchanging cached result");},
 "protocol/configuration"|"protocol/delayed_ready"=>{r.request();r.model.borrow().required();},
 "transport/async_config"=>{assert!(r.bus.fault_hit.get(),"NOT_EXERCISED:no config transfer");println!("OBS\tfault reached; no mandated initialization recovery");},
 "device/stalled"=>{r.model.borrow_mut().stall=true;r.model.borrow_mut().ready=false;let v=r.driver.read_temperature();r.advance(r.clock.get()+2000000);println!("OBS\tstalled {v:?} callbacks={}",r.count());},
 "transport/immediate_data"|"transport/async_data"|"transport/repeated_after_fault"=>{r.bus.fault.set(Some(Fault{at:FaultAt::Data,asynchronous:id!="transport/immediate_data"}));let v=r.driver.read_temperature();r.advance(r.clock.get()+1000000);assert!(r.bus.fault_hit.get(),"NOT_EXERCISED:no matching transfer");println!("OBS\tfault reached request={v:?} callbacks={}",r.count());if id=="transport/repeated_after_fault"{let v=r.driver.read_temperature();r.advance(r.clock.get()+1000000);println!("OBS\tafter fault {v:?} callbacks={}",r.count());}},
 _=>panic!("unknown case")}
}
fn main(){let id=std::env::args().nth(1).expect("case id");let scope=if id.starts_with("model/"){"internal"}else if id.starts_with("transport/")||id=="device/stalled"||id=="protocol/delayed_ready"{"diagnostic"}else{"core"};let outcome=catch_unwind(AssertUnwindSafe(||run(&id)));let(status,detail)=match outcome{Ok(())=>("pass",String::new()),Err(e)=>{let s=e.downcast_ref::<String>().cloned().or_else(||e.downcast_ref::<&str>().map(|s|s.to_string())).unwrap_or_else(||"panic".into());let status=if s.contains("MODEL_UNSUPPORTED:"){"model_unsupported"}else if s.contains("NOT_COMPLETED:"){"not_completed"}else if s.contains("NOT_EXERCISED:"){"not_exercised"}else{"fail"};(status,s.replace(['\n','\t']," "))}};println!("CASE\t{id}\t{scope}\t{status}\t{detail}");}
