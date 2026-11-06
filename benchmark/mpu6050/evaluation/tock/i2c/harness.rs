//! Independent MPU6050 digital register/epoch model: RM Rev4.2, PS Rev3.4.
mod executor;
use kernel::hil::sensors::{NineDof,NineDofClient};
use kernel::hil::time::Alarm;
use kernel::ErrorCode;
use std::cell::{Cell,RefCell};
use std::collections::BTreeMap;
mod mpu6050; mod support;
use support::{DeviceModel,FakeAlarm,Fault,FaultAt};
type FakeI2c=support::FakeI2c<Model>;
type Driver=mpu6050::Mpu6050<'static,FakeI2c,FakeAlarm>;
const REQUEST:u64=500_000;const STARTUP:u64=150_000;const PROGRESS:u64=1_000_000;
use std::panic::{catch_unwind,AssertUnwindSafe};
const PERIOD:u64=1_250;
#[derive(Clone,Copy,Debug)]
struct Vector {id:&'static str,raw:[u8;6],expected:[i64;3],lower:[i64;3],upper:[i64;3]}
include!("vectors.rs");
struct Model {
    regs:[u8;128],pointer:u8,identity:u8,wake:Option<u64>,first:u64,generation:u64,epoch_base:u64,
    input:Vector,sample:Vector,dynamic:bool,stall:bool,ready:bool,status_reads:u64, reset_until:Option<u64>,
    reads:BTreeMap<u64,(u8,Vector)>,
}
impl Model {
    fn new(v:&Vector)->Self {let mut regs=[0;128];regs[0x6b]=0x40;
        Self{regs,pointer:0,identity:0x68,wake:None,first:5_000,generation:0,epoch_base:0,input:*v,
            sample:VECTORS[0],dynamic:false,stall:false,ready:false,status_reads:0,reset_until:None,reads:BTreeMap::new()}}
    fn tick(&mut self,now:u64){
        if let Some(due)=self.reset_until {if now<due{return}self.reset_until=None;self.regs=[0;128];self.regs[0x6b]=0x40;self.ready=false;self.wake=None;}
        if let Some(wake)=self.wake {if !self.stall && now>=wake+self.first {
            let generation=self.epoch_base+(now-wake-self.first)/PERIOD+1;
            if generation>self.generation {self.generation=generation;
                self.sample=if self.dynamic {VECTORS[generation as usize % VECTORS.len()]}else{self.input};
                if self.regs[0x38]&1!=0 {self.ready=true;}
            }
        }}
    }
    fn configuration(&self){
        assert_eq!(self.regs[0x6b]&0xe0,0,"must be awake in continuous mode");
        assert!(self.regs[0x6b]&7<=3,"external/stopped clocks outside selected fixture");
        assert_eq!(self.regs[0x6c]&0x38,0,"acceleration axes cannot be in standby");
        assert_eq!(self.regs[0x19],9,"selected divider9");
        assert_eq!(self.regs[0x1a]&0x3f,0,"selected DLPF0 and no external FSYNC");
        assert_eq!(self.regs[0x1b]&0xf8,0,"gyro±250; self-test disabled");
        assert_eq!(self.regs[0x1c]&0xf8,0,"acceleration±2g; self-test disabled");
        assert_eq!(self.regs[0x23],0,"FIFO disabled");
        assert_eq!(self.regs[0x6a]&0xf0,0,"CONFIG: internal master/FIFO disabled");if self.regs[0x6a]&15!=0{panic!("MODEL_UNSUPPORTED: transient reset controls");}
    }
    fn write(&mut self,reg:u8,value:u8,now:u64){
        match reg {
            0x19|0x1a|0x1b|0x1c|0x23|0x37|0x38|0x6a|0x6b|0x6c=>(),
            _=>panic!("MODEL_UNSUPPORTED: register write {reg:02x}"),
        }
        if self.reset_until.is_some(){panic!("MODEL_UNSUPPORTED: write during reset");}
        if reg==0x6b {
            if value&0x10!=0{panic!("MODEL_UNSUPPORTED: PWR_MGMT reserved bit");}
            if value&0x80!=0 {self.reset_until=Some(now+2_000);self.wake=None;self.ready=false;self.regs[0x6b]=0x80;return}
            if value&0x40==0 && self.regs[0x6b]&0x40!=0 {self.epoch_base=self.generation;self.wake=Some(now);}
            if value&0x40!=0 {self.wake=None;self.ready=false;}
        }
        if reg==0x37 {if value&0x0d!=0{panic!("MODEL_UNSUPPORTED: reserved/FSYNC/clock output");}}
        if reg==0x38 {if value&!1!=0{panic!("MODEL_UNSUPPORTED: alternate interrupt sources");}}
        self.regs[reg as usize]=value;
    }
}
impl DeviceModel for Model {
    fn exchange(&mut self,now:u64,tx:&[u8],read_len:usize,buffer:&mut[u8]){
        self.tick(now.saturating_sub(100));
        if !tx.is_empty(){self.pointer=tx[0];assert!(self.pointer<128,"register address must not have SPI flag");
            if tx.len()>1 {assert_eq!(read_len,0,"register writes are pointer/data only");
                for (i,v) in tx[1..].iter().enumerate(){self.write(tx[0]+i as u8,*v,now);}
                self.pointer=tx[0]+(tx.len()-1) as u8;
            }
        }
        if read_len==0{return}
        assert!(tx.len()<=1,"invalid read prefix");
        assert!((self.pointer as usize)+read_len<=128,"read register overflow");
        // Burst snapshot is coherent even if more than one converter tick fits wire time.
        let start=self.pointer;
        for offset in 0..read_len {let reg=start+offset as u8;
            buffer[offset]=match reg {
                0x75=>self.identity,
                0x3a=>{self.status_reads+=1;let status=if self.ready {1}else{0};self.ready=false;status},
                0x3b..=0x40=>{
                    let bit=reg-0x3b;
                    let entry=self.reads.entry(self.generation).or_insert((0,self.sample));entry.0|=1<<bit;
                    self.sample.raw[bit as usize]
                },
                0x41..=0x48=>0xa5, // unrequested temperature/gyro are deliberately not acceleration
                0x19|0x1a|0x1b|0x1c|0x23|0x37|0x38|0x6a|0x6b|0x6c=>self.regs[reg as usize],
                _=>panic!("MODEL_UNSUPPORTED: register read {reg:02x}"),
            };
        }
        if self.regs[0x37]&0x10!=0 {self.ready=false;}
        self.pointer=start+read_len as u8;self.tick(now);
    }
    fn fault_matches(&self,at:FaultAt,tx:&[u8],read_len:usize)->bool {
        let pointer=tx.first().copied().unwrap_or(self.pointer);
        match at {FaultAt::Any=>true,FaultAt::Configuration=>tx.len()>1,
            FaultAt::Data=>read_len>0 && pointer<=0x40 && pointer as usize+read_len>0x3b}
    }
}
#[derive(Debug)]struct Output{xyz:[i64;3]}
struct Collector{model:&'static RefCell<Model>,pending:Cell<usize>,values:RefCell<Vec<Output>>}
impl NineDofClient for Collector{
 fn callback(&self,x:usize,y:usize,z:usize){assert!(self.pending.get()>0,"CALLBACK: duplicate or unsolicited scalar callback");self.pending.set(self.pending.get()-1);
  let xyz=[x,y,z].map(|v|v as u32 as i32 as i64);let m=self.model.borrow();m.configuration();
  for axis in 0..3{let mask=3u8<<(2*axis);
   let acquired=m.reads.iter().filter(|(g,(bits,v))|**g>0 && true && *bits&mask==mask).collect::<Vec<_>>();
   assert!(!acquired.is_empty(),"DATA: axis pair lacks complete valid acquired data");
   assert!(acquired.iter().any(|(_,(bits,v))|xyz[axis]>=v.lower[axis]&&xyz[axis]<=v.upper[axis]),"NUMERIC: axis packing/sign/units/rounding differs from actual acquired data");
  }self.values.borrow_mut().push(Output{xyz});
 }
}
struct Rig{clock:&'static Cell<u64>,model:&'static RefCell<Model>,bus:&'static FakeI2c,alarm:&'static FakeAlarm,driver:&'static Driver,client:&'static Collector}
impl Rig{
 fn new(v:&Vector)->Self{let clock=Box::leak(Box::new(Cell::new(100_000)));let model=Box::leak(Box::new(RefCell::new(Model::new(v))));let bus=Box::leak(Box::new(FakeI2c{clock,model,pending:RefCell::new(None),client:Cell::new(None),fault:Cell::new(None),fault_hit:Cell::new(false),enabled:Cell::new(false)}));let alarm=Box::leak(Box::new(FakeAlarm{clock,due:Cell::new(None),client:Cell::new(None)}));let driver=Box::leak(Box::new(Driver::new(bus,alarm,Box::leak(vec![0;14].into_boxed_slice()))));let client=Box::leak(Box::new(Collector{model,pending:Cell::new(0),values:RefCell::new(vec![])}));bus.client.set(Some(driver));alarm.set_alarm_client(driver);NineDof::set_client(driver,client);Self{clock,model,bus,alarm,driver,client}}
 fn advance(&self,end:u64,complete:bool){
        executor::drive(self.clock, end, executor::EVENT_LIMIT, &[
            executor::Event { name: "bus", next: &|| self.bus.pending.borrow().as_ref().map(|p| p.due), fire: &|| self.bus.complete() },
            executor::Event { name: "alarm", next: &|| self.alarm.due.get(), fire: &|| self.alarm.fire() },
        ], || complete&&self.client.pending.get()==0, |now| self.model.borrow_mut().tick(now));
    }
 fn init(&self){let _=self.driver.startup();self.advance(self.clock.get()+STARTUP,false);}
 fn request(&self)->Result<(),ErrorCode>{let n=self.client.values.borrow().len();self.client.pending.set(self.client.pending.get()+1);let result=self.driver.read_accelerometer();
  if result.is_err(){assert_eq!(self.client.values.borrow().len(),n,"CALLBACK: rejected request produced callback");self.client.pending.set(self.client.pending.get()-1);}result}
 fn complete_until(&self,end:u64){self.advance(end,true);if self.client.pending.get()!=0{panic!("NOT_COMPLETED: healthy request not completed");}self.advance((self.clock.get()+1000).min(end),false);}
 fn sample_until(&self,end:u64){executor::accept_request(self.clock, end, || self.request(), |until| self.advance(until, false), || self.client.values.borrow().len());self.complete_until(end.min(self.clock.get()+REQUEST));}
 fn sample(&self){self.sample_until(self.clock.get()+REQUEST);}
 fn progress(&self,v:&Vector){self.model.borrow_mut().input=*v;let end=self.clock.get()+PROGRESS;let mut seen=false;
  while self.clock.get()<end{self.sample_until(end);if self.client.values.borrow().last().map_or(false,|x|(0..3).all(|i|x.xyz[i]>=v.lower[i]&&x.xyz[i]<=v.upper[i])){seen=true;break;}self.advance((self.clock.get()+1000).min(end),false);}
  if !seen{panic!("NOT_COMPLETED: stable stimulus change not reflected");}}
}
fn run(id:&str,scope:&str,f:impl FnOnce()){
 if std::env::args().nth(1).as_deref()!=Some(id){return;}
 let(status,message)=match catch_unwind(AssertUnwindSafe(f)){Ok(())=>("pass",String::new()),Err(e)=>{
 let s=e.downcast_ref::<String>().cloned().or_else(||e.downcast_ref::<&str>().map(|s|s.to_string())).unwrap_or_else(||"candidate panic".into());
 let status=if s.starts_with("NOT_COMPLETED:"){"not_completed"}else if s.starts_with("MODEL_UNSUPPORTED:"){"model_unsupported"}else if s.starts_with("NOT_EXERCISED:"){"not_exercised"}else{"fail"};(status,s)}};
 println!("CASE\t{}\t{}\t{}\t{}",id,scope,status,message.replace('\t'," ").replace('\n'," "));
}
fn main(){std::panic::set_hook(Box::new(|_|{}));
 for v in VECTORS{run(&format!("numeric/{}",v.id),"core",||{let r=Rig::new(v);r.init();r.sample();println!("OBS\tvalue\t{:?}",r.client.values.borrow());});}
 run("requests/first_available","core",||{let r=Rig::new(&VECTORS[1]);let _=r.driver.startup();let end=r.clock.get()+STARTUP;let mut accepted=false;
  while r.clock.get()<end{if r.request().is_ok(){accepted=true;break;}r.advance((r.clock.get()+100).min(end),false);}if !accepted{panic!("NOT_COMPLETED: initialization never accepts request");}r.complete_until(r.clock.get()+REQUEST);});
 run("requests/concurrent","core",||{let r=Rig::new(&VECTORS[1]);r.init();executor::accept_request(r.clock, r.clock.get()+REQUEST, || r.request(), |until| r.advance(until, false), || r.client.values.borrow().len());let _=r.request();r.complete_until(r.clock.get()+REQUEST);});
 run("requests/stable_changes","core",||{let r=Rig::new(&VECTORS[1]);r.init();r.sample();for v in VECTORS.iter().chain(VECTORS[..3].iter()){r.progress(v);}});
 run("data/turnovers","core",||{let r=Rig::new(&VECTORS[1]);r.model.borrow_mut().dynamic=true;r.init();for i in 0..40{r.advance(r.clock.get()+i*317,false);r.sample();}});
 for first in [5000, 10000, 20000]{run(&format!("timing/first_{}us",first),"core",||{let r=Rig::new(&VECTORS[1]);r.model.borrow_mut().first=first;r.init();r.sample();});}
 run("diagnostic/stalled","diagnostic",||{let r=Rig::new(&VECTORS[1]);r.model.borrow_mut().stall=true;r.init();let result=r.request();r.advance(r.clock.get()+REQUEST,false);println!("OBS\tstalled\trequest={:?} pending={}",result,r.client.pending.get());});
 for id in [0u8,0xff,0x71]{run(&format!("diagnostic/identity_{:02x}",id),"diagnostic",||{let r=Rig::new(&VECTORS[1]);r.model.borrow_mut().identity=id;r.init();let result=r.request();r.advance(r.clock.get()+REQUEST,false);println!("OBS\tidentity\t{:?}",result);});}
 for at in [FaultAt::Any,FaultAt::Data]{for asynchronous in [false,true]{run(&format!("diagnostic/request/{:?}/{}",at,asynchronous),"diagnostic",||{let r=Rig::new(&VECTORS[1]);r.init();r.bus.fault.set(Some(Fault{at,asynchronous}));let result=r.request();r.advance(r.clock.get()+REQUEST,false);if !r.bus.fault_hit.get(){panic!("NOT_EXERCISED: injection not reached");}println!("OBS\tfault\trequest={:?} pending={}",result,r.client.pending.get());});}}
 for at in [FaultAt::Any,FaultAt::Configuration]{for asynchronous in [false,true]{run(&format!("diagnostic/init/{:?}/{}",at,asynchronous),"diagnostic",||{let r=Rig::new(&VECTORS[1]);r.bus.fault.set(Some(Fault{at,asynchronous}));r.init();if !r.bus.fault_hit.get(){panic!("NOT_EXERCISED: injection not reached");}let result=r.request();r.advance(r.clock.get()+REQUEST,false);println!("OBS\tfault\trequest={:?}",result);});}}
 run("__model/callback_encoding","internal",||{for x in[i32::MIN,-1,0,i32::MAX]{assert_eq!(x as usize as u32 as i32,x);assert_eq!(x as u32 as usize as u32 as i32,x);}});
 run("__model/burst_snapshot","internal",||{let mut m=Model::new(&VECTORS[1]);m.write(0x6b,0,100000);m.tick(110000);let due=100000+m.first+m.generation*PERIOD;m.input=VECTORS[2];let mut b=[0;14];m.exchange(due+50,&[0x3b],6,&mut b);assert_eq!(&b[..6],&VECTORS[1].raw);m.exchange(due+200,&[0x3b],6,&mut b);assert_eq!(&b[..6],&VECTORS[2].raw);});
 run("__model/retained_data","internal",||{let mut m=Model::new(&VECTORS[1]);m.write(0x6b,0,100000);m.regs[0x38]=1;m.tick(110000);let mut b=[0;14];m.exchange(110100,&[0x3a],1,&mut b);assert!(!m.ready);m.exchange(110200,&[0x3b],6,&mut b);assert_eq!(&b[..6],&VECTORS[1].raw);});
}
