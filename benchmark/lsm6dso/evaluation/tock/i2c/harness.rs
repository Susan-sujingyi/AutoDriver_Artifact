//! ST DS12140 Rev3 / AN5192 Rev5 register and per-axis pair-BDU model.
//! Judge acquired per-axis values; do not invent a whole-XYZ same-epoch requirement.
mod executor;
use kernel::hil::sensors::{NineDof,NineDofClient};
use kernel::hil::time::Alarm;
use kernel::ErrorCode;
use std::cell::{Cell,RefCell};
use std::collections::BTreeMap;
mod lsm6dso;mod support;
use support::{DeviceModel,FakeAlarm,Fault,FaultAt};
type FakeI2c=support::FakeI2c<Model>;
type Driver=lsm6dso::Lsm6dso<'static,FakeI2c,FakeAlarm>;
const REQUEST:u64=500_000;const STARTUP:u64=150_000;const PROGRESS:u64=1_000_000;
use std::panic::{catch_unwind,AssertUnwindSafe};
#[derive(Clone,Copy,Debug)]
struct Vector{id:&'static str,raw:[u8;6],expected:[i64;3],lower:[i64;3],upper:[i64;3]}
include!("vectors.rs");
#[derive(Clone,Copy)]
struct Pair{epoch:u64,vector:Vector,settled:bool,read:u8}
struct Model{
    regs:[u8;128],bank:u8,pointer:u8,identity:u8,reset_until:Option<u64>,
    started:Option<u64>,first:u64,epoch_base:u64,generation:u64,ordinal:u64,
    input:Vector,sample:Vector,dynamic:bool,stall:bool,ready:bool,status_reads:u64,
    pairs:[Option<Pair>;3],reads:BTreeMap<u64,(u8,Vector,bool)>,
}
impl Model{
    fn defaults()->[u8;128]{let mut r=[0;128];r[2]=0x3f;r[0x12]=4;r[0x18]=0xe0;r}
    fn new(v:&Vector)->Self{Self{regs:Self::defaults(),bank:0,pointer:0,identity:0x6c,reset_until:None,started:None,first:9616,epoch_base:0,generation:0,ordinal:0,input:*v,sample:VECTORS[0],dynamic:false,stall:false,ready:false,status_reads:0,pairs:[None;3],reads:BTreeMap::new()}}
    fn tick(&mut self,now:u64){
        if let Some(due)=self.reset_until{if now<due{return}self.reset_until=None;self.regs=Self::defaults();self.bank=0;self.started=None;self.ready=false;self.pairs=[None;3];self.ordinal=0;}
        if let Some(start)=self.started{if !self.stall && now>=start+self.first{
            let ordinal=1+(now-start-self.first)*104/1_000_000;let generation=self.epoch_base+ordinal;
            if generation>self.generation{self.ordinal=ordinal;self.generation=generation;self.sample=if self.dynamic{VECTORS[generation as usize%VECTORS.len()]}else{self.input};
                if ordinal>1 || self.regs[0x13]&8==0{self.ready=true;}
            }
        }}
    }
    fn configuration(&self){
        assert_eq!(self.bank,0,"acquire through user register bank");assert!(self.reset_until.is_none(),"reset unfinished");
        assert_eq!(self.regs[0x10]&0xf3,0x40,"CONFIG: 104Hz/LPF2 off");let fs=(self.regs[0x10]>>2)&3;assert!(fs==0 || (fs==1 && self.regs[0x17]&2!=0),"selected±2g including documented equivalent FS encoding");assert_eq!(self.regs[0x11]&0xf0,0,"CONFIG: gyro must be off");
        assert_eq!(self.regs[0x13]&4,0,"I2C cannot be disabled");
        assert_eq!(self.regs[0x14]&0x8f,0,"CONFIG: no ULP/self-test");if self.regs[0x14]&0x70!=0{panic!("MODEL_UNSUPPORTED: rounding register sequence/reserved bit");}assert_eq!(self.regs[0x15]&0xf0,0,"CONFIG: high-performance, no DEN control");
        assert_eq!(self.regs[0x16]&2,0,"no user offset on output");assert_eq!(self.regs[0x17]&0xfc,0,"no HP/reference/filter changes");
        assert_eq!(self.regs[0x0a]&7,0,"FIFO bypass");
    }
    fn write(&mut self,reg:u8,value:u8,now:u64){
        if reg==1{if value&0x3f!=0||!matches!(value,0|0x40|0x80){panic!("MODEL_UNSUPPORTED: bank selection encoding");}self.bank=value;return}
        if self.bank!=0{if !(self.bank==0x40 && reg==0x14 && value&4==0){panic!("MODEL_UNSUPPORTED: alternate bank operation");}return}
        if self.reset_until.is_some(){panic!("MODEL_UNSUPPORTED: writes during reset");}
        match reg{2|0x07..=0x0e|0x10..=0x19|0x62|0x73..=0x75=>(),_=>panic!("MODEL_UNSUPPORTED: register write {reg:02x}")}
        if reg==0x12{if value&0x82!=0{panic!("MODEL_UNSUPPORTED: CTRL3 reserved/BOOT reload");}if value&1!=0{if self.regs[0x10]&0xf0!=0||self.regs[0x11]&0xf0!=0{panic!("MODEL_UNSUPPORTED: active sensor reset");}self.reset_until=Some(now+50);self.regs[0x12]=value;self.started=None;self.ready=false;return}}
        if reg==0x10{if value&0xf0==0{self.started=None;self.ready=false;}else if self.regs[0x10]&0xf0==0{self.started=Some(now);self.epoch_base=self.generation;self.ordinal=0;self.ready=false;self.pairs=[None;3];}}
        self.regs[reg as usize]=value;
    }
    fn byte(&mut self,reg:u8)->u8{
        if reg==1{return self.bank}
        if self.bank!=0{return 0} // inactive modeled alternate bank, no user identity/data
        match reg{
            0x0f=>self.identity,
            0x1e=>{self.status_reads+=1;if self.ready{1}else{0}},
            0x20..=0x27=>0xa5,
            0x28..=0x2d=>{
                let byte=(reg-0x28) as usize;let axis=byte/2;let part=byte%2;
                let mut pair=if self.regs[0x12]&0x40!=0{self.pairs[axis].unwrap_or(Pair{epoch:self.generation,vector:self.sample,settled:self.ordinal>1,read:0})}else{Pair{epoch:self.generation,vector:self.sample,settled:self.ordinal>1,read:0}};
                pair.read|=1<<part;let output=pair.vector.raw[byte];
                let entry=self.reads.entry(pair.epoch).or_insert((0,pair.vector,pair.settled));entry.0|=1<<byte;
                if self.regs[0x12]&0x40!=0{self.pairs[axis]=if pair.read==3{None}else{Some(pair)};}
                if part==1{self.ready=false;}
                output
            },
            2|0x07..=0x0e|0x10..=0x19|0x62|0x73..=0x75=>self.regs[reg as usize],
            _=>panic!("MODEL_UNSUPPORTED: register read {reg:02x}"),
        }
    }
}
impl DeviceModel for Model{
    fn exchange(&mut self,now:u64,tx:&[u8],read_len:usize,buffer:&mut[u8]){
        if !tx.is_empty(){assert!(tx[0]<128,"SPI flag is not an I2C address bit");self.pointer=tx[0];}
        if tx.len()>1{assert_eq!(read_len,0,"invalid combined register write/read");self.tick(now);let step=if self.regs[0x12]&4!=0{1}else{0};for (i,v) in tx[1..].iter().enumerate(){self.write(tx[0]+i as u8*step,*v,now);}self.pointer=tx[0]+(tx.len()-1) as u8*step;return}
        if read_len==0{self.tick(now);return}
        let step=if self.regs[0x12]&4!=0{1}else{0};let start=self.pointer;
        for i in 0..read_len{let at=now-100+(i as u64+1)*100/(read_len as u64+1);self.tick(at);let reg=start+i as u8*step;assert!(reg<128);buffer[i]=self.byte(reg);}
        self.tick(now);self.pointer=start+read_len as u8*step;
    }
    fn fault_matches(&self,at:FaultAt,tx:&[u8],read_len:usize)->bool{let p=tx.first().copied().unwrap_or(self.pointer);match at{FaultAt::Any=>true,FaultAt::Configuration=>tx.len()>1 && tx[0]!=1,FaultAt::Data=>read_len>0 && p<=0x2d && p as usize+read_len>0x28}}
}
#[derive(Debug)]struct Output{xyz:[i64;3]}
struct Collector{model:&'static RefCell<Model>,pending:Cell<usize>,values:RefCell<Vec<Output>>}
impl NineDofClient for Collector{
 fn callback(&self,x:usize,y:usize,z:usize){assert!(self.pending.get()>0,"CALLBACK: duplicate or unsolicited scalar callback");self.pending.set(self.pending.get()-1);
  let xyz=[x,y,z].map(|v|v as u32 as i32 as i64);let m=self.model.borrow();m.configuration();
  for axis in 0..3{let mask=3u8<<(2*axis);
   let acquired=m.reads.iter().filter(|(g,(bits,v,settled))|**g>0 && *settled && *bits&mask==mask).collect::<Vec<_>>();
   assert!(!acquired.is_empty(),"DATA: axis pair lacks complete valid acquired data");
   assert!(acquired.iter().any(|(_,(bits,v,settled))|xyz[axis]>=v.lower[axis]&&xyz[axis]<=v.upper[axis]),"NUMERIC: axis packing/sign/units/rounding differs from actual acquired data");
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
 for first in [9616, 15000, 20000]{run(&format!("timing/first_{}us",first),"core",||{let r=Rig::new(&VECTORS[1]);r.model.borrow_mut().first=first;r.init();r.sample();});}
 run("diagnostic/stalled","diagnostic",||{let r=Rig::new(&VECTORS[1]);r.model.borrow_mut().stall=true;r.init();let result=r.request();r.advance(r.clock.get()+REQUEST,false);println!("OBS\tstalled\trequest={:?} pending={}",result,r.client.pending.get());});
 for id in [0u8,0xff,0x69]{run(&format!("diagnostic/identity_{:02x}",id),"diagnostic",||{let r=Rig::new(&VECTORS[1]);r.model.borrow_mut().identity=id;r.init();let result=r.request();r.advance(r.clock.get()+REQUEST,false);println!("OBS\tidentity\t{:?}",result);});}
 for at in [FaultAt::Any,FaultAt::Data]{for asynchronous in [false,true]{run(&format!("diagnostic/request/{:?}/{}",at,asynchronous),"diagnostic",||{let r=Rig::new(&VECTORS[1]);r.init();r.bus.fault.set(Some(Fault{at,asynchronous}));let result=r.request();r.advance(r.clock.get()+REQUEST,false);if !r.bus.fault_hit.get(){panic!("NOT_EXERCISED: injection not reached");}println!("OBS\tfault\trequest={:?} pending={}",result,r.client.pending.get());});}}
 for at in [FaultAt::Any,FaultAt::Configuration]{for asynchronous in [false,true]{run(&format!("diagnostic/init/{:?}/{}",at,asynchronous),"diagnostic",||{let r=Rig::new(&VECTORS[1]);r.bus.fault.set(Some(Fault{at,asynchronous}));r.init();if !r.bus.fault_hit.get(){panic!("NOT_EXERCISED: injection not reached");}let result=r.request();r.advance(r.clock.get()+REQUEST,false);println!("OBS\tfault\trequest={:?}",result);});}}
 run("__model/callback_encoding","internal",||{for x in[i32::MIN,-1,0,i32::MAX]{assert_eq!(x as usize as u32 as i32,x);assert_eq!(x as u32 as usize as u32 as i32,x);}});
 for bank in[0x40u8,0x80]{run(&format!("diagnostic/initial_bank_{:02x}",bank),"diagnostic",||{let r=Rig::new(&VECTORS[1]);r.model.borrow_mut().bank=bank;r.init();let result=r.request();r.advance(r.clock.get()+REQUEST,false);println!("OBS\tbank\t{:?}",result);});}
 run("__model/pair_bdu_only","internal",||{let mut m=Model::new(&VECTORS[1]);m.regs[0x12]=0x44;m.write(0x10,0x40,100000);m.tick(140000);let xlo=m.byte(0x28);m.input=VECTORS[2];m.tick(160000);let xhi=m.byte(0x29);assert_eq!([xlo,xhi],[VECTORS[1].raw[0],VECTORS[1].raw[1]]);assert_eq!(m.byte(0x2a),VECTORS[2].raw[2]);});
 run("__model/retained_data","internal",||{let mut m=Model::new(&VECTORS[1]);m.regs[0x12]=0x44;m.write(0x10,0x40,100000);m.tick(140000);let a=m.byte(0x28);let b=m.byte(0x29);assert!(!m.ready);assert_eq!([m.byte(0x28),m.byte(0x29)],[a,b]);});
}
