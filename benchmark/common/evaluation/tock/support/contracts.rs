//! Platform contracts linked against the same frozen native kernel as candidates.
mod executor;
mod support;
use executor::{drive, Event};
use kernel::deferred_call::{DeferredCall, DeferredCallClient};
use kernel::hil::time::{Alarm, AlarmClient, Ticks32, Time};
use std::cell::{Cell, RefCell};

#[test]
fn equal_deadlines_follow_declared_order_in_both_legal_orders() {
    for reverse in [false, true] {
        let clock = Cell::new(0);
        let a = Cell::new(Some(10)); let b = Cell::new(Some(10));
        let log = RefCell::new(Vec::new());
        let an = || a.get(); let bn = || b.get();
        let af = || { a.set(None); log.borrow_mut().push('a'); };
        let bf = || { b.set(None); log.borrow_mut().push('b'); };
        let mut events = [Event {name:"a",next:&an,fire:&af}, Event {name:"b",next:&bn,fire:&bf}];
        if reverse { events.reverse(); }
        assert_eq!(drive(&clock, 20, 10, &events, || false, |_| {}), 2);
        assert_eq!(*log.borrow(), if reverse {vec!['b','a']} else {vec!['a','b']});
        assert_eq!(clock.get(), 20);
    }
}

#[test]
fn dispatch_rechecks_cancel_and_rearm() {
    let clock = Cell::new(0); let a = Cell::new(Some(10)); let b = Cell::new(Some(10));
    let calls = Cell::new(0);
    drive(&clock, 30, 10, &[
        Event {name:"cancel", next:&|| a.get(), fire:&|| {
            calls.set(calls.get()+1); b.set(None);
            a.set(if calls.get() == 1 {Some(20)} else {None});
        }},
        Event {name:"cancelled", next:&|| b.get(), fire:&|| panic!("cancelled callback")},
    ], || false, |_| {});
    assert_eq!(calls.get(), 2);
}

#[test]
fn overdue_events_and_old_observation_end_do_not_rewind_time() {
    let clock = Cell::new(20); let due = Cell::new(Some(10));
    drive(&clock, 5, 10, &[Event {name:"late", next:&|| due.get(), fire:&|| {
        assert_eq!(clock.get(), 20); due.set(None);
    }}], || false, |_| {});
    assert_eq!(clock.get(), 20);
}

#[test]
fn callback_time_past_observation_end_never_rewinds() {
    let clock=Cell::new(0);let due=Cell::new(Some(5));
    drive(&clock,10,10,&[Event {name:"interrupt_latency",next:&||due.get(),fire:&||{
        due.set(None);clock.set(15);
    }}],||false,|_|{});
    assert_eq!(clock.get(),15);
}

struct I2cModel;
impl support::DeviceModel for I2cModel {
    fn exchange(&mut self,_:u64,_:&[u8],_:usize,b:&mut[u8]){b[0]=42;}
    fn fault_matches(&self,_:support::FaultAt,_:&[u8],_:usize)->bool{true}
}
struct I2cClient {calls:Cell<usize>,pointer:Cell<usize>,error:Cell<Option<kernel::hil::i2c::Error>>}
impl kernel::hil::i2c::I2CClient for I2cClient {
    fn command_complete(&self,b:&'static mut[u8],r:Result<(),kernel::hil::i2c::Error>){
        self.calls.set(self.calls.get()+1);self.pointer.set(b.as_ptr() as usize);self.error.set(r.err());
    }
}
#[test]
fn native_i2c_rejection_returns_buffer_and_async_fault_completes_once() {
    use kernel::hil::i2c::{I2CDevice,Error};
    let clock=Box::leak(Box::new(Cell::new(0)));
    let model=Box::leak(Box::new(RefCell::new(I2cModel)));
    let client=Box::leak(Box::new(I2cClient{calls:Cell::new(0),pointer:Cell::new(0),error:Cell::new(None)}));
    let bus=support::FakeI2c{clock,model,pending:RefCell::new(None),client:Cell::new(Some(client)),
        fault:Cell::new(None),fault_hit:Cell::new(false),enabled:Cell::new(false)};
    bus.enable();
    let a=Box::leak(Box::new([0;2]));let ap=a.as_ptr() as usize;
    bus.write_read(a,1,1).unwrap();
    let b=Box::leak(Box::new([0;2]));let bp=b.as_ptr() as usize;
    let (error,b)=bus.write(b,1).unwrap_err();assert_eq!(error,Error::Busy);
    assert_eq!(b.as_ptr() as usize,bp);assert_eq!(client.calls.get(),0);
    bus.complete();assert_eq!(client.calls.get(),1);assert_eq!(client.pointer.get(),ap);
    bus.fault.set(Some(support::Fault{at:support::FaultAt::Any,asynchronous:false}));
    let (_,b)=bus.write(b,1).unwrap_err();assert_eq!(b.as_ptr() as usize,bp);
    assert_eq!(client.calls.get(),1);assert!(bus.pending.borrow().is_none());
    bus.fault.set(Some(support::Fault{at:support::FaultAt::Any,asynchronous:true}));
    bus.write(b,1).unwrap();assert_eq!(client.calls.get(),1);
    bus.complete();assert_eq!(client.calls.get(),2);assert_eq!(client.pointer.get(),bp);
    assert_eq!(client.error.get(),Some(Error::AddressNak));assert!(bus.pending.borrow().is_none());
}

#[test]
fn real_bus_and_alarm_complete_in_either_legal_equal_deadline_order() {
    use kernel::hil::i2c::I2CDevice;
    for reverse in [false, true] {
        let clock=Box::leak(Box::new(Cell::new(0)));
        let model=Box::leak(Box::new(RefCell::new(I2cModel)));
        let client=Box::leak(Box::new(I2cClient{calls:Cell::new(0),pointer:Cell::new(0),error:Cell::new(None)}));
        let bus=support::FakeI2c{clock,model,pending:RefCell::new(None),client:Cell::new(Some(client)),
            fault:Cell::new(None),fault_hit:Cell::new(false),enabled:Cell::new(false)};
        let counter=Box::leak(Box::new(Counter(Cell::new(0))));
        let alarm=support::FakeAlarm{clock,due:Cell::new(None),client:Cell::new(Some(counter))};
        bus.enable();
        let buffer=Box::leak(Box::new([0;2]));let pointer=buffer.as_ptr() as usize;
        bus.write_read(buffer,1,1).unwrap();
        alarm.set_alarm(Ticks32::from(0),Ticks32::from(100));
        let order=RefCell::new(Vec::new());
        let next_bus=||bus.pending.borrow().as_ref().map(|p|p.due);
        let next_alarm=||alarm.due.get();
        let complete_bus=||{bus.complete();order.borrow_mut().push("bus");};
        let complete_alarm=||{alarm.fire();order.borrow_mut().push("alarm");};
        let mut events=[Event{name:"bus",next:&next_bus,fire:&complete_bus},
                        Event{name:"alarm",next:&next_alarm,fire:&complete_alarm}];
        if reverse {events.reverse();}
        assert_eq!(drive(clock,200,10,&events,||client.calls.get()==1&&counter.0.get()==1,|_|{}),2);
        assert_eq!(*order.borrow(),if reverse{vec!["alarm","bus"]}else{vec!["bus","alarm"]});
        assert_eq!(clock.get(),100);assert_eq!(client.pointer.get(),pointer);
        assert!(bus.pending.borrow().is_none());assert!(!alarm.is_armed());
    }
}

type VirtualAlarm=capsules_core::virtualizers::virtual_alarm::VirtualMuxAlarm<'static,support::FakeAlarm>;
struct RearmingAlarm {alarm:&'static VirtualAlarm,calls:Cell<usize>}
impl AlarmClient for RearmingAlarm {
    fn alarm(&self) {
        self.calls.set(self.calls.get()+1);
        if self.calls.get()==1 {self.alarm.set_alarm(self.alarm.now(),Ticks32::from(25));}
    }
}

#[test]
fn frozen_alarm_mux_handles_wrap_reentrant_rearm_and_other_client_cancellation() {
    use capsules_core::virtualizers::virtual_alarm::{MuxAlarm,VirtualMuxAlarm};
    for reverse_setup in [false,true] {
        let start=0xfffffff0u64;
        let clock=Box::leak(Box::new(Cell::new(start)));
        let physical=Box::leak(Box::new(support::FakeAlarm{clock,due:Cell::new(None),client:Cell::new(None)}));
        let mux=Box::leak(Box::new(MuxAlarm::new(physical)));
        physical.set_alarm_client(mux);
        let first=Box::leak(Box::new(VirtualMuxAlarm::new(mux)));
        let second=Box::leak(Box::new(VirtualMuxAlarm::new(mux)));
        if reverse_setup {second.setup();first.setup();} else {first.setup();second.setup();}
        let rearm=Box::leak(Box::new(RearmingAlarm{alarm:first,calls:Cell::new(0)}));
        let counter=Box::leak(Box::new(Counter(Cell::new(0))));
        first.set_alarm_client(rearm);second.set_alarm_client(counter);
        first.set_alarm(first.now(),Ticks32::from(16));second.set_alarm(second.now(),Ticks32::from(30));
        let next=||physical.due.get();let fire=||physical.fire();
        let events=[Event{name:"physical_alarm",next:&next,fire:&fire}];
        drive(clock,start+20,10,&events,||false,|_|{});
        assert_eq!(rearm.calls.get(),1);assert_eq!(counter.0.get(),0);
        assert!(first.is_armed());second.disarm().unwrap();
        drive(clock,start+50,10,&events,||false,|_|{});
        assert_eq!(rearm.calls.get(),2);assert_eq!(counter.0.get(),0);
        assert!(!first.is_armed());assert!(!physical.is_armed());
    }
}

#[test]
fn event_limit_preserves_specific_evidence() {
    let clock = Cell::new(0);
    let error = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        drive(&clock, 10, 3, &[Event {name:"loop", next:&|| Some(0), fire:&|| {}}],
              || false, |_| {});
    })).unwrap_err();
    let message = error.downcast_ref::<String>().unwrap();
    assert!(message.contains("reason=event_budget"));
    assert!(message.contains("events=3"));
    assert!(message.contains("loop"));
}

struct Counter(Cell<usize>);
impl AlarmClient for Counter { fn alarm(&self) { self.0.set(self.0.get()+1); } }
impl DeferredCallClient for Counter {
    fn handle_deferred_call(&self) {self.0.set(self.0.get()+1);}
    fn register(&'static self) {}
}

#[test]
fn real_alarm_contract_handles_wrap_past_deadline_and_cancel() {
    let clock = Box::leak(Box::new(Cell::new(0xfffffff0)));
    let client = Box::leak(Box::new(Counter(Cell::new(0))));
    let alarm = support::FakeAlarm {clock, due:Cell::new(None), client:Cell::new(None)};
    alarm.set_alarm_client(client);
    alarm.set_alarm(Ticks32::from(0xfffffff0), Ticks32::from(32));
    assert_eq!(alarm.due.get(), Some(0x100000010));
    drive(clock, 0x100000010, 10, &[Event {name:"alarm",next:&|| alarm.due.get(),fire:&|| alarm.fire()}], || false, |_| {});
    assert_eq!(client.0.get(),1);
    alarm.set_alarm(Ticks32::from(0xfffffff0), Ticks32::from(4));
    assert_eq!(alarm.due.get(),Some(clock.get()+1));
    alarm.disarm().unwrap(); assert!(!alarm.is_armed());
    drive(clock, clock.get()+10, 10, &[Event {name:"alarm",next:&|| alarm.due.get(),fire:&|| alarm.fire()}], || false, |_| {});
    assert_eq!(client.0.get(),1);
}

#[test]
fn actual_kernel_deferred_call_runs_without_advancing_time() {
    let clock = Cell::new(12);
    let client = Box::leak(Box::new(Counter(Cell::new(0))));
    let dc = DeferredCall::new(); dc.register(client); dc.set();
    assert!(DeferredCall::has_tasks());
    let count = drive(&clock,100,10,&[],|| client.0.get()==1, |_|{});
    assert_eq!(count,1);assert_eq!(clock.get(),12);assert!(!DeferredCall::has_tasks());
}

#[test]
fn admission_waits_for_initialization_and_submits_accepted_request_once() {
    let clock=Cell::new(0);let calls=Cell::new(0);let accepted=Cell::new(0);
    executor::accept_request(&clock,5000,|| {
        calls.set(calls.get()+1);
        if clock.get()<26 {Err(kernel::ErrorCode::OFF)} else {accepted.set(accepted.get()+1);Ok(())}
    },|until| clock.set(until),||0);
    assert_eq!(calls.get(),2);assert_eq!(accepted.get(),1);
    assert_eq!(clock.get(),1000);
}

#[test]
fn rejected_request_callback_is_not_hidden_by_retry() {
    let clock=Cell::new(0);let callbacks=Cell::new(0);
    let error=std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        executor::accept_request(&clock,5000,||Err(kernel::ErrorCode::BUSY),
            |until| {clock.set(until);callbacks.set(1);},||callbacks.get());
    })).unwrap_err();
    assert!(error.downcast_ref::<String>().unwrap().contains("asynchronous completion of rejected request"));
}

#[test]
fn admission_deadline_is_distinct_from_event_limit() {
    let clock=Cell::new(0);
    let error=std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        executor::accept_request(&clock,1000,||Err(kernel::ErrorCode::OFF),
            |until|clock.set(until),||0);
    })).unwrap_err();
    let message=error.downcast_ref::<String>().unwrap();
    assert!(message.contains("reason=request_admission_deadline"));
    assert!(message.contains("attempts=2"));
    assert_eq!(clock.get(),1000);
}

// Exercise the frozen I2C virtualizer using the very same shared bus model.
// This bridge only translates the two native callback traits; it does not
// implement another transport, scheduler or virtualizer.
struct MasterBridge {
    bus: &'static support::FakeI2c<I2cModel>,
    client: Cell<Option<&'static dyn kernel::hil::i2c::I2CHwMasterClient>>,
    addresses: RefCell<Vec<u8>>,
}
impl kernel::hil::i2c::I2CClient for MasterBridge {
    fn command_complete(&self,b:&'static mut[u8],r:Result<(),kernel::hil::i2c::Error>) {
        self.client.get().unwrap().command_complete(b,r);
    }
}
impl kernel::hil::i2c::I2CMaster<'static> for MasterBridge {
    fn set_master_client(&self,c:&'static dyn kernel::hil::i2c::I2CHwMasterClient){self.client.set(Some(c));}
    fn enable(&self){kernel::hil::i2c::I2CDevice::enable(self.bus);}
    fn disable(&self){kernel::hil::i2c::I2CDevice::disable(self.bus);}
    fn write_read(&self,a:u8,b:&'static mut[u8],w:usize,r:usize)->Result<(),(kernel::hil::i2c::Error,&'static mut[u8])>{
        self.addresses.borrow_mut().push(a);kernel::hil::i2c::I2CDevice::write_read(self.bus,b,w,r)
    }
    fn write(&self,a:u8,b:&'static mut[u8],n:usize)->Result<(),(kernel::hil::i2c::Error,&'static mut[u8])>{
        self.addresses.borrow_mut().push(a);kernel::hil::i2c::I2CDevice::write(self.bus,b,n)
    }
    fn read(&self,a:u8,b:&'static mut[u8],n:usize)->Result<(),(kernel::hil::i2c::Error,&'static mut[u8])>{
        self.addresses.borrow_mut().push(a);kernel::hil::i2c::I2CDevice::read(self.bus,b,n)
    }
}
type VirtualI2c=capsules_core::virtualizers::virtual_i2c::I2CDevice<'static,MasterBridge>;
struct RepeatingI2c {
    device:&'static VirtualI2c,
    calls:Cell<usize>,
    pointers:RefCell<Vec<usize>>,
    errors:RefCell<Vec<Option<kernel::hil::i2c::Error>>>,
}
impl kernel::hil::i2c::I2CClient for RepeatingI2c {
    fn command_complete(&self,b:&'static mut[u8],r:Result<(),kernel::hil::i2c::Error>){
        use kernel::hil::i2c::I2CDevice;
        self.calls.set(self.calls.get()+1);self.pointers.borrow_mut().push(b.as_ptr() as usize);
        self.errors.borrow_mut().push(r.err());
        if self.calls.get()==1 {self.device.read(b,1).unwrap();}
    }
}
#[test]
fn frozen_i2c_mux_serializes_devices_and_preserves_buffers_across_reentrant_fault_callback() {
    use capsules_core::virtualizers::virtual_i2c::{MuxI2C,I2CDevice as Device};
    use kernel::hil::i2c::{I2CMaster,I2CDevice,Error};
    for reverse_registration in [false,true] {
        let clock=Box::leak(Box::new(Cell::new(0)));
        let model=Box::leak(Box::new(RefCell::new(I2cModel)));
        let bus=Box::leak(Box::new(support::FakeI2c{clock,model,pending:RefCell::new(None),client:Cell::new(None),
            fault:Cell::new(None),fault_hit:Cell::new(false),enabled:Cell::new(false)}));
        let master=Box::leak(Box::new(MasterBridge{bus,client:Cell::new(None),addresses:RefCell::new(Vec::new())}));
        bus.client.set(Some(master));
        let mux=Box::leak(Box::new(MuxI2C::new(master,None)));
        master.set_master_client(mux);mux.register();
        let first=Box::leak(Box::new(Device::new(mux,0x20)));
        let second=Box::leak(Box::new(Device::new(mux,0x21)));
        let repeating=Box::leak(Box::new(RepeatingI2c{device:first,calls:Cell::new(0),
            pointers:RefCell::new(Vec::new()),errors:RefCell::new(Vec::new())}));
        let other=Box::leak(Box::new(I2cClient{calls:Cell::new(0),pointer:Cell::new(0),error:Cell::new(None)}));
        if reverse_registration {second.set_client(other);first.set_client(repeating);}
        else {first.set_client(repeating);second.set_client(other);}
        first.enable();second.enable();assert!(bus.enabled.get());
        let a=Box::leak(Box::new([0;2]));let ap=a.as_ptr() as usize;
        let b=Box::leak(Box::new([0;2]));let bp=b.as_ptr() as usize;
        bus.fault.set(Some(support::Fault{at:support::FaultAt::Any,asynchronous:true}));
        first.write_read(a,1,1).unwrap();second.write(b,1).unwrap();
        assert_eq!(*master.addresses.borrow(),vec![0x20]);
        assert_eq!(repeating.calls.get()+other.calls.get(),0);
        let rejected=Box::leak(Box::new([0;2]));let rp=rejected.as_ptr() as usize;
        let (error,returned)=second.read(rejected,1).unwrap_err();
        assert_eq!(error,Error::ArbitrationLost);assert_eq!(returned.as_ptr() as usize,rp);
        let count=drive(clock,1000,20,&[Event{name:"mux_bus",next:&||bus.pending.borrow().as_ref().map(|p|p.due),
            fire:&||bus.complete()}],||repeating.calls.get()==2&&other.calls.get()==1,|_|{});
        assert_eq!(count,3);assert_eq!(clock.get(),300);
        assert_eq!(*repeating.pointers.borrow(),vec![ap,ap]);assert_eq!(other.pointer.get(),bp);
        assert_eq!(*repeating.errors.borrow(),vec![Some(Error::AddressNak),None]);assert_eq!(other.error.get(),None);
        let addresses=master.addresses.borrow();
        assert_eq!(addresses.iter().filter(|a|**a==0x20).count(),2);
        assert_eq!(addresses.iter().filter(|a|**a==0x21).count(),1);
        assert!(bus.pending.borrow().is_none());
        first.disable();assert!(bus.enabled.get());second.disable();assert!(!bus.enabled.get());
    }
}
