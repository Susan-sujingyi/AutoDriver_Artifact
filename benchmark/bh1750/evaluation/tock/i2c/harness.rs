//! BH1750FVI single-shot digital model; ROHM No.11046EDT01 Rev.D.
mod executor;
use kernel::hil::sensors::{AmbientLight,AmbientLightClient};
use kernel::hil::time::Alarm;
use kernel::ErrorCode;
use std::cell::{Cell,RefCell};
use std::panic::{catch_unwind,AssertUnwindSafe};
mod bh1750;
mod support;
use support::{DeviceModel,FakeAlarm,Fault,FaultAt};
type Bus=support::FakeI2c<Model>;
type Driver=bh1750::Bh1750<'static,Bus,FakeAlarm>;
const WINDOW:u64=1_000_000;
const PROGRESS:u64=2000000;
#[derive(Clone,Copy,Debug)]struct Vector{id:&'static str,raw:u16,lower:usize,upper:usize}
include!("vectors.rs");
struct Model{mtreg:u8,powered:bool,input:Vector,sample:Vector,valid:bool,due:Option<u64>,
    conversion_us:u64,history:Vec<Vector>,commands:u64}
impl Model{
    fn new(v:&Vector)->Self{Self{mtreg:69,powered:false,input:*v,sample:*v,valid:false,due:None,conversion_us:180_000,history:vec![],commands:0}}
    fn tick(&mut self,now:u64){if self.due.map_or(false,|d|now>=d){self.sample=self.input;self.valid=true;self.due=None;self.powered=false;}}
    fn command(&mut self,op:u8,now:u64){self.tick(now);match op{
        0=>{self.powered=false;self.due=None;},1=>self.powered=true,
        7=>{assert!(self.powered,"PROTOCOL: reset command in power down, RevD p5");self.valid=false;},
        0x40..=0x47|0x60..=0x7f=>{
            if self.due.is_some(){panic!("MODEL_UNSUPPORTED: sensitivity change during conversion");}
            if op&0xe0==0x40{self.mtreg=(self.mtreg&31)|((op&7)<<5);}else{self.mtreg=(self.mtreg&0xe0)|(op&31);}
        },
        0x20=>{assert_eq!(self.mtreg,69,"CONFIG: selected MTreg69");
            if self.due.is_some(){panic!("MODEL_UNSUPPORTED: overlapping conversion trigger");}
            self.powered=true;self.due=Some(now+self.conversion_us);self.commands+=1;},
        0x10|0x11|0x13|0x21|0x23=>panic!("CONFIG: selected one-time high resolution mode"),
        _=>panic!("PROTOCOL: undocumented opcode {:02x}, RevD p5 forbids other opcodes",op),
    }}
}
impl DeviceModel for Model{
    fn exchange(&mut self,now:u64,tx:&[u8],len:usize,b:&mut[u8]){self.tick(now);
        if !tx.is_empty(){assert_eq!(tx.len(),1,"PROTOCOL: one opcode per STOP, RevD p10");
            assert_eq!(len,0,"PROTOCOL: command then STOP before result read");self.command(tx[0],now);
        }else{match len{
            1|2=>{let bytes=if self.valid{self.sample.raw.to_be_bytes()}else{[0;2]};b[..len].copy_from_slice(&bytes[..len]);
                if len==2&&self.valid{self.history.push(self.sample);}},
            0=>(),_=>panic!("MODEL_UNSUPPORTED: clocking past two-byte result"),
        }}
    }
    fn fault_matches(&self,at:FaultAt,tx:&[u8],len:usize)->bool{match at{FaultAt::Any=>true,FaultAt::Data=>len>0,FaultAt::Configuration=>tx==[0x20]}}
}
struct Collector{model:&'static RefCell<Model>,values:RefCell<Vec<usize>>,pending:Cell<usize>}
impl AmbientLightClient for Collector{fn callback(&self,value:usize){
    assert!(self.pending.get()>0,"CALLBACK: unsolicited/duplicate callback");self.pending.set(self.pending.get()-1);
    let m=self.model.borrow();assert!(!m.history.is_empty(),"DATA: no completed two-byte light acquisition");
    assert!(m.history.iter().any(|v|value>=v.lower&&value<=v.upper),"NUMERIC: lux/width/scale differs from source-derived envelope");
    self.values.borrow_mut().push(value);
}}
struct Rig{clock:&'static Cell<u64>,model:&'static RefCell<Model>,bus:&'static Bus,alarm:&'static FakeAlarm,driver:&'static Driver,client:&'static Collector}
impl Rig{
    fn new(v:&Vector)->Self{let clock=Box::leak(Box::new(Cell::new(100_000)));let model=Box::leak(Box::new(RefCell::new(Model::new(v))));
        let bus=Box::leak(Box::new(Bus{clock,model,pending:RefCell::new(None),client:Cell::new(None),fault:Cell::new(None),fault_hit:Cell::new(false),enabled:Cell::new(false)}));
        let alarm=Box::leak(Box::new(FakeAlarm{clock,due:Cell::new(None),client:Cell::new(None)}));
        let driver=Box::leak(Box::new(bh1750::Bh1750::new(bus,alarm,Box::leak(vec![0;2].into_boxed_slice()))));
        let client=Box::leak(Box::new(Collector{model,values:RefCell::new(vec![]),pending:Cell::new(0)}));
        bus.client.set(Some(driver));alarm.set_alarm_client(driver);AmbientLight::set_client(driver,client);Self{clock,model,bus,alarm,driver,client}}
    fn advance(&self,end:u64,complete:bool){
        executor::drive(self.clock, end, executor::EVENT_LIMIT, &[
            executor::Event { name: "bus", next: &|| self.bus.pending.borrow().as_ref().map(|p| p.due), fire: &|| self.bus.complete() },
            executor::Event { name: "alarm", next: &|| self.alarm.due.get(), fire: &|| self.alarm.fire() },
        ], || complete&&self.client.pending.get()==0, |now| self.model.borrow_mut().tick(now));
    }
    fn init(&self){let _=self.driver.startup();self.advance(self.clock.get()+WINDOW,false);}
    fn request(&self)->Result<(),ErrorCode>{let before=self.client.values.borrow().len();self.client.pending.set(self.client.pending.get()+1);let result=self.driver.read_light_intensity();
        if result.is_err(){assert_eq!(self.client.values.borrow().len(),before,"CALLBACK: rejected request delivered value");self.client.pending.set(self.client.pending.get()-1);}result}
    fn complete(&self){self.complete_until(self.clock.get()+WINDOW);}
    fn complete_until(&self,end:u64){self.advance(end,true);if self.client.pending.get()!=0{panic!("NOT_COMPLETED: accepted request lacks callback");}self.advance((self.clock.get()+5_000).min(end),false);}
    fn sample(&self){self.sample_until(self.clock.get()+WINDOW);}
    fn sample_until(&self,end:u64){executor::accept_request(self.clock, end, || self.request(), |until| self.advance(until, false), || self.client.values.borrow().len());self.complete_until(end.min(self.clock.get()+WINDOW));}
}
fn run(id:&str,scope:&str,f:impl FnOnce()){
    if std::env::args().nth(1).as_deref()!=Some(id){return;}
    let(status,message)=match catch_unwind(AssertUnwindSafe(f)){Ok(())=>("pass",String::new()),Err(e)=>{
        let s=e.downcast_ref::<String>().cloned().or_else(||e.downcast_ref::<&str>().map(|s|s.to_string())).unwrap_or_else(||"candidate panic".into());
        let status=if s.starts_with("NOT_COMPLETED:"){"not_completed"}else if s.starts_with("MODEL_UNSUPPORTED:"){"model_unsupported"}else if s.starts_with("NOT_EXERCISED:"){"not_exercised"}else{"fail"};(status,s)}};
    println!("CASE\t{}\t{}\t{}\t{}",id,scope,status,message.replace('\t'," ").replace('\n'," "));
}
fn main(){std::panic::set_hook(Box::new(|_|{}));
    run("__model/one_time_retention","internal",||{let mut m=Model::new(&VECTORS[1]);m.command(0x20,0);m.tick(180_000);
        assert!(m.valid&&!m.powered);m.input=VECTORS[0];m.tick(1_000_000);assert_eq!(m.sample.raw,VECTORS[1].raw);m.command(0x20,1_000_000);m.tick(1_180_000);assert_eq!(m.sample.raw,VECTORS[0].raw);});
    run("__model/reset_and_mtreg","internal",||{let mut m=Model::new(&VECTORS[1]);m.command(0x01,0);m.command(0x07,1);assert!(!m.valid);
        m.command(0x65,2);m.command(0x42,3);assert_eq!(m.mtreg,69);});
    for v in VECTORS{run(&format!("numeric/{}/ambient_light",v.id),"core",||{let r=Rig::new(v);r.init();r.sample();});}
    run("requests/first_available","core",||{let r=Rig::new(&VECTORS[2]);let _=r.driver.startup();let end=r.clock.get()+WINDOW;let mut accepted=false;
        while r.clock.get()<end{if r.request().is_ok(){accepted=true;break;}r.advance(r.clock.get()+100,false);}if !accepted{panic!("NOT_COMPLETED: startup never accepts request");}r.complete();});
    run("requests/concurrent","core",||{let r=Rig::new(&VECTORS[1]);r.init();executor::accept_request(r.clock, r.clock.get()+WINDOW, || r.request(), |until| r.advance(until, false), || r.client.values.borrow().len());let _=r.request();r.complete();});
    run("requests/input_changes","core",||{let r=Rig::new(&VECTORS[0]);r.init();for v in VECTORS{r.model.borrow_mut().input=*v;let end=r.clock.get()+PROGRESS;let mut seen=false;
        while r.clock.get()<end{r.sample_until(end);let x=*r.client.values.borrow().last().unwrap();if x>=v.lower&&x<=v.upper{seen=true;break;}r.advance((r.clock.get()+10_000).min(end),false);}
        if !seen{panic!("NOT_COMPLETED: stable light change not reflected within observation");}}});
    for delay in [60_000u64,120_000,180_000]{run(&format!("timing/conversion_{}us",delay),"core",||{let r=Rig::new(&VECTORS[1]);r.model.borrow_mut().conversion_us=delay;r.init();r.sample();});}
    for at in [FaultAt::Any,FaultAt::Configuration,FaultAt::Data]{for asynchronous in [false,true]{run(&format!("diagnostic/bus/{:?}/{}",at,asynchronous),"diagnostic",||{
        let r=Rig::new(&VECTORS[1]);r.init();r.bus.fault.set(Some(Fault{at,asynchronous}));let result=r.request();r.advance(r.clock.get()+WINDOW,false);
        if !r.bus.fault_hit.get(){panic!("NOT_EXERCISED: injection site absent");}println!("OBS\tfault\trequest={:?} pending={} values={:?}",result,r.client.pending.get(),r.client.values.borrow());});}}
    run("diagnostic/repeated_startup","diagnostic",||{let r=Rig::new(&VECTORS[1]);r.init();let _=r.driver.startup();r.advance(r.clock.get()+WINDOW,false);r.sample();});
}
