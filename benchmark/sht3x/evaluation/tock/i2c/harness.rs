//! SHT30 command and channel-CRC model; Sensirion SHT3x-DIS v7.
mod executor;
use kernel::hil::{i2c,sensors};
use kernel::hil::time::Alarm;
use kernel::ErrorCode;
use std::cell::{Cell,RefCell};
use std::panic::{catch_unwind,AssertUnwindSafe};
mod support;
mod sht3x;
use support::{DeviceModel,FakeAlarm,Fault,FaultAt};
type Bus=support::FakeI2c<Model>;
type Driver=sht3x::Sht3x<'static,Bus,FakeAlarm>;
const WINDOW:u64=250_000;
const PROGRESS:u64=1000000;
#[derive(Clone,Copy,Debug)]struct Vector{id:&'static str,bytes:[u8;6],lower:[i64;2],upper:[i64;2]}
include!("vectors.rs");
#[derive(Clone,Copy,Debug,PartialEq)]enum Channel{Temperature,Humidity}
impl Channel{fn i(self)->usize{if self==Self::Temperature{0}else{1}}
    fn name(self)->&'static str{if self==Self::Temperature{"temperature"}else{"humidity"}}}
const CHANNELS:[Channel;2]=[Channel::Temperature,Channel::Humidity];
fn crc(bytes:&[u8])->u8{let mut p=(255u32<<16)^(((bytes[0] as u32)*256+bytes[1] as u32)<<8);
    for i in (0..16).rev(){if p&(1<<(i+8))!=0{p^=0x131<<i;}}p as u8}
struct Model{input:Vector,sample:Vector,valid:bool,due:Option<u64>,conversion:u64,command_after:u64,busy_until:u64,
    status:u16,status_read:bool,stuck:bool,corrupt:Option<(usize,bool)>,fault_frames:usize,history:Vec<(Vector,[bool;2])>}
impl Model{
    fn new(v:&Vector)->Self{Self{input:*v,sample:*v,valid:false,due:None,conversion:15_000,command_after:0,busy_until:0,
        status:0x8010,status_read:false,stuck:false,corrupt:None,fault_frames:0,history:vec![]}}
    fn tick(&mut self,now:u64){if !self.stuck&&self.due.map_or(false,|d|now>=d){self.sample=self.input;self.valid=true;self.due=None;}}
    fn receive(&mut self,n:usize,b:&mut[u8]){if self.status_read{
        if n>3{panic!("MODEL_UNSUPPORTED: status read beyond three bytes");}let v=self.status.to_be_bytes();let bytes=[v[0],v[1],crc(&v)];b[..n].copy_from_slice(&bytes[..n]);self.status_read=false;return;
    }
        if n>6{panic!("MODEL_UNSUPPORTED: reading beyond six-byte measurement");}
        let mut bytes=self.sample.bytes;if let Some((word,data))=self.corrupt{bytes[word*3+if data{0}else{2}]^=1;
            if n>=word*3+3{self.fault_frames+=1;}}
        b[..n].copy_from_slice(&bytes[..n]);
        self.history.push((self.sample,[n>=3&&crc(&bytes[..2])==bytes[2],n>=6&&crc(&bytes[3..5])==bytes[5]]));
    }
}
impl DeviceModel for Model{
    fn response_error(&mut self,now:u64,tx:&[u8],n:usize)->Option<i2c::Error>{let start=now.saturating_sub(100);self.tick(start);
        if start<self.busy_until||(!tx.is_empty()&&start<self.command_after){return Some(i2c::Error::AddressNak);}
        if n>0&&tx!=[0xf3,0x2d]&&!self.status_read&&!self.valid{return Some(i2c::Error::AddressNak);}None}
    fn exchange(&mut self,now:u64,tx:&[u8],n:usize,b:&mut[u8]){self.tick(now);
        if !tx.is_empty(){assert_eq!(tx.len(),2,"PROTOCOL: 16-bit command required");let cmd=u16::from_be_bytes([tx[0],tx[1]]);
            if cmd!=0xf32d{assert_eq!(n,0,"PROTOCOL: measurement/config command cannot immediately read result");}
            self.command_after=now+1000;
            match cmd{
                0x2400=>{self.due=Some(now+self.conversion);self.valid=false;self.status_read=false;},
                0x3041=>self.status&=!0x8c10,
                0x3066=>self.status&=!0x2000,
                0x306d=>panic!("CONFIG: heater requested disabled"),
                0x30a2=>{self.status=0x8010;self.busy_until=now+1500;self.due=None;self.valid=false;self.status_read=false;},
                0xf32d=>self.status_read=true,
                0x3093=>{self.due=None;self.valid=false;self.busy_until=now+1000;},
                0x240b|0x2416|0x2c06|0x2c0d|0x2c10|0x2032|0x2130|0x2236|0x2334|0x2737=>panic!("CONFIG: selected high-repeatability non-stretch single shot"),
                0x0024=>panic!("PROTOCOL: command bytes do not encode a documented measurement"),
                _=>panic!("MODEL_UNSUPPORTED: command {:04x}",cmd),
            }
        }
        if n>0{self.receive(n,b);}
    }
    fn fault_matches(&self,at:FaultAt,tx:&[u8],n:usize)->bool{match at{FaultAt::Any=>true,FaultAt::Data=>n>0,FaultAt::Configuration=>tx==[0x24,0]}}
}
struct Collector{model:&'static RefCell<Model>,pending:Cell<[usize;2]>,values:RefCell<Vec<(Channel,Result<i64,ErrorCode>)>>}
impl Collector{fn add(&self,c:Channel,v:Result<i64,ErrorCode>){let mut p=self.pending.get();
    assert!(p[c.i()]>0,"CALLBACK: unsolicited/duplicate/wrong-channel callback");p[c.i()]-=1;self.pending.set(p);
    if let Ok(x)=v{let m=self.model.borrow();let data=m.history.iter().filter(|(_,good)|good[c.i()]).collect::<Vec<_>>();
        assert!(!data.is_empty(),"DATA: no complete, CRC-valid acquired word");
        assert!(data.iter().any(|(v,_)|x>=v.lower[c.i()]&&x<=v.upper[c.i()]),"NUMERIC: value/scale/width differs from exact rational quantization");}
    self.values.borrow_mut().push((c,v));
}}
impl sensors::TemperatureClient for Collector{fn callback(&self,v:Result<i32,ErrorCode>){self.add(Channel::Temperature,v.map(i64::from));}}
impl sensors::HumidityClient for Collector{fn callback(&self,v:usize){let x=i64::try_from(v).expect("NUMERIC: full-width humidity outside representable physical range");self.add(Channel::Humidity,Ok(x));}}
struct Rig{clock:&'static Cell<u64>,model:&'static RefCell<Model>,bus:&'static Bus,alarm:&'static FakeAlarm,driver:&'static Driver,client:&'static Collector}
impl Rig{
    fn new(v:&Vector)->Self{let clock=Box::leak(Box::new(Cell::new(100_000)));let model=Box::leak(Box::new(RefCell::new(Model::new(v))));
        let bus=Box::leak(Box::new(Bus{clock,model,pending:RefCell::new(None),client:Cell::new(None),fault:Cell::new(None),fault_hit:Cell::new(false),enabled:Cell::new(false)}));
        let alarm=Box::leak(Box::new(FakeAlarm{clock,due:Cell::new(None),client:Cell::new(None)}));
        let driver=Box::leak(Box::new(sht3x::Sht3x::new(bus,alarm,Box::leak(vec![0;6].into_boxed_slice()))));
        let client=Box::leak(Box::new(Collector{model,pending:Cell::new([0;2]),values:RefCell::new(vec![])}));
        bus.client.set(Some(driver));alarm.set_alarm_client(driver);sensors::TemperatureDriver::set_client(driver,client);sensors::HumidityDriver::set_client(driver,client);
        Self{clock,model,bus,alarm,driver,client}}
    fn advance(&self,end:u64,complete:bool){
        executor::drive(self.clock, end, executor::EVENT_LIMIT, &[
            executor::Event { name: "bus", next: &|| self.bus.pending.borrow().as_ref().map(|p| p.due), fire: &|| self.bus.complete() },
            executor::Event { name: "alarm", next: &|| self.alarm.due.get(), fire: &|| self.alarm.fire() },
        ], || complete&&self.client.pending.get()==[0;2], |now| self.model.borrow_mut().tick(now));
    }
    fn init(&self){let _=self.driver.startup();self.advance(self.clock.get()+100_000,false);}
    fn request(&self,c:Channel)->Result<(),ErrorCode>{let before=self.client.values.borrow().len();let mut p=self.client.pending.get();p[c.i()]+=1;self.client.pending.set(p);
        let result=match c{Channel::Temperature=>sensors::TemperatureDriver::read_temperature(self.driver),Channel::Humidity=>sensors::HumidityDriver::read_humidity(self.driver)};
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
    run("__model/crc_witness","internal",||{assert_eq!(crc(&[0xbe,0xef]),0x92);});
    run("__model/readiness","internal",||{let mut m=Model::new(&VECTORS[0]);let mut b=[0;6];m.exchange(100,&[0x24,0],0,&mut b);
        assert!(m.response_error(1000,&[],6).is_some());assert!(m.response_error(15200,&[],6).is_none());m.exchange(15200,&[],3,&mut b);assert_eq!(m.history[0].1,[true,false]);});
    run("__model/channel_crc","internal",||{let mut m=Model::new(&VECTORS[0]);m.valid=true;m.corrupt=Some((1,false));let mut b=[0;6];m.receive(6,&mut b);assert_eq!(m.history[0].1,[true,false]);});
    for v in VECTORS{for c in CHANNELS{run(&format!("numeric/{}/{}",v.id,c.name()),"core",||{let r=Rig::new(v);r.init();r.sample(c);});}}
    for c in CHANNELS{
        run(&format!("requests/repeated_{}",c.name()),"core",||{let r=Rig::new(&VECTORS[0]);r.init();r.sample(CHANNELS[1-c.i()]);
            for v in VECTORS{r.model.borrow_mut().input=*v;let end=r.clock.get()+PROGRESS;let mut seen=false;
                while r.clock.get()<end{r.sample_until(c,end);if let Some((_,Ok(x)))=r.client.values.borrow().iter().rev().find(|x|x.0==c){if *x>=v.lower[c.i()]&&*x<=v.upper[c.i()]{seen=true;break;}}r.advance((r.clock.get()+1000).min(end),false);}
                if !seen{panic!("NOT_COMPLETED: repeated same-channel requests keep old value despite stable input change");}}});
        run(&format!("requests/first_available/{}",c.name()),"core",||{let r=Rig::new(&VECTORS[0]);let _=r.driver.startup();let end=r.clock.get()+WINDOW;let mut accepted=false;
            while r.clock.get()<end{if r.request(c).is_ok(){accepted=true;break;}r.advance(r.clock.get()+100,false);}if !accepted{panic!("NOT_COMPLETED: initial request never accepted");}r.complete(0);});
        run(&format!("requests/concurrent/{}",c.name()),"core",||{let r=Rig::new(&VECTORS[0]);r.init();executor::accept_request(r.clock, r.clock.get()+WINDOW, || r.request(c), |until| r.advance(until, false), || r.client.values.borrow().len());
            for other in CHANNELS{let _=r.request(other);}r.complete(0);});
        for delay in [8000u64,12500,15000,15500]{run(&format!("timing/{}/{}us",c.name(),delay),"core",||{let r=Rig::new(&VECTORS[0]);r.model.borrow_mut().conversion=delay;r.init();r.sample(c);});}
        for data in [false,true]{run(&format!("crc/{}/{}",c.name(),if data{"data_bit"}else{"checksum_bit"}),"core",||{
            let r=Rig::new(&VECTORS[0]);r.model.borrow_mut().corrupt=Some((c.i(),data));r.init();let result=r.request(c);r.advance(r.clock.get()+WINDOW,false);
            if r.model.borrow().fault_frames==0{panic!("NOT_EXERCISED: corrupted word not acquired");}
            println!("OBS\tcrc_safety_window\trequest={:?} pending={:?} values={:?}; no recovery claim",result,r.client.pending.get(),r.client.values.borrow());});}
        run(&format!("diagnostic/unused_word_crc/{}",c.name()),"diagnostic",||{let r=Rig::new(&VECTORS[0]);r.model.borrow_mut().corrupt=Some((1-c.i(),false));r.init();
            let result=r.request(c);r.advance(r.clock.get()+WINDOW,false);println!("OBS\tunused_crc\trequest={:?} pending={:?} values={:?}",result,r.client.pending.get(),r.client.values.borrow());});
        run(&format!("diagnostic/stalled/{}",c.name()),"diagnostic",||{let r=Rig::new(&VECTORS[0]);r.model.borrow_mut().stuck=true;r.init();let result=r.request(c);
            r.advance(r.clock.get()+WINDOW,false);println!("OBS\tstalled\trequest={:?} pending={:?}",result,r.client.pending.get());});
        for at in [FaultAt::Any,FaultAt::Configuration,FaultAt::Data]{for asynchronous in [false,true]{run(&format!("diagnostic/bus/{}/{:?}/{}",c.name(),at,asynchronous),"diagnostic",||{
            let r=Rig::new(&VECTORS[0]);r.init();r.bus.fault.set(Some(Fault{at,asynchronous}));let result=r.request(c);r.advance(r.clock.get()+WINDOW,false);
            if !r.bus.fault_hit.get(){panic!("NOT_EXERCISED: injection site absent");}println!("OBS\tfault\trequest={:?} pending={:?} values={:?}",result,r.client.pending.get(),r.client.values.borrow());});}}
        for v in ENCODING{run(&format!("diagnostic/encoding/{}/{}",v.id,c.name()),"diagnostic",||{let r=Rig::new(v);r.init();let result=r.request(c);r.advance(r.clock.get()+WINDOW,false);
            println!("OBS\toutside_rated_temperature\trequest={:?} pending={:?} values={:?}",result,r.client.pending.get(),r.client.values.borrow());});}
    }
    run("requests/input_changes","core",||{let r=Rig::new(&VECTORS[0]);r.init();for v in VECTORS{r.model.borrow_mut().input=*v;let end=r.clock.get()+PROGRESS;let mut seen=[false;2];
        while r.clock.get()<end&&seen!=[true;2]{for c in CHANNELS{if r.clock.get()>=end{break;}r.sample_until(c,end);if let Some((_,Ok(x)))=r.client.values.borrow().iter().rev().find(|x|x.0==c){if *x>=v.lower[c.i()]&&*x<=v.upper[c.i()]{seen[c.i()]=true;}}}r.advance((r.clock.get()+1000).min(end),false);}
        if seen!=[true;2]{panic!("NOT_COMPLETED: stable physical change not reflected within observation");}}});
    run("diagnostic/repeated_startup","diagnostic",||{let r=Rig::new(&VECTORS[0]);r.init();let _=r.driver.startup();r.advance(r.clock.get()+WINDOW,false);r.sample(Channel::Temperature);});
}
