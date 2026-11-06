//! DHT20 v2: raw manufacturer protocol and exact rational native-HIL outputs.
mod executor;
use kernel::hil::{sensors,time::Alarm};
use kernel::ErrorCode;
use std::cell::{Cell,RefCell};
use std::panic::{catch_unwind,AssertUnwindSafe};
mod dht20;
mod support;
use support::{DeviceModel,FakeAlarm,Fault,FaultAt};
type Bus=support::FakeI2c<Model>;
type Driver=dht20::Dht20<'static,Bus,FakeAlarm>;
const WINDOW:u64=500_000;
const STARTUP:u64=800_000;
const PROGRESS:u64=2_000_000;
const DEN:i128=1_048_576;
#[derive(Clone,Copy,Debug)]struct Vector{id:&'static str,frame:[u8;7],numerator:[i64;2]}
include!("vectors.rs");
#[derive(Clone,Copy,Debug,PartialEq)]enum Channel{Temperature,Humidity}
impl Channel{fn i(self)->usize{if self==Self::Temperature{0}else{1}}
    fn name(self)->&'static str{if self==Self::Temperature{"temperature"}else{"humidity"}}}
const CHANNELS:[Channel;2]=[Channel::Temperature,Channel::Humidity];
fn crc(data:&[u8])->u8{let mut p=0u64;for b in data{p=(p<<8)|*b as u64;}p=(p<<8)^(255u64<<(8*data.len()));
    while p>=256{let shift=63-p.leading_zeros()-8;p^=0x131u64<<shift;}p as u8}
struct Model{input:Vector,sample:Vector,calibration:u8,status_extra:u8,restored:u8,pending:Option<(u8,u64)>,
    read_at:Option<u64>,trigger_after:u64,status_seen:bool,due:Option<u64>,conversion:u64,valid:bool,
    stuck:bool,calibration_stuck:bool,corrupt:Option<(usize,u8)>,fault_frames:usize,history:Vec<Vector>}
impl Model{
    fn new(v:&Vector)->Self{Self{input:*v,sample:*v,calibration:0x18,status_extra:v.frame[0]&4,restored:0,pending:None,
        read_at:None,trigger_after:0,status_seen:false,due:None,conversion:80_000,valid:false,stuck:false,
        calibration_stuck:false,corrupt:None,fault_frames:0,history:vec![]}}
    fn tick(&mut self,now:u64){if !self.stuck&&self.due.map_or(false,|d|now>=d){self.sample=self.input;self.valid=true;self.due=None;}}
    fn status(&self)->u8{self.calibration|self.status_extra|if self.due.is_some(){0x80}else{0}}
    fn pair(reg:u8)->[u8;2]{match reg{0x1b=>[0xa5,0x13],0x1c=>[0x27,0xc9],0x1e=>[0x6d,0xe2],_=>unreachable!()}}
}
impl DeviceModel for Model{
    fn exchange(&mut self,now:u64,tx:&[u8],n:usize,b:&mut[u8]){self.tick(now);let start=now.saturating_sub(100);
        if !tx.is_empty(){
            if tx==[0x71]{panic!("PROTOCOL: 0x71 is the I2C read address, not a payload register");}
            assert_eq!(n,0,"PROTOCOL: commands and subsequent timed reads use separate transactions");
            match tx{
                [0xac,0x33,0]=>{
                    assert_eq!(self.calibration&0x18,0x18,"CONFIG: measurement started without calibrated status");
                    assert!(start>=self.trigger_after,"PROTOCOL: initial post-status/recovery 10ms wait omitted");
                    if self.due.is_some(){panic!("MODEL_UNSUPPORTED: overlapping conversion command");}
                    self.due=Some(now+self.conversion);
                },
                [r,0,0] if [0x1b,0x1c,0x1e].contains(r)=>{self.pending=Some((*r,now));self.read_at=None;},
                [r,a,c] if [0xbb,0xbc,0xbe].contains(r)=>{
                    let reg=r&0x1f;let (p,_)=self.pending.expect("PROTOCOL: calibration restore without read command");
                    assert_eq!(p,reg,"PROTOCOL: wrong calibration register restored");
                    assert_eq!([*a,*c],Self::pair(reg),"PROTOCOL: manufacturer calibration reply bytes changed");
                    let read=self.read_at.expect("PROTOCOL: calibration reply not read");
                    if start<read+10000{panic!("MODEL_UNSUPPORTED: below vendor example approximate recovery-write delay; no specified exact minimum");}
                    self.restored|=match reg{0x1b=>1,0x1c=>2,_=>4};self.pending=None;self.read_at=None;
                    if self.restored==7&&!self.calibration_stuck{self.calibration=0x18;self.trigger_after=now+10000;}
                },
                [0xac,_,_]=>panic!("CONFIG: measurement parameters differ from DHT20 command"),
                [0x9b,_,_]|[0x9c,_,_]|[0x9e,_,_]=>panic!("PROTOCOL: wrong documented calibration restore command"),
                _=>panic!("MODEL_UNSUPPORTED: command {:?}",tx),
            }return;
        }
        if n==0||n>7{panic!("MODEL_UNSUPPORTED: read length {}",n);}
        if !self.status_seen{self.status_seen=true;if self.calibration&0x18==0x18{self.trigger_after=now+10000;}}
        if n==3&&self.pending.is_some(){let (reg,sent)=self.pending.unwrap();
            if start<sent+5000{panic!("MODEL_UNSUPPORTED: below vendor example approximate recovery-read delay; no specified exact minimum");}
            let pair=Self::pair(reg);b[..3].copy_from_slice(&[self.status(),pair[0],pair[1]]);self.read_at=Some(now);return;}
        if self.pending.is_some()&&n>1{panic!("MODEL_UNSUPPORTED: partial or extended calibration reply");}
        let mut frame=self.sample.frame;frame[0]=self.status();frame[6]=crc(&frame[..6]);
        if let Some((byte,mask))=self.corrupt{frame[byte]^=mask;if n>byte{self.fault_frames+=1;}}
        b[..n].copy_from_slice(&frame[..n]);
        if n==7&&self.valid&&self.due.is_none()&&self.calibration&0x18==0x18&&crc(&frame[..6])==frame[6]{self.history.push(self.sample);}
    }
    fn fault_matches(&self,at:FaultAt,tx:&[u8],n:usize)->bool{match at{FaultAt::Any=>true,FaultAt::Data=>n>1,FaultAt::Configuration=>!tx.is_empty()}}
}
struct Collector{model:&'static RefCell<Model>,pending:Cell<[usize;2]>,values:RefCell<Vec<(Channel,Result<i64,ErrorCode>)>>}
impl Collector{fn add(&self,c:Channel,v:Result<i64,ErrorCode>){let mut p=self.pending.get();
    assert!(p[c.i()]>0,"CALLBACK: unsolicited, duplicate or wrong-channel callback");p[c.i()]-=1;self.pending.set(p);
    if let Ok(x)=v{let m=self.model.borrow();assert!(!m.history.is_empty(),"DATA: no complete ready calibrated CRC-valid acquired frame");
        assert!(m.history.iter().any(|v|((x as i128)*DEN-v.numerator[c.i()] as i128).abs()<=DEN),"NUMERIC: packed word, sign, unit or quantization differs from rational oracle");}
    self.values.borrow_mut().push((c,v));
}}
impl sensors::TemperatureClient for Collector{fn callback(&self,v:Result<i32,ErrorCode>){self.add(Channel::Temperature,v.map(i64::from));}}
impl sensors::HumidityClient for Collector{fn callback(&self,v:usize){self.add(Channel::Humidity,Ok(i64::try_from(v).expect("NUMERIC: full-width humidity outside physical integer range")));}}
struct Rig{clock:&'static Cell<u64>,model:&'static RefCell<Model>,bus:&'static Bus,alarm:&'static FakeAlarm,driver:&'static Driver,client:&'static Collector}
impl Rig{
    fn new(v:&Vector)->Self{let clock=Box::leak(Box::new(Cell::new(100_000)));let model=Box::leak(Box::new(RefCell::new(Model::new(v))));
        let bus=Box::leak(Box::new(Bus{clock,model,pending:RefCell::new(None),client:Cell::new(None),fault:Cell::new(None),fault_hit:Cell::new(false),enabled:Cell::new(false)}));
        let alarm=Box::leak(Box::new(FakeAlarm{clock,due:Cell::new(None),client:Cell::new(None)}));
        let driver=Box::leak(Box::new(dht20::Dht20::new(bus,alarm,Box::leak(vec![0;7].into_boxed_slice()))));
        let client=Box::leak(Box::new(Collector{model,pending:Cell::new([0;2]),values:RefCell::new(vec![])}));
        bus.client.set(Some(driver));alarm.set_alarm_client(driver);sensors::TemperatureDriver::set_client(driver,client);sensors::HumidityDriver::set_client(driver,client);
        Self{clock,model,bus,alarm,driver,client}}
    fn advance(&self,end:u64,complete:bool){
        executor::drive(self.clock, end, executor::EVENT_LIMIT, &[
            executor::Event { name: "bus", next: &|| self.bus.pending.borrow().as_ref().map(|p| p.due), fire: &|| self.bus.complete() },
            executor::Event { name: "alarm", next: &|| self.alarm.due.get(), fire: &|| self.alarm.fire() },
        ], || complete&&self.client.pending.get()==[0;2], |now| self.model.borrow_mut().tick(now));
    }
    fn init(&self){let _=self.driver.startup();self.advance(self.clock.get()+STARTUP,false);}
    fn request(&self,c:Channel)->Result<(),ErrorCode>{let before=self.client.values.borrow().len();let mut p=self.client.pending.get();p[c.i()]+=1;self.client.pending.set(p);
        let result=match c{Channel::Temperature=>sensors::TemperatureDriver::read_temperature(self.driver),Channel::Humidity=>sensors::HumidityDriver::read_humidity(self.driver)};
        if result.is_err(){assert_eq!(self.client.values.borrow().len(),before,"CALLBACK: callback on synchronously rejected request");let mut p=self.client.pending.get();p[c.i()]-=1;self.client.pending.set(p);}result}
    fn complete_until(&self,start:usize,end:u64){self.advance(end,true);
        if self.client.pending.get()!=[0;2]{panic!("NOT_COMPLETED: accepted request lacks callback within supervisor window");}
        if self.client.values.borrow()[start..].iter().any(|v|v.1.is_err()){panic!("DATA: healthy measurement completed with error time_us={} values={:?}",self.clock.get(),&self.client.values.borrow()[start..]);}
        self.advance((self.clock.get()+10_000).min(end),false);}
    fn sample_until(&self,c:Channel,end:u64){let start=self.client.values.borrow().len();
        executor::accept_request(self.clock, end, || self.request(c), |until| self.advance(until, false), || self.client.values.borrow().len());self.complete_until(start,end.min(self.clock.get()+WINDOW));}
    fn sample(&self,c:Channel){self.sample_until(c,self.clock.get()+WINDOW);}
    fn matches(&self,c:Channel,v:&Vector)->bool{self.client.values.borrow().iter().rev().find(|x|x.0==c).map_or(false,|x|match x.1{
        Ok(x)=>((x as i128)*DEN-v.numerator[c.i()] as i128).abs()<=DEN,Err(_)=>false})}
}
fn run(id:&str,scope:&str,f:impl FnOnce()){
    if std::env::args().nth(1).as_deref()!=Some(id){return;}
    let(status,message)=match catch_unwind(AssertUnwindSafe(f)){Ok(())=>("pass",String::new()),Err(e)=>{
        let s=e.downcast_ref::<String>().cloned().or_else(||e.downcast_ref::<&str>().map(|s|s.to_string())).unwrap_or_else(||"candidate panic".into());
        let status=if s.starts_with("NOT_COMPLETED:"){"not_completed"}else if s.starts_with("MODEL_UNSUPPORTED:"){"model_unsupported"}else if s.starts_with("NOT_EXERCISED:"){"not_exercised"}else{"fail"};(status,s)}};
    println!("CASE\t{}\t{}\t{}\t{}",id,scope,status,message.replace('\t'," ").replace('\n'," "));
}
fn main(){std::panic::set_hook(Box::new(|_|{}));
    run("__model/packing_crc","internal",||{for v in VECTORS{assert_eq!(crc(&v.frame[..6]),v.frame[6]);}});
    run("__model/ready_and_crc","internal",||{let mut m=Model::new(&VECTORS[0]);let mut b=[0;7];m.exchange(100_000,&[0xac,0x33,0],0,&mut b);
        m.exchange(110_000,&[],7,&mut b);assert!(m.history.is_empty());m.exchange(180_100,&[],7,&mut b);assert_eq!(m.history.len(),1);
        m.corrupt=Some((6,1));m.exchange(180_200,&[],7,&mut b);assert_eq!(m.history.len(),1);assert_eq!(m.fault_frames,1);});
    run("__model/recovery_pairs","internal",||{let mut m=Model::new(&VECTORS[0]);m.calibration=0;let mut b=[0;7];let mut t=100_000;
        for reg in [0x1e,0x1b,0x1c]{m.exchange(t,&[reg,0,0],0,&mut b);m.exchange(t+5100,&[],3,&mut b);let pair=[b[1],b[2]];m.exchange(t+15200,&[0xb0|reg,pair[0],pair[1]],0,&mut b);t+=16000;}
        assert_eq!(m.calibration,0x18);assert_eq!(m.restored,7);});
    for v in VECTORS{for c in CHANNELS{run(&format!("numeric/{}/{}",v.id,c.name()),"core",||{let r=Rig::new(v);r.init();r.sample(c);});}}
    for c in CHANNELS{
        run(&format!("requests/repeated_{}",c.name()),"core",||{let r=Rig::new(&VECTORS[0]);r.init();r.sample(CHANNELS[1-c.i()]);
            for v in VECTORS{r.model.borrow_mut().input=*v;let end=r.clock.get()+PROGRESS;let mut seen=false;
                while r.clock.get()<end{r.sample_until(c,end);if r.matches(c,v){seen=true;break;}r.advance((r.clock.get()+1000).min(end),false);}
                if !seen{panic!("NOT_COMPLETED: stable input not reflected by repeated same-channel requests");}}});
        run(&format!("requests/first_available/{}",c.name()),"core",||{let r=Rig::new(&VECTORS[0]);let _=r.driver.startup();let end=r.clock.get()+STARTUP;let mut accepted=false;
            while r.clock.get()<end{if r.request(c).is_ok(){accepted=true;break;}r.advance((r.clock.get()+100).min(end),false);}if !accepted{panic!("NOT_COMPLETED: initial request never accepted");}r.complete_until(0,r.clock.get()+WINDOW);});
        run(&format!("requests/concurrent/{}",c.name()),"core",||{let r=Rig::new(&VECTORS[0]);r.init();executor::accept_request(r.clock, r.clock.get()+WINDOW, || r.request(c), |until| r.advance(until, false), || r.client.values.borrow().len());
            for other in CHANNELS{let _=r.request(other);}r.complete_until(0,r.clock.get()+WINDOW);});
        for delay in [80000u64,100000,150000]{run(&format!("timing/{}/{}us",c.name(),delay),"core",||{let r=Rig::new(&VECTORS[0]);r.model.borrow_mut().conversion=delay;r.init();r.sample(c);});}
        for byte in [0,1,3,4,6]{run(&format!("crc/{}/byte{}",c.name(),byte),"core",||{let r=Rig::new(&VECTORS[0]);r.model.borrow_mut().corrupt=Some((byte,if byte==0{4}else{1}));
            r.init();let result=r.request(c);r.advance(r.clock.get()+WINDOW,false);if r.model.borrow().fault_frames==0{panic!("NOT_EXERCISED: corrupted frame not acquired");}
            println!("OBS\tcrc_safety\trequest={:?} pending={:?} values={:?}; bounded safety, not recovery",result,r.client.pending.get(),r.client.values.borrow());});}
        run(&format!("diagnostic/stalled/{}",c.name()),"diagnostic",||{let r=Rig::new(&VECTORS[0]);r.model.borrow_mut().stuck=true;r.init();let result=r.request(c);r.advance(r.clock.get()+WINDOW,false);
            println!("OBS\tstalled\trequest={:?} pending={:?} values={:?}",result,r.client.pending.get(),r.client.values.borrow());});
        for at in [FaultAt::Any,FaultAt::Configuration,FaultAt::Data]{for asynchronous in [false,true]{run(&format!("diagnostic/bus/{}/{:?}/{}",c.name(),at,asynchronous),"diagnostic",||{
            let r=Rig::new(&VECTORS[0]);r.init();r.bus.fault.set(Some(Fault{at,asynchronous}));let result=r.request(c);r.advance(r.clock.get()+WINDOW,false);
            if !r.bus.fault_hit.get(){panic!("NOT_EXERCISED: injection site absent");}println!("OBS\tbus_fault\trequest={:?} pending={:?} values={:?}",result,r.client.pending.get(),r.client.values.borrow());});}}
    }
    for status in [0,8,16]{run(&format!("initialization/recover_{status}"),"core",||{let r=Rig::new(&VECTORS[0]);r.model.borrow_mut().calibration=status;r.init();r.sample(Channel::Temperature);r.sample(Channel::Humidity);});}
    run("requests/input_changes","core",||{let r=Rig::new(&VECTORS[0]);r.init();for v in VECTORS{r.model.borrow_mut().input=*v;let end=r.clock.get()+PROGRESS;let mut seen=[false;2];
        while r.clock.get()<end&&seen!=[true;2]{for c in CHANNELS{if r.clock.get()>=end{break;}r.sample_until(c,end);if r.matches(c,v){seen[c.i()]=true;}}r.advance((r.clock.get()+1000).min(end),false);}
        if seen!=[true;2]{panic!("NOT_COMPLETED: stable input change not reflected within supervisor window");}}});
    for at in [FaultAt::Any,FaultAt::Configuration,FaultAt::Data]{for asynchronous in [false,true]{run(&format!("diagnostic/init/{:?}/{}",at,asynchronous),"diagnostic",||{
        let r=Rig::new(&VECTORS[0]);r.model.borrow_mut().calibration=0;r.bus.fault.set(Some(Fault{at,asynchronous}));r.init();
        if !r.bus.fault_hit.get(){panic!("NOT_EXERCISED: startup injection not reached");}let result=r.request(Channel::Temperature);r.advance(r.clock.get()+WINDOW,false);
        println!("OBS\tinit_fault\trequest={:?} pending={:?}",result,r.client.pending.get());});}}
    run("diagnostic/repeated_startup","diagnostic",||{let r=Rig::new(&VECTORS[0]);r.init();let _=r.driver.startup();r.advance(r.clock.get()+STARTUP,false);r.sample(Channel::Temperature);});
    run("diagnostic/calibration_stuck","diagnostic",||{let r=Rig::new(&VECTORS[0]);{let mut m=r.model.borrow_mut();m.calibration=0;m.calibration_stuck=true;}r.init();let result=r.request(Channel::Temperature);r.advance(r.clock.get()+WINDOW,false);
        println!("OBS\tcalibration_stuck\trequest={:?} pending={:?}",result,r.client.pending.get());});
}
