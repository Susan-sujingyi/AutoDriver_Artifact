//! Independent BMP388 register model, fixed DS001-07 revision1.7.
mod executor;
use kernel::hil::sensors::{TemperatureClient,TemperatureDriver,PressureClient,PressureDriver};
use kernel::hil::{time::Alarm,i2c};
use std::panic::{catch_unwind,AssertUnwindSafe};
use kernel::ErrorCode;
use std::cell::{Cell,RefCell};
mod bmp388;
mod support;
use support::{DeviceModel,FakeAlarm,Fault,FaultAt};
type FakeI2c=support::FakeI2c<Model>;
type Driver=bmp388::Bmp388<'static,FakeI2c,FakeAlarm>;
const FIRST:u64=12_480;
const PERIOD:u64=40_000;
const STARTUP:u64=100_000;
const WINDOW:u64=500_000;
const PROGRESS:u64=2_000_000;
#[derive(Clone,Copy,Debug)]
struct Vector {id:&'static str,cal:[u8;21],raw:[u8;6],expected:[i64;2],lower:[i64;2],upper:[i64;2]}
include!("vectors.rs");
#[derive(Clone,Copy)]
struct Acquisition {generation:u64,sample:Vector,mask:u8,eligible:[bool;2]}
struct Model {
    regs:[u8;128],pointer:u8,ready_at:u64,command_ready:bool,initial_error:u8,
    next:Option<u64>,ready:[bool;2],stuck:[bool;2],error:u8,
    input:Vector,sample:Vector,generation:u64,history:Vec<Acquisition>,
    identity_reads:usize,cal_mask:u32,error_reads:usize,fault_site:&'static str,
}
impl Model {
    fn new(v:&Vector)->Self {
        let mut regs=[0;128];regs[0]=0x50;regs[0x19]=2;
        regs[0x31..0x46].copy_from_slice(&v.cal);
        // BMP-TIME starts after power stabilization; only explicit soft reset
        // enters the separate modeled settling interval.
        Self {regs,pointer:0,ready_at:0,command_ready:true,initial_error:0,
            next:None,ready:[false;2],stuck:[false;2],error:0,input:*v,sample:*v,
            generation:0,history:Vec::new(),identity_reads:0,cal_mask:0,error_reads:0,fault_site:"Any"}
    }
    fn tick(&mut self,now:u64) {
        if let Some(due)=self.next {if now>=due {
            let n=(now-due)/PERIOD+1;self.generation+=n;self.sample=self.input;
            self.ready=[!self.stuck[0],!self.stuck[1]];self.next=if self.regs[0x1b]&0x30==0x30{Some(due+n*PERIOD)}else{self.regs[0x1b]&=!0x30;None};
        }}
    }
    fn status(&self)->u8 {
        (if self.command_ready {0x10}else{0})|(if self.ready[1]{0x20}else{0})|(if self.ready[0]{0x40}else{0})
    }
    fn configuration(&self) {
        // A known, fixed device need not issue an identity probe.
        // Needed coefficients may be acquired lazily or in separate transactions.
        assert_eq!(self.regs[0x1b],0x33,"CONFIG: selected normal mode and both channels required");
        assert_eq!(self.regs[0x1c]&0x3f,2,"CONFIG: selected pressure4x temperature1x");
        assert_eq!(self.regs[0x1d]&0x1f,3,"CONFIG: selected 25Hz ODR");
        assert_eq!(self.regs[0x1f]&0x0e,0,"CONFIG: selected IIR off");
        assert_eq!(self.regs[0x17]&1,0,"CONFIG: selected FIFO disabled");
        assert_eq!(self.regs[0x19]&0x58,0,"CONFIG: selected interrupt acquisition disabled");
    }
    fn write(&mut self,reg:u8,value:u8,now:u64) {
        if reg==0x7e {
            if value==0xb0{return;}if value!=0xb6{panic!("MODEL_UNSUPPORTED: CMD {:02x}",value);}if !self.command_ready{self.error|=2;return;}
            self.ready_at=now+2_000;self.next=None;self.ready=[false;2];
            self.cal_mask=0;self.error=0;
            for reg in [0x17,0x18,0x1a,0x1b,0x1c,0x1d,0x1f] {self.regs[reg]=0;}
            self.regs[0x10]=1;self.regs[0x19]=2;return;
        }
        match reg {
            0x1b=>{if value&!0x33!=0{panic!("MODEL_UNSUPPORTED: undocumented PWR_CTRL reserved-bit behavior");}},
            0x1c=>{if value&0xc0!=0{panic!("MODEL_UNSUPPORTED: undocumented OSR reserved-bit behavior");}},
            0x1d=>{if value&0xe0!=0{panic!("MODEL_UNSUPPORTED: undocumented ODR reserved-bit behavior");}},
            0x1f=>{if value&!0x0e!=0{panic!("MODEL_UNSUPPORTED: undocumented CONFIG reserved-bit behavior");}},
            0x17|0x18=>{if value&!0x1f!=0{panic!("MODEL_UNSUPPORTED: FIFO reserved bits");}},
            0x19=>{if value&!0x5f!=0{panic!("MODEL_UNSUPPORTED: interrupt reserved bits");}},0x1a=>{if value&7!=0{panic!("MODEL_UNSUPPORTED: watchdog or alternate interface settings");}},
            _=>panic!("MODEL_UNSUPPORTED: write register {:02x}",reg),
        }
        self.regs[reg as usize]=value;
        if reg==0x1b {
            if value&3==3&&value&0x30!=0 {self.next=Some(now+FIRST);self.error|=self.initial_error;}
            else {self.next=None;}
            self.ready=[false;2];
        }
    }
    fn read(&mut self,reg:u8)->u8 {
        match reg {
            0x00=>{self.identity_reads+=1;self.regs[0]},
            0x02=>{let value=self.error;self.error&=1;self.error_reads+=1;value},
            0x03=>self.status(),
            0x04..=0x09=>self.sample.raw[(reg-4) as usize],
            0x0c..=0x0e=>panic!("MODEL_UNSUPPORTED: bmp388_sensor_time_clock_not_modeled"),
            0x10=>{let value=self.regs[0x10];self.regs[0x10]=0;value},
            0x31..=0x45=>{self.cal_mask|=1<<(reg-0x31);self.regs[reg as usize]},
            _=>self.regs[reg as usize],
        }
    }
}
impl DeviceModel for Model {
    fn exchange(&mut self,now:u64,tx:&[u8],len:usize,buffer:&mut [u8]) {
        let start=now.saturating_sub(100);if start<self.ready_at{panic!("MODEL_UNSUPPORTED: before modeled 2ms reset completion; physical reset maximum not established");}self.tick(start);
        if tx.len()>1 {
            assert_eq!(len,0,"PROTOCOL: register/data writes must not simultaneously read");
            assert_eq!(tx.len()%2,0,"PROTOCOL: BMP388 I2C writes require address/data pairs");
            for pair in tx.chunks_exact(2){self.write(pair[0],pair[1],now);}self.tick(now);return;
        }
        if let Some(reg)=tx.first(){self.pointer=*reg;}
        if self.pointer as usize+len>128{panic!("MODEL_UNSUPPORTED: read beyond defined register range");}
        let before=self.ready;let mut mask=0u8;let error=self.error;
        for (i,byte) in buffer[..len].iter_mut().enumerate() {
            let reg=self.pointer+i as u8;*byte=self.read(reg);
            if (4..=9).contains(&reg){mask|=1<<(reg-4);}
        }
        if mask!=0 && self.generation>0 {
            let index=self.history.iter().position(|a|a.generation==self.generation);
            let eligible=[mask&0x38!=0 && before[0],mask&7!=0 && before[1]];
            if let Some(index)=index {
                self.history[index].mask|=mask;
                for ch in 0..2{self.history[index].eligible[ch]|=eligible[ch];}
            } else {self.history.push(Acquisition{generation:self.generation,sample:self.sample,mask,eligible});}
        }
        // Each DATA channel's first read byte consumes only its own flag.
        if mask&7!=0{self.ready[1]=false;}if mask&0x38!=0{self.ready[0]=false;}
        self.pointer=self.pointer.wrapping_add(len as u8);
        // Read transaction snapshots at START; conversion during it publishes after STOP.
        self.tick(now);
    }
    fn fault_matches(&self,at:FaultAt,tx:&[u8],len:usize)->bool {
        let reg=tx.first().copied().unwrap_or(self.pointer);
        let site=match at {FaultAt::Data=>"Data",FaultAt::Configuration=>"Configuration",FaultAt::Any=>self.fault_site};
        match site {
            "Data"=>len>0 && reg<=9 && reg as usize+len>4,
            "Calibration"=>len>0 && reg<=0x45 && reg as usize+len>0x31,
            "Configuration"=>tx.chunks_exact(2).any(|p|(0x1b..=0x1f).contains(&p[0])),
            "Mode"=>tx.chunks_exact(2).any(|p|p[0]==0x1b),
            "Status"=>len>0 && reg<=3 && reg as usize+len>3,
            "Error"=>len>0 && reg<=2 && reg as usize+len>2,
            _=>true,
        }
    }

}
#[derive(Debug)]struct Output{channel:usize,value:Result<i64,ErrorCode>}
struct Collector{model:&'static RefCell<Model>,pending:Cell<[usize;2]>,values:RefCell<Vec<Output>>}
impl Collector{fn add(&self,ch:usize,v:Result<i64,ErrorCode>){let mut n=self.pending.get();
    assert!(n[ch]>0,"CALLBACK: unsolicited, duplicate or wrong channel");n[ch]-=1;self.pending.set(n);
    if let Ok(x)=v{let m=self.model.borrow();m.configuration();
        let acquired=m.history.iter().filter(|a|if ch==0{a.mask&0x38==0x38}else{a.mask&7==7&&m.history.iter().any(|t|t.mask&0x38==0x38&&t.sample.raw[3..]==a.sample.raw[3..])}).collect::<Vec<_>>();
        assert!(!acquired.is_empty(),"DATA: no complete acquired channel with usable temperature compensation evidence");
        assert!(acquired.iter().any(|a|x>=a.sample.lower[ch]&&x<=a.sample.upper[ch]),"NUMERIC: compensation, unit or acquired sample coherence mismatch");
    }self.values.borrow_mut().push(Output{channel:ch,value:v});
}}
impl TemperatureClient for Collector{fn callback(&self,v:Result<i32,ErrorCode>){self.add(0,v.map(i64::from));}}
impl PressureClient for Collector{fn callback(&self,v:Result<u32,ErrorCode>){self.add(1,v.map(i64::from));}}
struct Rig{clock:&'static Cell<u64>,model:&'static RefCell<Model>,bus:&'static FakeI2c,alarm:&'static FakeAlarm,driver:&'static Driver,client:&'static Collector}
impl Rig{
    fn new(v:&Vector)->Self{let clock=Box::leak(Box::new(Cell::new(100_000)));let model=Box::leak(Box::new(RefCell::new(Model::new(v))));
        let bus=Box::leak(Box::new(FakeI2c{clock,model,pending:RefCell::new(None),client:Cell::new(None),fault:Cell::new(None),fault_hit:Cell::new(false),enabled:Cell::new(false)}));
        let alarm=Box::leak(Box::new(FakeAlarm{clock,due:Cell::new(None),client:Cell::new(None)}));
        let driver=Box::leak(Box::new(bmp388::Bmp388::new(bus,alarm,Box::leak(vec![0;21].into_boxed_slice()))));
        let client=Box::leak(Box::new(Collector{model,pending:Cell::new([0;2]),values:RefCell::new(vec![])}));
        bus.client.set(Some(driver));alarm.set_alarm_client(driver);TemperatureDriver::set_client(driver,client);PressureDriver::set_client(driver,client);
        Self{clock,model,bus,alarm,driver,client}}
    fn advance(&self,end:u64,complete:bool){
        executor::drive(self.clock, end, executor::EVENT_LIMIT, &[
            executor::Event { name: "bus", next: &|| self.bus.pending.borrow().as_ref().map(|p| p.due), fire: &|| self.bus.complete() },
            executor::Event { name: "alarm", next: &|| self.alarm.due.get(), fire: &|| self.alarm.fire() },
        ], || complete&&self.client.pending.get()==[0;2], |now| self.model.borrow_mut().tick(now));
    }
    fn init(&self){let _=self.driver.startup();self.advance(self.clock.get()+STARTUP,false);}
    fn request(&self,ch:usize)->Result<(),ErrorCode>{let before=self.client.values.borrow().len();let mut n=self.client.pending.get();n[ch]+=1;self.client.pending.set(n);
        let result=if ch==0{self.driver.read_temperature()}else{self.driver.read_atmospheric_pressure()};
        if result.is_err(){assert_eq!(self.client.values.borrow().len(),before,"CALLBACK: callback on rejected request");let mut n=self.client.pending.get();n[ch]-=1;self.client.pending.set(n);}result}
    fn complete_until(&self,start:usize,end:u64){self.advance(end,true);
        if self.client.pending.get()!=[0;2]{panic!("NOT_COMPLETED: accepted request lacks callback");}
        if self.client.values.borrow()[start..].iter().any(|v|v.value.is_err()){panic!("DATA: healthy measurement completed with error time_us={} values={:?}",self.clock.get(),&self.client.values.borrow()[start..]);}
        self.advance((self.clock.get()+5000).min(end),false);}
    fn sample_until(&self,ch:usize,end:u64){let start=self.client.values.borrow().len();executor::accept_request(self.clock, end, || self.request(ch), |until| self.advance(until, false), || self.client.values.borrow().len());self.complete_until(start,end.min(self.clock.get()+WINDOW));}
    fn sample(&self,ch:usize){self.sample_until(ch,self.clock.get()+WINDOW);}
    fn matches(&self,ch:usize,v:&Vector)->bool{self.client.values.borrow().iter().rev().find(|x|x.channel==ch).map_or(false,|x|x.value.map_or(false,|x|x>=v.lower[ch]&&x<=v.upper[ch]))}
}
fn run(id:&str,scope:&str,f:impl FnOnce()){
    if std::env::args().nth(1).as_deref()!=Some(id){return;}
    let(status,message)=match catch_unwind(AssertUnwindSafe(f)){Ok(())=>("pass",String::new()),Err(e)=>{
        let s=e.downcast_ref::<String>().cloned().or_else(||e.downcast_ref::<&str>().map(|s|s.to_string())).unwrap_or_else(||"candidate panic".into());
        let status=if s.starts_with("NOT_COMPLETED:"){"not_completed"}else if s.starts_with("MODEL_UNSUPPORTED:"){"model_unsupported"}else if s.starts_with("NOT_EXERCISED:"){"not_exercised"}else{"fail"};(status,s)}};
    println!("CASE\t{}\t{}\t{}\t{}",id,scope,status,message.replace('\t'," ").replace('\n'," "));
}
fn named(id:&str)->&'static Vector{VECTORS.iter().find(|v|v.id==id).unwrap()}
fn main(){std::panic::set_hook(Box::new(|_|{}));
    run("__model/retention_flags","internal",||{let mut m=Model::new(&VECTORS[0]);let mut b=[0;21];m.write(0x1b,0x33,100_000);m.tick(120_000);
        assert_eq!(m.status()&0x60,0x60);m.exchange(120_100,&[4],3,&mut b);assert_eq!(m.status()&0x60,0x40);let first=b[..3].to_vec();
        m.exchange(120_200,&[4],3,&mut b);assert_eq!(&b[..3],&first);});
    run("__model/transaction_shadow","internal",||{let mut m=Model::new(named("same_pressure_cold"));let mut b=[0;21];m.write(0x1b,0x33,100_000);m.tick(120_000);
        let old=m.sample;m.input=*named("coherence_hot_pressure");let due=m.next.unwrap();m.exchange(due+50,&[4],6,&mut b);
        assert_eq!(&b[..6],&old.raw);assert_eq!(m.sample.id,"coherence_hot_pressure");m.exchange(due+200,&[4],6,&mut b);assert_eq!(&b[..6],&m.input.raw);});
    run("__model/paired_write","internal",||{let mut m=Model::new(&VECTORS[0]);let mut b=[0;21];m.exchange(100_000,&[0x1d,3,0x1c,2,0x1b,0x33],0,&mut b);m.configuration();});
    for v in VECTORS{for ch in 0..2{run(&format!("numeric/{}/{}",v.id,ch),"core",||{let r=Rig::new(v);r.init();r.sample(ch);});}}
    for ch in 0..2{
        run(&format!("requests/first_available/{}",ch),"core",||{let r=Rig::new(&VECTORS[0]);let _=r.driver.startup();let end=r.clock.get()+STARTUP;let mut accepted=false;
            while r.clock.get()<end{if r.request(ch).is_ok(){accepted=true;break;}r.advance((r.clock.get()+100).min(end),false);}if !accepted{panic!("NOT_COMPLETED: first request never accepted");}r.complete_until(0,r.clock.get()+WINDOW);});
        run(&format!("requests/concurrent/{}",ch),"core",||{let r=Rig::new(&VECTORS[0]);r.init();executor::accept_request(r.clock, r.clock.get()+WINDOW, || r.request(ch), |until| r.advance(until, false), || r.client.values.borrow().len());
            let _=r.request(ch);let _=r.request(1-ch);r.complete_until(0,r.clock.get()+WINDOW);});
        run(&format!("requests/input_changes/{}",ch),"core",||{let r=Rig::new(&VECTORS[0]);r.init();r.sample(1-ch);
            for v in VECTORS.iter().filter(|v|v.cal==VECTORS[0].cal){r.model.borrow_mut().input=*v;let end=r.clock.get()+PROGRESS;let mut seen=false;
                while r.clock.get()<end{r.sample_until(ch,end);if r.matches(ch,v){seen=true;break;}r.advance((r.clock.get()+1000).min(end),false);}
                if !seen{panic!("NOT_COMPLETED: permanent old value under stable input change");}}});
        for site in ["Error","Status","Data"]{for asynchronous in [false,true]{run(&format!("diagnostic/bus/{}/{}/{}",ch,site,asynchronous),"diagnostic",||{
            let r=Rig::new(&VECTORS[0]);r.init();r.model.borrow_mut().fault_site=site;r.bus.fault.set(Some(Fault{at:FaultAt::Any,asynchronous}));let result=r.request(ch);r.advance(r.clock.get()+WINDOW,false);
            if !r.bus.fault_hit.get(){panic!("NOT_EXERCISED: fault site absent");}println!("OBS\tbus_fault\trequest={:?} pending={:?} values={:?}",result,r.client.pending.get(),r.client.values.borrow());});}}
        for error in [1u8,2,4]{run(&format!("diagnostic/device_error/{}/{}",ch,error),"diagnostic",||{let r=Rig::new(&VECTORS[0]);r.init();r.model.borrow_mut().error=error;let result=r.request(ch);r.advance(r.clock.get()+WINDOW,false);
            println!("OBS\tdevice_error\trequest={:?} pending={:?} error_reads={}",result,r.client.pending.get(),r.model.borrow().error_reads);});}
        run(&format!("diagnostic/stalled_flag/{}",ch),"diagnostic",||{let r=Rig::new(&VECTORS[0]);r.model.borrow_mut().stuck[ch]=true;r.init();let result=r.request(ch);r.advance(r.clock.get()+WINDOW,false);
            println!("OBS\tstalled_flag\trequest={:?} pending={:?}",result,r.client.pending.get());});
    }
    run("coherence/temperature_changes","core",||{let r=Rig::new(named("same_pressure_cold"));r.init();r.sample(0);
        for id in ["same_pressure_cold","same_pressure_hot","same_pressure_freezing","same_pressure_room"]{let v=named(id);r.model.borrow_mut().input=*v;let end=r.clock.get()+PROGRESS;let mut seen=false;
            while r.clock.get()<end{r.sample_until(1,end);if r.matches(1,v){seen=true;break;}r.advance((r.clock.get()+1000).min(end),false);}if !seen{panic!("NOT_COMPLETED: pressure compensation not tracking actual temperature");}}});
    run("coherence/turnover_between_transactions","core",||{let r=Rig::new(named("same_pressure_cold"));r.init();r.sample(0);let due=r.model.borrow().next.unwrap();
        r.advance(due-250,false);r.model.borrow_mut().input=*named("coherence_hot_pressure");r.sample(1);});
    for id in [0u8,0x60,0xff]{run(&format!("diagnostic/identity_{:02x}",id),"diagnostic",||{let r=Rig::new(&VECTORS[0]);r.model.borrow_mut().regs[0]=id;r.init();let result=r.request(0);r.advance(r.clock.get()+WINDOW,false);
        println!("OBS\tidentity\tprobes={} request={:?}",r.model.borrow().identity_reads,result);});}
    for site in ["Any","Calibration","Configuration","Mode"]{for asynchronous in [false,true]{run(&format!("diagnostic/init/{}/{}",site,asynchronous),"diagnostic",||{
        let r=Rig::new(&VECTORS[0]);r.model.borrow_mut().fault_site=site;r.bus.fault.set(Some(Fault{at:FaultAt::Any,asynchronous}));r.init();
        if !r.bus.fault_hit.get(){panic!("NOT_EXERCISED: startup injection absent");}let result=r.request(0);r.advance(r.clock.get()+WINDOW,false);println!("OBS\tinit_fault\trequest={:?} pending={:?}",result,r.client.pending.get());});}}
    run("diagnostic/command_not_ready","diagnostic",||{let r=Rig::new(&VECTORS[0]);r.model.borrow_mut().command_ready=false;r.init();let result=r.request(0);r.advance(r.clock.get()+WINDOW,false);println!("OBS\tcommand_not_ready\trequest={:?} pending={:?}",result,r.client.pending.get());});
    for error in [1u8,2,4]{run(&format!("diagnostic/init_error/{}",error),"diagnostic",||{let r=Rig::new(&VECTORS[0]);r.model.borrow_mut().initial_error=error;r.init();let result=r.request(0);r.advance(r.clock.get()+WINDOW,false);println!("OBS\tinitial_error\trequest={:?} pending={:?}",result,r.client.pending.get());});}
}
