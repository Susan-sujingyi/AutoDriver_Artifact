//! Actual candidate linked with public SPI/Alarm/scalar-sensor HILs.
#[path="observer.rs"] mod clock;
mod spi_observer;
#[path="@DRIVER_PATH@"] mod candidate;
use clock::{State,Alarm};
use spi_observer::Bus;
use std::cell::RefCell;
use std::rc::Rc;
use kernel::hil::spi::SpiMasterDevice;
use kernel::hil::time::Alarm as AlarmTrait;
use kernel::ErrorCode;

struct Client { clock:Rc<RefCell<State>> }
@CLIENT_IMPLEMENTATIONS@

fn pump(clock:&Rc<RefCell<State>>,bus:&Bus,alarm:&Alarm,steps:&[&[u8]],fail:bool){
 let mut offset=0;let mut injected=false;
 for _ in 0..128 {
  let io=bus.next_due();let timer=clock.borrow().alarm_deadline();
  if io.is_some() && (timer.is_none() || io<=timer) {
   if offset>=steps.len(){clock.borrow_mut().record("executor_gap","\"reason\":\"candidate exceeded contract prefix I/O script\"".into());return;}
   let length=bus.read_length().unwrap();let data=if length==0{&[][..]}else{steps[offset]};
   if length!=data.len(){clock.borrow_mut().record("executor_gap",format!("\"reason\":\"candidate read length differs from contract script\",\"read_length\":{}",length));return;}
   let failed=fail&&!injected;injected|=failed;offset+=1;bus.complete(data,failed);
  } else if timer.is_some(){alarm.fire();}else{return;}
 }
 clock.borrow_mut().record("executor_gap","\"reason\":\"bounded public scheduler did not quiesce\"".into());
}

fn main(){
 let cases:&[(&str,&str,&[&[u8]],bool,bool,bool)]=&[@CASES@];
 for (name,channel,steps,fail,reject,reenter) in cases {
  println!("{{\"phase\":\"case_begin\",\"case\":{:?}}}",name);
  let clock=Rc::new(RefCell::new(State::new(32768)));
  let bus=Box::leak(Box::new(Bus::new(clock.clone())));let alarm=Box::leak(Box::new(Alarm::new(clock.clone())));
  let tx=Box::leak(vec![0u8;@BUFFER_SIZE@].into_boxed_slice());let rx=Box::leak(vec![0u8;@BUFFER_SIZE@].into_boxed_slice());
  let driver=Box::leak(Box::new(candidate::@DRIVER_TYPE@::new(bus,alarm,tx,rx)));
  bus.set_client(driver);alarm.set_alarm_client(driver);
  let client=Box::leak(Box::new(Client{clock:clock.clone()}));@SET_CLIENTS@
  let result=driver.startup();clock.borrow_mut().record("native_return",format!("\"request\":\"startup\",\"result\":\"{:?}\"",result));
  pump(&clock,bus,alarm,&[@INITIAL_STEPS@],false);
  clock.borrow_mut().record("request_boundary",format!("\"request\":\"{}\",\"instance\":\"acquisition\"",channel));
  bus.reject_next.set(*reject);
  let result=@REQUEST_MATCH@;clock.borrow_mut().record("native_return",format!("\"request\":\"{}\",\"accepted\":{},\"result\":\"{:?}\"",channel,result.is_ok(),result));
  if *reenter {let result=@REQUEST_MATCH@;clock.borrow_mut().record("native_return",format!("\"request\":\"{}_reentry\",\"accepted\":{},\"result\":\"{:?}\"",channel,result.is_ok(),result));}
  pump(&clock,bus,alarm,steps,*fail);
  for event in &clock.borrow().events{println!("{}",event);}
  println!("{{\"phase\":\"case_end\"}}");
 }
}
