//! Public Tock SPI device observer. Protocol bytes come from the current DBC.
use crate::clock::State;
use kernel::hil::spi::{SpiMasterClient,SpiMasterDevice,ClockPhase,ClockPolarity};
use kernel::ErrorCode;
use std::cell::{Cell,RefCell};
use std::rc::Rc;

struct Transfer {
 id:usize, write:&'static mut[u8], read:Option<&'static mut[u8]>, length:usize, due:u64,
}
pub struct Bus<'a> {
 pub clock:Rc<RefCell<State>>, client:Cell<Option<&'a dyn SpiMasterClient>>,
 pending:RefCell<Option<Transfer>>, serial:Cell<usize>, rate:Cell<u32>,
 polarity:Cell<ClockPolarity>, phase:Cell<ClockPhase>, pub reject_next:Cell<bool>,
}
impl<'a> Bus<'a> {
 pub fn new(clock:Rc<RefCell<State>>)->Self {
  Self {clock,client:Cell::new(None),pending:RefCell::new(None),serial:Cell::new(0),rate:Cell::new(500000),
   polarity:Cell::new(ClockPolarity::IdleLow),phase:Cell::new(ClockPhase::SampleLeading),reject_next:Cell::new(false)}
 }
 pub fn next_due(&self)->Option<u64>{self.pending.borrow().as_ref().map(|p|p.due)}
 pub fn read_length(&self)->Option<usize>{self.pending.borrow().as_ref().map(|p|if p.read.is_some(){p.length}else{0})}
 pub fn complete(&self,payload:&[u8],failed:bool){
  let mut pending=self.pending.borrow_mut().take().expect("no accepted SPI request");
  if !failed {
   if let Some(data)=pending.read.as_mut(){assert_eq!(payload.len(),pending.length);data[..pending.length].copy_from_slice(payload);}
   else{assert!(payload.is_empty());}
  }
  {
   let mut clock=self.clock.borrow_mut();clock.tick=clock.tick.max(pending.due);
   let tx=clock.buffer_id(pending.write);let rx=pending.read.as_ref().map(|b|clock.buffer_id(b));
   clock.record(if failed{"physical_failure"}else{"physical_success"},format!("\"operation\":{}",pending.id));
   clock.record("cs_release",format!("\"operation\":{},\"virtual\":true",pending.id));
   clock.record(if failed{"callback_failure"}else{"callback_success"},format!("\"operation\":{},\"write_buffer\":{},\"read_buffer\":{},\"length\":{},\"data\":{:?}",pending.id,tx,rx.map(|n|n.to_string()).unwrap_or("null".into()),pending.length,pending.read.as_ref().map(|b|&b[..pending.length]).unwrap_or(&[])));
  }
  self.client.get().expect("SPI client not registered").read_write_done(pending.write,pending.read,pending.length,if failed{Err(ErrorCode::FAIL)}else{Ok(())});
 }
 fn idle(&self)->Result<(),ErrorCode>{if self.pending.borrow().is_some(){Err(ErrorCode::BUSY)}else{Ok(())}}
 fn record_configuration(&self){self.clock.borrow_mut().record("spi_configuration",format!("\"mode\":{},\"rate\":{}",2*u8::from(self.polarity.get()==ClockPolarity::IdleHigh)+u8::from(self.phase.get()==ClockPhase::SampleTrailing),self.rate.get()));}
}
impl<'a> SpiMasterDevice<'a> for Bus<'a> {
 fn set_client(&self,client:&'a dyn SpiMasterClient){self.client.set(Some(client));}
 fn configure(&self,polarity:ClockPolarity,phase:ClockPhase,rate:u32)->Result<(),ErrorCode>{self.idle()?;if rate==0{return Err(ErrorCode::INVAL)};self.polarity.set(polarity);self.phase.set(phase);self.rate.set(rate);self.record_configuration();Ok(())}
 fn set_rate(&self,rate:u32)->Result<(),ErrorCode>{self.configure(self.polarity.get(),self.phase.get(),rate)}
 fn get_rate(&self)->u32{self.rate.get()}
 fn set_polarity(&self,polarity:ClockPolarity)->Result<(),ErrorCode>{self.configure(polarity,self.phase.get(),self.rate.get())}
 fn get_polarity(&self)->ClockPolarity{self.polarity.get()}
 fn set_phase(&self,phase:ClockPhase)->Result<(),ErrorCode>{self.configure(self.polarity.get(),phase,self.rate.get())}
 fn get_phase(&self)->ClockPhase{self.phase.get()}
 fn read_write_bytes(&self,write:&'static mut[u8],read:Option<&'static mut[u8]>,length:usize)->Result<(),(ErrorCode,&'static mut[u8],Option<&'static mut[u8]>)>{
  let id=self.serial.get()+1;self.serial.set(id);
  let actual=length.min(write.len()).min(read.as_ref().map(|b|b.len()).unwrap_or(usize::MAX));
  {
   let mut clock=self.clock.borrow_mut();let tx=clock.buffer_id(write);let rx=read.as_ref().map(|b|clock.buffer_id(b));
   clock.record("submit",format!("\"operation\":{},\"write_buffer\":{},\"read_buffer\":{},\"requested_length\":{},\"write_len\":{},\"read_len\":{},\"mode\":{},\"rate\":{},\"write\":{:?}",id,tx,rx.map(|n|n.to_string()).unwrap_or("null".into()),length,actual,if read.is_some(){actual}else{0},2*u8::from(self.polarity.get()==ClockPolarity::IdleHigh)+u8::from(self.phase.get()==ClockPhase::SampleTrailing),self.rate.get(),&write[..actual]));
   let error=if actual==0{Some(ErrorCode::INVAL)}else if self.pending.borrow().is_some()||self.reject_next.replace(false){Some(ErrorCode::BUSY)}else{None};
   if let Some(error)=error {clock.record("rejected",format!("\"operation\":{},\"write_buffer\":{},\"read_buffer\":{},\"error\":\"{:?}\"",id,tx,rx.map(|n|n.to_string()).unwrap_or("null".into()),error));return Err((error,write,read));}
   clock.record("accepted",format!("\"operation\":{}",id));clock.record("cs_assert",format!("\"operation\":{},\"virtual\":true",id));clock.record("physical_start",format!("\"operation\":{}",id));
   let due=clock.tick+1;*self.pending.borrow_mut()=Some(Transfer{id,write,read,length:actual,due});
  }
  Ok(())
 }
}
