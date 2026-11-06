//! LPS22HB bounded native-HIL model, ST DocID027083 Rev6.
mod executor;
use kernel::hil::{i2c,sensors};
use kernel::hil::time::Alarm;
use kernel::ErrorCode;
use std::cell::{Cell,RefCell};
use std::panic::{catch_unwind,AssertUnwindSafe};
mod support;
mod lps22hb;
use support::{DeviceModel,FakeAlarm,Fault,FaultAt};
type Bus=support::FakeI2c<Model>;
type Driver=lps22hb::Lps22hb<'static,Bus,FakeAlarm>;
const PERIOD:u64=1_000_000;
const WINDOW:u64=4_000_000;
const PROGRESS:u64=8000000; // observation budget, not a specified HIL deadline
#[derive(Clone,Copy,Debug)]struct Vector{id:&'static str,bytes:[u8;5],lower:[i64;2],upper:[i64;2]}
include!("vectors.rs");
#[derive(Clone,Copy,Debug,PartialEq)]enum Channel{Temperature,Pressure}
impl Channel{fn i(self)->usize{if self==Self::Temperature{0}else{1}}
    fn name(self)->&'static str{if self==Self::Temperature{"temperature"}else{"pressure"}}
    fn mask(self)->u8{if self==Self::Temperature{24}else{7}}}
const CHANNELS:[Channel;2]=[Channel::Temperature,Channel::Pressure];
struct Model{regs:[u8;256],pointer:u8,input:Vector,sample:Vector,next:Option<u64>,valid:bool,
    holding:bool,ready:u8,overrun:u8,generation:u64,acquisitions:Vec<(u64,Vector,u8)>,
    reset_until:u64,stuck:bool,clear_on_first:bool,identity_reads:usize}
impl Model{
    fn new(v:&Vector)->Self{let mut regs=[0;256];regs[15]=0xb1;regs[17]=0x10;
        Self{regs,pointer:0,input:*v,sample:*v,next:None,valid:false,holding:false,ready:0,overrun:0,
            generation:0,acquisitions:vec![],reset_until:0,stuck:false,clear_on_first:false,identity_reads:0}}
    fn tick(&mut self,now:u64){if now>=self.reset_until{self.regs[17]&=!4;}
        if let Some(due)=self.next{if now>=due{let n=(now-due)/PERIOD+1;self.next=Some(due+n*PERIOD);
            if !self.stuck&&(!self.holding||self.regs[16]&2==0){self.overrun|=self.ready<<4;
                self.sample=self.input;self.generation+=n;self.valid=true;self.ready=3;self.holding=self.regs[16]&2!=0;}
        }}}
    fn fields(&self){assert_eq!(self.regs[16]&0xfa,0x12,"CONFIG: 1Hz, BDU, filter disabled, reserved bit7 clear");
        assert_eq!(self.regs[17]&0x59,0,"CONFIG: I2C enabled, IF_ADD_INC0, FIFO off, no one-shot");
        assert_eq!(self.regs[11]&0xe0,0,"CONFIG: absolute pressure without automatic reference");
        assert_eq!(self.regs[24] as u16|((self.regs[25] as u16)<<8),0,"CONFIG: no custom pressure offset");}
    fn read(&mut self,reg:u8,now:u64)->u8{self.tick(now);match reg{
        15=>{self.identity_reads+=1;self.regs[15]},
        39=>self.ready|self.overrun,
        40..=44=>{if now<self.reset_until{panic!("MODEL_UNSUPPORTED: reading during modeled reset interval");}
            let i=(reg-40) as usize;if self.valid{self.fields();
                if let Some(last)=self.acquisitions.last_mut().filter(|a|a.0==self.generation){last.2|=1<<i;}
                else{self.acquisitions.push((self.generation,self.sample,1<<i));}}
            // The exact DA-clear byte is unspecified in Rev6. This nominal
            // high-byte model is audited separately with a first-byte variant.
            if reg==44||(self.clear_on_first&&reg==43){self.ready&=!2;self.overrun&=!32;}
            if reg==42||(self.clear_on_first&&reg==40){self.ready&=!1;self.overrun&=!16;}
            if reg==42{self.holding=false;} // p38: PRESS_OUT_H releases BDU
            if self.valid{self.sample.bytes[i]}else{0}},
        11|12|13|16..=20|24|25|26=>self.regs[reg as usize],
        _=>panic!("MODEL_UNSUPPORTED: read register {:02x}",reg),
    }}
    fn write(&mut self,reg:u8,value:u8,now:u64){self.tick(now);
        if now<self.reset_until{panic!("MODEL_UNSUPPORTED: configuration during modeled reset interval");}
        match reg{
            15|39..=44=>panic!("PROTOCOL: write read-only register"),
            17 if value&0x80!=0=>panic!("MODEL_UNSUPPORTED: memory reboot"),
            17 if value&4!=0=>{self.regs=[0;256];self.regs[15]=0xb1;self.regs[17]=0x14;
                self.next=None;self.valid=false;self.holding=false;self.ready=0;self.overrun=0;self.reset_until=now+1000;},
            16=>{let old=self.regs[16];self.regs[16]=value;if (old^value)&0x70!=0{
                self.next=if value&0x70!=0{Some(now+PERIOD)}else{None};self.holding=false;}},
            11|12|13|17..=20|24|25|26=>self.regs[reg as usize]=value,
            _=>panic!("MODEL_UNSUPPORTED: write register {:02x}",reg),
        }
    }
}
impl DeviceModel for Model{
    fn exchange(&mut self,now:u64,tx:&[u8],len:usize,b:&mut[u8]){self.tick(now);
        if let Some(reg)=tx.first(){self.pointer=*reg&0x7f;for value in &tx[1..]{self.write(self.pointer,*value,now);
            if self.regs[17]&16!=0{self.pointer=self.pointer.wrapping_add(1);}}}
        if len>0&&(40..=44).contains(&self.pointer)&&self.regs[16]&2!=0{
            assert!(len==1&&self.regs[17]&16==0,"PROTOCOL: Rev6 p37 BDU I2C footnote requires single-byte reads and IF_ADD_INC0");}
        for byte in &mut b[..len]{*byte=self.read(self.pointer,now);if self.regs[17]&16!=0{self.pointer=self.pointer.wrapping_add(1);}}
    }
    fn fault_matches(&self,at:FaultAt,tx:&[u8],len:usize)->bool{let p=tx.first().copied().unwrap_or(self.pointer)&127;
        match at{FaultAt::Any=>true,FaultAt::Data=>len>0&&(40..=44).contains(&p),FaultAt::Configuration=>tx.len()>1&&(p==16||p==17)}}
}
struct Collector{model:&'static RefCell<Model>,pending:Cell<[usize;2]>,values:RefCell<Vec<(Channel,Result<i64,ErrorCode>)>>}
impl Collector{fn add(&self,c:Channel,v:Result<i64,ErrorCode>){let mut pending=self.pending.get();
    assert!(pending[c.i()]>0,"CALLBACK: unsolicited, duplicate or wrong-channel callback");pending[c.i()]-=1;self.pending.set(pending);
    if let Ok(value)=v{let m=self.model.borrow();let acquired=m.acquisitions.iter().filter(|a|a.2&c.mask()==c.mask()).collect::<Vec<_>>();
        assert!(!acquired.is_empty(),"DATA: no complete channel acquisition from valid measurement");
        assert!(acquired.iter().any(|a|value>=a.1.lower[c.i()]&&value<=a.1.upper[c.i()]),"NUMERIC: output does not match coherent acquired channel");}
    self.values.borrow_mut().push((c,v));
}}
impl sensors::TemperatureClient for Collector{fn callback(&self,v:Result<i32,ErrorCode>){self.add(Channel::Temperature,v.map(i64::from));}}
impl sensors::PressureClient for Collector{fn callback(&self,v:Result<u32,ErrorCode>){self.add(Channel::Pressure,v.map(i64::from));}}
struct Rig{clock:&'static Cell<u64>,model:&'static RefCell<Model>,bus:&'static Bus,alarm:&'static FakeAlarm,driver:&'static Driver,client:&'static Collector}
impl Rig{
    fn new(v:&Vector)->Self{let clock=Box::leak(Box::new(Cell::new(100_000)));let model=Box::leak(Box::new(RefCell::new(Model::new(v))));
        let bus=Box::leak(Box::new(Bus{clock,model,pending:RefCell::new(None),client:Cell::new(None),fault:Cell::new(None),fault_hit:Cell::new(false),enabled:Cell::new(false)}));
        let alarm=Box::leak(Box::new(FakeAlarm{clock,due:Cell::new(None),client:Cell::new(None)}));
        let driver=Box::leak(Box::new(lps22hb::Lps22hb::new(bus,alarm,Box::leak(vec![0;8].into_boxed_slice()))));
        let client=Box::leak(Box::new(Collector{model,pending:Cell::new([0;2]),values:RefCell::new(vec![])}));
        bus.client.set(Some(driver));alarm.set_alarm_client(driver);sensors::TemperatureDriver::set_client(driver,client);sensors::PressureDriver::set_client(driver,client);
        Self{clock,model,bus,alarm,driver,client}}
    fn advance(&self,end:u64,complete:bool){
        executor::drive(self.clock, end, executor::EVENT_LIMIT, &[
            executor::Event { name: "bus", next: &|| self.bus.pending.borrow().as_ref().map(|p| p.due), fire: &|| self.bus.complete() },
            executor::Event { name: "alarm", next: &|| self.alarm.due.get(), fire: &|| self.alarm.fire() },
        ], || complete&&self.client.pending.get()==[0;2], |now| self.model.borrow_mut().tick(now));
    }
    fn init(&self){let _=self.driver.startup();self.advance(self.clock.get()+100_000,false);}
    fn request(&self,c:Channel)->Result<(),ErrorCode>{let before=self.client.values.borrow().len();let mut p=self.client.pending.get();p[c.i()]+=1;self.client.pending.set(p);
        let result=match c{Channel::Temperature=>sensors::TemperatureDriver::read_temperature(self.driver),Channel::Pressure=>sensors::PressureDriver::read_atmospheric_pressure(self.driver)};
        if result.is_err(){assert_eq!(self.client.values.borrow().len(),before,"CALLBACK: callback for synchronously rejected request");let mut p=self.client.pending.get();p[c.i()]-=1;self.client.pending.set(p);}result}
    fn complete(&self,start:usize){self.complete_until(start,self.clock.get()+WINDOW);}
    fn complete_until(&self,start:usize,end:u64){self.advance(end,true);
        if self.client.pending.get()!=[0;2]{panic!("NOT_COMPLETED: accepted request lacks callback");}
        if self.client.values.borrow()[start..].iter().any(|x|x.1.is_err()){panic!("DATA: healthy measurement completed with error time_us={} values={:?}",self.clock.get(),&self.client.values.borrow()[start..]);}
        self.advance((self.clock.get()+10_000).min(end),false);}
    fn sample(&self,c:Channel){self.sample_until(c,self.clock.get()+WINDOW);}
    fn sample_until(&self,c:Channel,end:u64){let start=self.client.values.borrow().len();executor::accept_request(self.clock, end, || self.request(c), |until| self.advance(until, false), || self.client.values.borrow().len());self.complete_until(start,end.min(self.clock.get()+WINDOW));}
}
fn run(id:&str,scope:&str,f:impl FnOnce()){
    if std::env::args().nth(1).as_deref()!=Some(id){return;}
    let(status,message)=match catch_unwind(AssertUnwindSafe(f)){Ok(())=>("pass",String::new()),Err(e)=>{
        let s=e.downcast_ref::<String>().cloned().or_else(||e.downcast_ref::<&str>().map(|s|s.to_string())).unwrap_or_else(||"candidate panic".into());
        let status=if s.starts_with("NOT_COMPLETED:"){"not_completed"}else if s.starts_with("MODEL_UNSUPPORTED:"){"model_unsupported"}else if s.starts_with("NOT_EXERCISED:"){"not_exercised"}else{"fail"};(status,s)}};
    println!("CASE\t{}\t{}\t{}\t{}",id,scope,status,message.replace('\t'," ").replace('\n'," "));
}
fn main(){std::panic::set_hook(Box::new(|_|{}));
    run("__model/bdu_release","internal",||{let mut m=Model::new(&VECTORS[0]);m.write(17,0,0);m.write(16,0x12,0);m.tick(PERIOD);
        m.input=VECTORS[1];m.tick(2*PERIOD);assert_eq!(m.sample.bytes,VECTORS[0].bytes);m.read(42,2*PERIOD);m.tick(3*PERIOD);assert_eq!(m.sample.bytes,VECTORS[1].bytes);});
    run("__model/data_retention","internal",||{let mut m=Model::new(&VECTORS[0]);m.write(17,0,0);m.write(16,0x12,0);m.tick(PERIOD);
        let x=m.read(42,PERIOD);assert_eq!(m.read(42,PERIOD+1),x);assert!(m.valid);assert_eq!(m.ready&1,0);});
    for v in VECTORS{for c in CHANNELS{run(&format!("numeric/{}/{}",v.id,c.name()),"core",||{let r=Rig::new(v);r.init();r.sample(c);});}}
    for c in CHANNELS{
        run(&format!("requests/repeated_{}",c.name()),"core",||{let r=Rig::new(&VECTORS[0]);r.init();r.sample(CHANNELS[1-c.i()]);
            for v in VECTORS.iter().take(6){r.model.borrow_mut().input=*v;let end=r.clock.get()+PROGRESS;let mut seen=false;
                while r.clock.get()<end{r.sample_until(c,end);if let Some((_,Ok(x)))=r.client.values.borrow().iter().rev().find(|x|x.0==c){if *x>=v.lower[c.i()]&&*x<=v.upper[c.i()]{seen=true;break;}}r.advance((r.clock.get()+10_000).min(end),false);}
                if !seen{panic!("NOT_COMPLETED: repeated same-channel requests retain old value after stable input change");}}});
        run(&format!("requests/first_available/{}",c.name()),"core",||{let r=Rig::new(&VECTORS[1]);let _=r.driver.startup();let end=r.clock.get()+WINDOW;let mut accepted=false;
            while r.clock.get()<end{if r.request(c).is_ok(){accepted=true;break;}r.advance(r.clock.get()+100,false);}if !accepted{panic!("NOT_COMPLETED: startup never accepts request");}r.complete(0);});
        run(&format!("requests/concurrent/{}",c.name()),"core",||{let r=Rig::new(&VECTORS[0]);r.init();executor::accept_request(r.clock, r.clock.get()+WINDOW, || r.request(c), |until| r.advance(until, false), || r.client.values.borrow().len());
            for other in CHANNELS{let _=r.request(other);}r.complete(0);});
        run(&format!("protocol/coherent_boundary/{}",c.name()),"core",||{let r=Rig::new(&VECTORS[0]);r.init();
            let due=r.model.borrow().next.unwrap_or(r.clock.get()+PERIOD)+PERIOD;r.advance(due-550,false);r.model.borrow_mut().input=VECTORS[1];r.sample(c);});
        for at in [FaultAt::Any,FaultAt::Data]{for asynchronous in [false,true]{run(&format!("diagnostic/bus/{}/{:?}/{}",c.name(),at,asynchronous),"diagnostic",||{
            let r=Rig::new(&VECTORS[0]);r.init();r.bus.fault.set(Some(Fault{at,asynchronous}));let result=r.request(c);r.advance(r.clock.get()+WINDOW,false);
            if !r.bus.fault_hit.get(){panic!("NOT_EXERCISED: injection site absent");}println!("OBS\tfault\trequest={:?} pending={:?} values={:?}",result,r.client.pending.get(),r.client.values.borrow());});}}
        run(&format!("diagnostic/stalled_measurement/{}",c.name()),"diagnostic",||{let r=Rig::new(&VECTORS[0]);r.init();r.model.borrow_mut().stuck=true;
            let result=r.request(c);r.advance(r.clock.get()+WINDOW,false);println!("OBS\tstalled\trequest={:?} pending={:?} values={:?}",result,r.client.pending.get(),r.client.values.borrow());});
        run(&format!("diagnostic/alternate_DA_clear/{}",c.name()),"diagnostic",||{let r=Rig::new(&VECTORS[0]);r.model.borrow_mut().clear_on_first=true;r.init();r.sample(c);});
    }
    run("requests/input_changes","core",||{let r=Rig::new(&VECTORS[0]);r.init();for v in VECTORS.iter().take(6){r.model.borrow_mut().input=*v;
        let end=r.clock.get()+PROGRESS;let mut seen=[false;2];while r.clock.get()<end&&seen!=[true;2]{for c in CHANNELS{if r.clock.get()>=end{break;}r.sample_until(c,end);
            if let Some((_,Ok(x)))=r.client.values.borrow().iter().rev().find(|x|x.0==c){if *x>=v.lower[c.i()]&&*x<=v.upper[c.i()]{seen[c.i()]=true;}}}
            r.advance((r.clock.get()+10_000).min(end),false);}if seen!=[true;2]{panic!("NOT_COMPLETED: stable physical change not reflected within observation");}}});
    run("diagnostic/wrong_identity","diagnostic",||{let r=Rig::new(&VECTORS[0]);r.model.borrow_mut().regs[15]=0xb3;r.init();let result=r.request(Channel::Temperature);
        r.advance(r.clock.get()+WINDOW,false);println!("OBS\tidentity\trequest={:?} probes={}",result,r.model.borrow().identity_reads);});
    for at in [FaultAt::Any,FaultAt::Configuration]{for asynchronous in [false,true]{run(&format!("diagnostic/startup/{:?}/{}",at,asynchronous),"diagnostic",||{
        let r=Rig::new(&VECTORS[0]);r.bus.fault.set(Some(Fault{at,asynchronous}));r.init();
        if !r.bus.fault_hit.get(){panic!("NOT_EXERCISED: startup injection site absent");}println!("OBS\tstartup\tpending={:?}",r.client.pending.get());});}}
}
