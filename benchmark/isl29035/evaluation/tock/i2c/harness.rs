//! ISL29035 continuous ALS model; no per-call generation or exact BUSY policy.
mod executor;
use kernel::hil::sensors::{AmbientLight,AmbientLightClient};
use kernel::hil::time::Alarm;
use kernel::ErrorCode;
use std::cell::{Cell,RefCell};
use std::panic::{catch_unwind,AssertUnwindSafe};
mod isl29035;
mod support;
use support::{DeviceModel,FakeAlarm,Fault,FaultAt};
type Bus=support::FakeI2c<Model>;
type Driver=isl29035::Isl29035<'static,Bus,FakeAlarm>;
const PERIOD:u64=410; // Table11 selected 8-bit nominal integration
const WINDOW:u64=100_000;
const PROGRESS:u64=400000; // supervisor limit, not API/hardware deadline
#[derive(Clone,Copy,Debug)]struct Vector{id:&'static str,raw:u8,lower:usize,upper:usize}
include!("vectors.rs");
struct Model{regs:[u8;16],pointer:u8,input:Vector,sample:Vector,next:Option<u64>,valid:bool,
    history:Vec<Vector>,upper:u8,outside:u64,identity_reads:usize}
impl Model{
    fn new(v:&Vector)->Self{let mut regs=[0;16];regs[15]=0xa8;regs[6]=255;regs[7]=255;
        Self{regs,pointer:0,input:*v,sample:*v,next:None,valid:false,history:vec![],upper:0,outside:0,identity_reads:0}}
    fn tick(&mut self,now:u64){if let Some(due)=self.next{if now>=due{
        let n=(now-due)/PERIOD+1;self.sample=self.input;self.valid=true;self.next=Some(due+n*PERIOD);
        let low=self.regs[4] as u16+((self.regs[5] as u16)<<8);let high=self.regs[6] as u16+((self.regs[7] as u16)<<8);
        if self.sample.raw as u16<=low||self.sample.raw as u16>=high{self.outside+=n;}else{self.outside=0;}
        let persistence=[1,4,8,16][(self.regs[0]&3) as usize];if self.outside>=persistence{self.regs[0]|=4;}
    }}}
    fn fields(&self){assert_eq!(self.regs[0]&0xe0,0xa0,"CONFIG: continuous ALS");assert_eq!(self.regs[1]&15,9,"CONFIG: 4000 lux range, 8-bit ADC");}
    fn read(&mut self,reg:u8,now:u64)->u8{self.tick(now);match reg{
        0=>{let value=self.regs[0];self.regs[0]&=!4;value},
        2=>{if self.valid{self.fields();self.history.push(self.sample);self.sample.raw}else{0}},
        3=>self.upper,
        15=>{self.identity_reads+=1;self.regs[15]},
        1|4..=7=>self.regs[reg as usize],
        _=>panic!("MODEL_UNSUPPORTED: register read {:02x}",reg),
    }}
    fn write(&mut self,reg:u8,value:u8,now:u64){self.tick(now);match reg{
        15=>{self.regs[15]=(self.regs[15]&!0x80)|(value&0x80);},
        2|3=>panic!("PROTOCOL: write read-only ADC data"),
        0|1=>{
            let old=self.regs[reg as usize];self.regs[reg as usize]=value;
            if (reg==0&&(value^old)&0xe0!=0)||(reg==1&&(value^old)&15!=0){
                self.next=if self.regs[0]&0xe0==0xa0{Some(now+PERIOD)}else{None};
                // Retain prior completed data. Register refresh follows the next
                // complete integration; no fictitious read-to-clear data flag.
            }
        },
        4..=7=>self.regs[reg as usize]=value,
        _=>panic!("MODEL_UNSUPPORTED: register write {:02x}",reg),
    }}
}
impl DeviceModel for Model{
    fn exchange(&mut self,now:u64,tx:&[u8],len:usize,b:&mut[u8]){
        self.tick(now);if let Some(reg)=tx.first(){if *reg>=16{panic!("MODEL_UNSUPPORTED: register address {:02x}",reg);}self.pointer=*reg;
            for value in &tx[1..]{self.write(self.pointer,*value,now);self.pointer=(self.pointer+1)&15;}}
        for byte in &mut b[..len]{*byte=self.read(self.pointer,now);self.pointer=(self.pointer+1)&15;}
    }
    fn fault_matches(&self,at:FaultAt,tx:&[u8],len:usize)->bool{let p=tx.first().copied().unwrap_or(self.pointer) as usize;
        match at{FaultAt::Any=>true,FaultAt::Data=>len>0&&p<=3&&p+len>2,FaultAt::Configuration=>tx.len()>1&&matches!(p,0|1|15)}}
}
struct Collector{model:&'static RefCell<Model>,values:RefCell<Vec<usize>>,pending:Cell<usize>}
impl AmbientLightClient for Collector{fn callback(&self,value:usize){
    assert!(self.pending.get()>0,"CALLBACK: unsolicited/duplicate callback");self.pending.set(self.pending.get()-1);
    let m=self.model.borrow();assert!(!m.history.is_empty(),"DATA: no completed ADC acquisition");
    assert!(m.history.iter().any(|v|value>=v.lower&&value<=v.upper),"NUMERIC: lux/width/scale differs from source-derived envelope");
    self.values.borrow_mut().push(value);
}}
struct Rig{clock:&'static Cell<u64>,model:&'static RefCell<Model>,bus:&'static Bus,alarm:&'static FakeAlarm,driver:&'static Driver,client:&'static Collector}
impl Rig{
    fn new(v:&Vector)->Self{let clock=Box::leak(Box::new(Cell::new(100_000)));let model=Box::leak(Box::new(RefCell::new(Model::new(v))));
        let bus=Box::leak(Box::new(Bus{clock,model,pending:RefCell::new(None),client:Cell::new(None),fault:Cell::new(None),fault_hit:Cell::new(false),enabled:Cell::new(false)}));
        let alarm=Box::leak(Box::new(FakeAlarm{clock,due:Cell::new(None),client:Cell::new(None)}));
        let driver=Box::leak(Box::new(isl29035::Isl29035::new(bus,alarm,Box::leak(vec![0;3].into_boxed_slice()))));
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
    run("__model/data_retention","internal",||{let mut m=Model::new(&VECTORS[1]);m.write(1,9,0);m.write(0,0xa0,0);m.tick(PERIOD);assert!(m.valid);let raw=m.read(2,PERIOD);assert_eq!(m.read(2,PERIOD+1),raw);assert_eq!(m.history.len(),2);});
    run("__model/threshold_clear","internal",||{let mut m=Model::new(&VECTORS[0]);m.write(1,9,0);m.write(0,0xa0,0);m.tick(PERIOD);assert_ne!(m.read(0,PERIOD)&4,0);assert_eq!(m.regs[0]&4,0);});
    for v in VECTORS{run(&format!("numeric/{}/ambient_light",v.id),"core",||{let r=Rig::new(v);r.init();r.sample();});}
    run("requests/first_available","core",||{let r=Rig::new(&VECTORS[2]);let _=r.driver.startup();let end=r.clock.get()+WINDOW;let mut accepted=false;
        while r.clock.get()<end{if r.request().is_ok(){accepted=true;break;}r.advance(r.clock.get()+100,false);}if !accepted{panic!("NOT_COMPLETED: startup never accepts request");}r.complete();});
    run("requests/concurrent","core",||{let r=Rig::new(&VECTORS[1]);r.init();executor::accept_request(r.clock, r.clock.get()+WINDOW, || r.request(), |until| r.advance(until, false), || r.client.values.borrow().len());let _=r.request();r.complete();});
    run("requests/input_changes","core",||{let r=Rig::new(&VECTORS[0]);r.init();for v in VECTORS{r.model.borrow_mut().input=*v;let end=r.clock.get()+PROGRESS;let mut seen=false;
        while r.clock.get()<end{r.advance((r.clock.get()+1000).min(end),false);if r.clock.get()>=end{break;}r.sample_until(end);let x=*r.client.values.borrow().last().unwrap();if x>=v.lower&&x<=v.upper{seen=true;break;}}
        if !seen{panic!("NOT_COMPLETED: stable light change not reflected within observation");}}});
    for at in [FaultAt::Any,FaultAt::Data]{for asynchronous in [false,true]{run(&format!("diagnostic/bus/{:?}/{}",at,asynchronous),"diagnostic",||{
        let r=Rig::new(&VECTORS[1]);r.init();r.bus.fault.set(Some(Fault{at,asynchronous}));let _=r.request();r.advance(r.clock.get()+WINDOW,false);
        if !r.bus.fault_hit.get(){panic!("NOT_EXERCISED: injection site absent");}println!("OBS\tfault\tpending={} values={:?}",r.client.pending.get(),r.client.values.borrow());
    });}}
    for id in [0x08u8,0x20,0x28,0x2f,0x68,0xef]{run(&format!("diagnostic/identity_{:02x}",id),"diagnostic",||{let r=Rig::new(&VECTORS[1]);r.model.borrow_mut().regs[15]=id;r.init();let result=r.request();r.advance(r.clock.get()+WINDOW,false);println!("OBS\tidentity\trequest={:?} probes={}",result,r.model.borrow().identity_reads);});}
    run("diagnostic/unused_upper_bits","diagnostic",||{let r=Rig::new(&VECTORS[1]);r.model.borrow_mut().upper=0xa5;r.init();r.sample();});
    run("diagnostic/brownout_ack","diagnostic",||{let r=Rig::new(&VECTORS[1]);r.init();println!("OBS\tbrownout\tbit={}",r.model.borrow().regs[15]&0x80);});
}
