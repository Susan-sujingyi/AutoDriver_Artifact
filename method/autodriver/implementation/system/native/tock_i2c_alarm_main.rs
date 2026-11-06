//! Actual candidate execution through prepared public Tock I2C/Alarm HILs.
mod observer;
#[path="@DRIVER_PATH@"] mod candidate;
use observer::{State,Bus,Alarm};
use std::cell::RefCell;
use std::rc::Rc;
use kernel::hil::time::Alarm as AlarmTrait;
use kernel::ErrorCode;
struct Client{clock:Rc<RefCell<State>>}
@CLIENT_IMPLEMENTATIONS@
fn pump(clock:&Rc<RefCell<State>>,bus:&Bus,alarm:&Alarm,driver:&impl kernel::hil::i2c::I2CClient,steps:&[&[u8]],fail:bool){
 let mut offset=0;let mut injected=false;
 for _ in 0..128 {
  let next=clock.borrow().next();
  match next {
   Some("io")=>{
    if offset>=steps.len(){clock.borrow_mut().record("executor_gap","\"reason\":\"candidate exceeded contract prefix I/O script\"".into());return;}
    let length=bus.read_length().unwrap();let data=if length==0{&[][..]}else{steps[offset]};
    if length!=data.len(){clock.borrow_mut().record("executor_gap",format!("\"reason\":\"candidate read length differs from contract script\",\"read_length\":{}",length));return;}
    let failed=fail&&!injected;injected|=failed;offset+=1;bus.complete(driver,data,failed);
   },
   Some("alarm")=>alarm.fire(),None=>return,_=>unreachable!(),
  }
 }
 clock.borrow_mut().record("executor_gap","\"reason\":\"bounded public scheduler did not quiesce\"".into());
}
fn main(){
 let cases:&[(&str,&str,&[&[u8]],bool,bool,bool)]=&[@CASES@];
 for (name,channel,steps,fail,reject,reenter) in cases {
  println!("{{\"phase\":\"case_begin\",\"case\":{:?}}}",name);
  let clock=Rc::new(RefCell::new(State::new(32768)));
  let bus=Box::leak(Box::new(Bus{state:clock.clone()}));let alarm=Box::leak(Box::new(Alarm::new(clock.clone())));
  let buffer=Box::leak(vec![0u8;@BUFFER_SIZE@].into_boxed_slice());
  let driver=Box::leak(Box::new(candidate::@DRIVER_TYPE@::new(bus,alarm,buffer)));
  alarm.set_alarm_client(driver);
  let client=Box::leak(Box::new(Client{clock:clock.clone()}));@SET_CLIENTS@
  let result=driver.startup();clock.borrow_mut().record("native_return",format!("\"request\":\"startup\",\"result\":\"{:?}\"",result));
  pump(&clock,bus,alarm,driver,&[@INITIAL_STEPS@],false);
  clock.borrow_mut().record("request_boundary",format!("\"request\":\"{}\",\"instance\":\"acquisition\"",channel));
  clock.borrow_mut().reject_next=*reject;
  let result=@REQUEST_MATCH@;
  clock.borrow_mut().record("native_return",format!("\"request\":\"{}\",\"accepted\":{},\"result\":\"{:?}\"",channel,result.is_ok(),result));
  if *reenter {
   let result=@REQUEST_MATCH@;
   clock.borrow_mut().record("native_return",format!("\"request\":\"{}_reentry\",\"accepted\":{},\"result\":\"{:?}\"",channel,result.is_ok(),result));
  }
  pump(&clock,bus,alarm,driver,steps,*fail);
  for event in &clock.borrow().events{println!("{}",event);}
  println!("{{\"phase\":\"case_end\"}}");
 }
}
