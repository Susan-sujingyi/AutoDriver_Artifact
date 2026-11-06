//! Device-level evaluator. The wire is generated from independent test vectors.
extern crate self as kernel;
pub use tock_kernel::*;
include!("platform_hil.rs");
mod executor;
use kernel::hil::{gpio,time};
use kernel::hil::gpio::{Configure,Output};
use kernel::hil::time::{Alarm,Time};
use kernel::hil::sensors::{TemperatureDriver,HumidityDriver,TemperatureClient,HumidityClient};
use std::cell::{Cell,RefCell};
use std::panic::{catch_unwind,AssertUnwindSafe};
mod dht11;mod waveform;
use waveform::{Wire,ClockAlarm,Sample,Fault,Sampler};
type Driver=dht11::Dht11<'static,Wire,ClockAlarm>;
#[derive(Clone,Copy)]
struct Vector{id:&'static str,temperature:u8,humidity:u8,expected_t:i32,expected_h:usize}
include!("vectors.rs");
struct Client{wire:&'static Wire,values:RefCell<Vec<(bool,Result<i64,ErrorCode>)>>}
impl TemperatureClient for Client{fn callback(&self,v:Result<i32,ErrorCode>){if let Ok(x)=v{assert!(self.wire.acquired.borrow().iter().any(|s|i32::from(s.temperature)*100==x),"NUMERIC:temperature not from a complete valid frame: {x}");}self.values.borrow_mut().push((false,v.map(i64::from)));}}
impl HumidityClient for Client{fn callback(&self,v:usize){assert!(self.wire.acquired.borrow().iter().any(|s|usize::from(s.humidity)*100==v),"NUMERIC:humidity not from a complete valid frame: {v}");self.values.borrow_mut().push((true,Ok(v as i64)));}}
struct Rig{clock:&'static Cell<u64>,wire:&'static Wire,alarm:&'static ClockAlarm,sampler:&'static Sampler,driver:&'static Driver,client:&'static Client}
impl Rig{
 fn new(v:Vector)->Self{
  let clock=Box::leak(Box::new(Cell::new(0)));let wire=Box::leak(Box::new(Wire::new(clock,Sample{temperature:v.temperature,humidity:v.humidity})));
  let alarm=Box::leak(Box::new(ClockAlarm{clock,due:Cell::new(None),client:Cell::new(None)}));let sampler=Box::leak(Box::new(Sampler::new(wire)));let driver=Box::leak(Box::new(Driver::new(wire,alarm)));driver.set_sampler(sampler,Box::leak(vec![0;2048].into_boxed_slice()));gpio::Interrupt::set_client(wire,driver);alarm.set_alarm_client(driver);
  let client=Box::leak(Box::new(Client{wire,values:RefCell::new(Vec::new())}));TemperatureDriver::set_client(driver,client);HumidityDriver::set_client(driver,client);Self{clock,wire,alarm,sampler,driver,client}
 }
 fn advance(&self,end:u64){
        executor::drive(self.clock, end, executor::EVENT_LIMIT, &[
            executor::Event { name: "wire", next: &|| self.wire.due(), fire: &|| self.wire.edge() },
            executor::Event { name: "sampler", next: &|| self.sampler.due.get(), fire: &|| self.sampler.fire() },
            executor::Event { name: "alarm", next: &|| self.alarm.due.get(), fire: &|| self.alarm.fire() },
        ], || false, |_| {});
    }
 fn init(&self){let n=self.count();let _=self.driver.startup();self.advance(self.clock.get()+1500000);assert_eq!(self.count(),n,"CALLBACK:unsolicited init result");}
 fn count(&self)->usize{self.client.values.borrow().len()}
 fn submit(&self,h:bool)->Result<(),ErrorCode>{if h{self.driver.read_humidity()}else{self.driver.read_temperature()}}
 fn request(&self,h:bool)->i64{
  let n=self.count();let end=self.clock.get()+3000000;
  executor::accept_request(self.clock,end,||self.submit(h),|until|self.advance(until),||self.count());self.advance(end);
  if self.count()==n{panic!("NOT_COMPLETED:accepted request without result");}
  assert_eq!(self.count(),n+1,"CALLBACK:more than one result");let(x,v)=self.client.values.borrow()[n];assert_eq!(x,h,"CALLBACK:wrong channel");v.expect("DATA:normal sensor produced error")
 }
}
fn model_test(id:&str){
 let clock=Box::leak(Box::new(Cell::new(1100000)));
 let v=VECTORS[0]; let wire=Wire::new(clock,Sample{temperature:v.temperature,humidity:v.humidity});
 let alarm=ClockAlarm{clock,due:Cell::new(None),client:Cell::new(None)};
 if id=="model/clock_wrap"{clock.set(u32::MAX as u64-20);alarm.set_alarm(alarm.now(),time::Ticks32::from(50));assert_eq!(alarm.due.get(),Some(u32::MAX as u64+30));return;}
 let send=|duration:u64|{wire.clear();wire.make_output();clock.set(clock.get()+duration);wire.make_input();};
 if id=="model/early_start"{clock.set(0);assert!(catch_unwind(AssertUnwindSafe(||send(19000))).is_err());return;}
 if id=="model/short_start"{assert!(catch_unwind(AssertUnwindSafe(||send(1000))).is_err());return;}
 send(19000);while wire.due().is_some(){wire.edge();}
 assert_eq!(wire.acquired.borrow().len(),1);assert_eq!(wire.edges.get(),84);assert_eq!(wire.acquired.borrow()[0].humidity,53);assert_eq!(wire.acquired.borrow()[0].temperature,24);
 if id=="model/min_interval"{assert!(catch_unwind(AssertUnwindSafe(||send(19000))).is_err());}
}
fn run(id:&str){
 if id.starts_with("model/"){model_test(id);return;}
 let v=if id.starts_with("numeric/"){let name=id.split('/').nth(1).unwrap();*VECTORS.iter().find(|v|v.id==name).unwrap()}else{VECTORS[0]};let r=Rig::new(v);
 if id=="time/wraparound"{r.clock.set(u32::MAX as u64-1520000);}
 match id{"protocol/short_zero"=>r.wire.zero_us.set(26),"protocol/long_zero"=>r.wire.zero_us.set(28),"protocol/short_one"=>r.wire.one_us.set(68),"protocol/long_one"=>r.wire.one_us.set(72),"protocol/edge_delay"=>r.wire.edge_delay.set(5),_=>()}
 if id=="requests/early"{let a=r.submit(false);if a.is_err(){assert_eq!(r.count(),0);}else{r.advance(3000000);assert_eq!(r.count(),1,"CALLBACK:early accepted request");}r.init();r.request(false);return;}
 r.init();
 if id.starts_with("integrity/"){
  r.wire.fault.set(Fault::Checksum);let h=id.ends_with("humidity");let n=r.count();let a=r.submit(h);r.advance(r.clock.get()+3000000);
  assert!(a.is_ok()&&r.wire.starts.get()>0,"NOT_COMPLETED:checksum path not exercised");
  // The raw manual requires discarding a corrupt frame. Silence or a supported
  // error callback are both legal; a numeric success from that frame is not.
  assert!(r.client.values.borrow()[n..].iter().all(|(_,v)|v.is_err()),"DATA:corrupt frame reported as success");return;
 }
 if id.starts_with("capture/") {
  if id=="capture/deferred_completion" {
   let now=r.clock.get();assert!(r.submit(false).is_ok());assert_eq!(r.clock.get(),now);assert_eq!(r.count(),0);
   while r.clock.get()<now+1000000 && r.sampler.due.get().is_none() && r.count()==0 {r.advance(r.clock.get()+100);}
   assert!(r.sampler.due.get().is_some(),"NOT_EXERCISED:alternative receiver does not use capture service");
   assert_eq!(r.count(),0,"CALLBACK:completed before sample window");
   r.advance(r.clock.get()+100000);assert_eq!(r.count(),1);return;
  }
  if id=="capture/delayed_delivery"{r.sampler.delay.set(5000);r.request(false);return;}
  if id=="capture/short_transfer"{r.sampler.short.set(true);}else if id=="capture/cancel_recovery"{r.sampler.delay.set(50000);}else{r.sampler.fail.set(true);}
  let n=r.count();assert!(r.submit(false).is_ok());r.advance(r.clock.get()+100000);
  assert!(r.sampler.delivered.get()>0,"NOT_EXERCISED:no capture completion");
  assert!(r.client.values.borrow()[n..].iter().all(|(_,v)|v.is_err()),"DATA:invalid transport reported success");
  r.sampler.short.set(false);r.sampler.fail.set(false);r.sampler.delay.set(0);r.request(false);return;
 }
 if id.starts_with("numeric/"){let h=id.ends_with("/humidity");assert_eq!(r.request(h),if h{v.expected_h as i64}else{v.expected_t as i64},"NUMERIC:wrong output unit/value");return;}
 if id.starts_with("protocol/")||id=="time/wraparound"{r.request(false);r.request(true);return;}
 match id{
 "requests/repeated"=>{for i in 0..6{r.request(i%2==1);}},
 "requests/rapid_alternation"=>{
  for i in 0..6 {
   let n=r.count();let end=r.clock.get()+3000000;let mut accepted=false;
   while r.clock.get()<end {if r.submit(i%2==1).is_ok(){accepted=true;break;}r.advance(r.clock.get()+1000);assert_eq!(r.count(),n,"CALLBACK:rejected rapid request");}
   assert!(accepted,"NOT_COMPLETED:rapid request rejected indefinitely");
   while r.count()==n&&r.clock.get()<end {r.advance((r.clock.get()+500).min(end));}
   if r.count()==n{panic!("NOT_COMPLETED:rapid request missing result");}
   assert_eq!(r.count(),n+1,"CALLBACK:duplicate rapid result");
   let (h,v)=r.client.values.borrow()[n];assert_eq!(h,i%2==1,"CALLBACK:wrong rapid channel");assert!(v.is_ok(),"DATA:rapid request error on normal wire");
  }
 },
 "requests/concurrent"=>{let n=r.count();let a=r.submit(false);let b=r.submit(true);let accepted=usize::from(a.is_ok())+usize::from(b.is_ok());assert!(accepted>0,"NOT_COMPLETED:no accepted requests");r.advance(r.clock.get()+4000000);if r.count()-n<accepted{panic!("NOT_COMPLETED:lost accepted request");}assert_eq!(r.count()-n,accepted,"CALLBACK:duplicate");if a.is_ok(){assert!(r.client.values.borrow()[n..].iter().any(|x|!x.0),"CALLBACK:missing temperature");}if b.is_ok(){assert!(r.client.values.borrow()[n..].iter().any(|x|x.0),"CALLBACK:missing humidity");}r.request(false);},
 "requests/no_unsolicited"=>{r.advance(r.clock.get()+3000000);assert_eq!(r.count(),0,"CALLBACK:unsolicited");r.request(false);let n=r.count();r.advance(r.clock.get()+3000000);assert_eq!(r.count(),n,"CALLBACK:unsolicited");},
 "data/stable_change"=>{r.request(false);r.request(true);let s=VECTORS[4];r.wire.sample.set(Sample{temperature:s.temperature,humidity:s.humidity});let mut changed_t=false;let mut changed_h=false;for _ in 0..3{changed_t|=r.request(false)==s.expected_t as i64;changed_h|=r.request(true)==s.expected_h as i64;}assert!(changed_t&&changed_h,"DATA:never observes stable changed input");},
 x if x.starts_with("fault/")=>{
  let fault=match x{"fault/no_response"=>Fault::NoResponse,"fault/truncated"=>Fault::Truncate,"fault/stuck_low"=>Fault::StuckLow,_=>Fault::Checksum};r.wire.fault.set(fault);let a=r.submit(false);r.advance(r.clock.get()+3000000);println!("OBS\trequest={a:?} values={:?} starts={}",r.client.values.borrow(),r.wire.starts.get());assert!(r.wire.starts.get()>0,"NOT_EXERCISED:no wire transaction");if x=="fault/recovery"{r.wire.fault.set(Fault::None);let value=r.request(false);println!("OBS\trecovered value={value}");}
 },_=>panic!("unknown case")}
}
fn main(){let id=std::env::args().nth(1).unwrap();let scope=if id.starts_with("model/"){"internal"}else if id.starts_with("fault/")||id.starts_with("capture/")||["protocol/short_one","protocol/long_one","protocol/edge_delay"].contains(&id.as_str()){"diagnostic"}else{"core"};let result=catch_unwind(AssertUnwindSafe(||run(&id)));let(status,detail)=match result{Ok(())=>("pass",String::new()),Err(e)=>{let s=e.downcast_ref::<String>().cloned().or_else(||e.downcast_ref::<&str>().map(|x|x.to_string())).unwrap_or_else(||"panic".into());let status=if s.contains("NOT_COMPLETED:"){"not_completed"}else if s.contains("NOT_EXERCISED:"){"not_exercised"}else if s.contains("MODEL_UNSUPPORTED:"){"model_unsupported"}else{"fail"};(status,s.replace(['\n','\t']," "))}};println!("CASE\t{id}\t{scope}\t{status}\t{detail}");}
