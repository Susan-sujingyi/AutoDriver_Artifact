//! CCS811 mailbox behavior from ams DS000459 v1-06 and native Tock consumers.
mod executor;
use kernel::hil::sensors::{AirQualityClient,AirQualityDriver};
use kernel::hil::time::Alarm;
use kernel::ErrorCode;
use std::cell::{Cell,RefCell};
use std::panic::{catch_unwind,AssertUnwindSafe};
mod ccs811;
mod support;
use support::{DeviceModel,FakeAlarm,Fault,FaultAt};
type Bus=support::FakeI2c<Model>;
type Driver=ccs811::Ccs811<'static,Bus,FakeAlarm>;
const PERIOD:u64=1_000_000;
const WINDOW:u64=4_000_000;
const PROGRESS:u64=8000000; // supervisor observation, not chip/API deadline
#[derive(Clone,Copy,Debug)]
struct Vector{id:&'static str,bytes:[u8;8],expected:[u32;2]}
include!("vectors.rs");
#[derive(Clone,Copy)]struct Acquisition{sample:Vector,channels:usize}
struct Model{
    pointer:u8,hw_id:u8,app_valid:bool,app:bool,ready_at:u64,mode:u8,next:Option<u64>,
    ready:bool,stalled:bool,error:u8,input:Vector,sample:Vector,generation:u64,
    history:Vec<Acquisition>,identity_reads:usize,error_reads:usize,
}
impl Model{
    fn new(v:&Vector)->Self{Self{pointer:0,hw_id:0x81,app_valid:true,app:false,ready_at:0,mode:0,
        next:None,ready:false,stalled:false,error:0,input:*v,sample:*v,generation:0,
        history:vec![],identity_reads:0,error_reads:0}}
    fn tick(&mut self,now:u64){if !self.stalled{if let Some(due)=self.next{if now>=due{
        let n=(now-due)/PERIOD+1;self.generation+=n;self.sample=self.input;self.ready=true;self.next=Some(due+n*PERIOD);
    }}}}
    fn status(&self)->u8{(if self.app{0x80}else{0})|(if self.app_valid{0x10}else{0})|(if self.ready{8}else{0})|(if self.error!=0{1}else{0})}
    fn fields(&self){assert!(self.app,"CONFIG: firmware application is not running");assert_eq!(self.mode&0x70,0x10,"CONFIG: mode 1");}
    fn mailbox(&mut self,reg:u8,len:usize,b:&mut[u8]){
        assert!(len>0,"PROTOCOL: empty read");
        match reg{
            0x00=>{assert_eq!(len,1,"PROTOCOL: STATUS mailbox length");b[0]=self.status();},
            0x01=>{assert_eq!(len,1,"PROTOCOL: MEAS_MODE mailbox length");b[0]=self.mode;},
            0x02=>{
                assert!(len<=8,"PROTOCOL: ALG_RESULT_DATA maximum length");
                let mut bytes=if self.generation>0{self.sample.bytes}else{[0;8]};
                bytes[4]=self.status();bytes[5]=self.error;b[..len].copy_from_slice(&bytes[..len]);
                // DATA_READY clears on read. It does not erase the stored result;
                // a second channel may reuse an already completed result.
                if self.generation>0{self.fields();self.history.push(Acquisition{sample:self.sample,channels:if len>=4{2}else if len>=2{1}else{0}});}
                self.ready=false;
            },
            0x03=>{assert!(len<=2,"PROTOCOL: RAW_DATA maximum length");b[..len].copy_from_slice(&self.sample.bytes[6..6+len]);},
            0x20=>{assert_eq!(len,1,"PROTOCOL: HW_ID length");self.identity_reads+=1;b[0]=self.hw_id;},
            0x21=>{assert_eq!(len,1,"PROTOCOL: HW_VERSION length");b[0]=0x12;},
            0x23|0x24=>{assert!(len<=2,"PROTOCOL: firmware version length");b[..len].copy_from_slice(&[0x20,0][..len]);},
            0xe0=>{assert_eq!(len,1,"PROTOCOL: ERROR_ID length");b[0]=self.error;self.error=0;self.error_reads+=1;},
            _=>panic!("MODEL_UNSUPPORTED: mailbox read {:02x}",reg),
        }
    }
}
impl DeviceModel for Model{
    fn exchange(&mut self,now:u64,tx:&[u8],len:usize,b:&mut[u8]){
        self.tick(now);
        // DS000459 v1-06 pp17/26: these registers exist only in application
        // mode. Pointer selection alone is not a data access. This diagnoses
        // an invalid operation without inventing device ACK/NACK semantics.
        let accessed = tx.first().copied().unwrap_or(self.pointer);
        if !self.app && (len > 0 || tx.len() > 1)
            && matches!(accessed, 0x01 | 0x02 | 0x03 | 0x05 | 0x10 | 0x11 | 0xa0) {
            panic!("PROTOCOL: application-only mailbox {:02x} accessed in boot mode", accessed);
        }
        if let Some(reg)=tx.first().copied(){
            self.pointer=reg;
            if reg==0xff{
                assert_eq!(tx,[0xff,0x11,0xe5,0x72,0x8a],"PROTOCOL: SW_RESET sequence");assert_eq!(len,0,"PROTOCOL: reset read");
                self.app=false;self.mode=0;self.next=None;self.ready=false;self.generation=0;self.ready_at=now+2000;self.error=0;return;
            }
            if reg==0xf4{
                assert_eq!(tx,[0xf4],"PROTOCOL: APP_START is command-only");assert_eq!(len,0,"PROTOCOL: APP_START read");
                self.app=self.app_valid;self.ready_at=now+1000;return;
            }
            if tx.len()>1{
                assert_eq!(len,0,"PROTOCOL: write with read payload");
                if reg!=1{panic!("MODEL_UNSUPPORTED: optional mailbox write {:02x}",reg);}
                assert_eq!(tx.len(),2,"PROTOCOL: MEAS_MODE length");
                assert_eq!(tx[1]&0x83,0,"PROTOCOL: MEAS_MODE reserved bits");
                let old=self.mode;self.mode=tx[1];
                if self.mode&0x70==0{self.next=None;}else if self.next.is_none()||old&0x70!=self.mode&0x70{
                    if self.mode&0x70!=0x10{panic!("CONFIG: selected drive mode 1");}
                    self.next=Some(now+PERIOD);
                }
            }
        }
        if len>0{self.mailbox(self.pointer,len,b);}
    }
    fn response_error(&mut self,now:u64,_tx:&[u8],_len:usize)->Option<kernel::hil::i2c::Error>{
        // Figure7 has typical (not guaranteed max) start times. A command before
        // the modeled ready time NACKs, rather than an invented hard assertion.
        if now.saturating_sub(100)<self.ready_at{Some(kernel::hil::i2c::Error::AddressNak)}else{None}
    }
    fn fault_matches(&self,at:FaultAt,tx:&[u8],len:usize)->bool{
        let reg=tx.first().copied().unwrap_or(self.pointer);
        match at{FaultAt::Any=>true,FaultAt::Data=>reg==2&&len>0,FaultAt::Configuration=>reg==1&&tx.len()>1}
    }
}
#[derive(Debug)]struct Output{ch:usize,value:Result<u32,ErrorCode>}
struct Collector{model:&'static RefCell<Model>,values:RefCell<Vec<Output>>,pending:Cell<[usize;2]>,environment:Cell<usize>}
impl Collector{
    fn output(&self,ch:usize,value:Result<u32,ErrorCode>){
        let mut n=self.pending.get();assert!(n[ch]>0,"CALLBACK: unsolicited/duplicate/wrong-channel delivery");n[ch]-=1;self.pending.set(n);
        if let Ok(value)=value{let m=self.model.borrow();
            assert!(m.history.iter().any(|a|a.channels>ch),"DATA: no completed acquired gas sample");
            assert!(m.history.iter().any(|a|a.channels>ch&&a.sample.expected[ch]==value),"NUMERIC: gas word/byte order/unit mismatch");
        }
        self.values.borrow_mut().push(Output{ch,value});
    }
}
impl AirQualityClient for Collector{
    fn co2_data_available(&self,v:Result<u32,ErrorCode>){self.output(0,v);}
    fn tvoc_data_available(&self,v:Result<u32,ErrorCode>){self.output(1,v);}
    fn environment_specified(&self,_v:Result<(),ErrorCode>){self.environment.set(self.environment.get()+1);}
}
struct Rig{clock:&'static Cell<u64>,model:&'static RefCell<Model>,bus:&'static Bus,alarm:&'static FakeAlarm,driver:&'static Driver,client:&'static Collector}
impl Rig{
    fn new(v:&Vector)->Self{
        let clock=Box::leak(Box::new(Cell::new(100_000)));let model=Box::leak(Box::new(RefCell::new(Model::new(v))));
        let bus=Box::leak(Box::new(Bus{clock,model,pending:RefCell::new(None),client:Cell::new(None),fault:Cell::new(None),fault_hit:Cell::new(false),enabled:Cell::new(false)}));
        let alarm=Box::leak(Box::new(FakeAlarm{clock,due:Cell::new(None),client:Cell::new(None)}));
        let driver=Box::leak(Box::new(ccs811::Ccs811::new(bus,alarm,Box::leak(vec![0;8].into_boxed_slice()))));
        let client=Box::leak(Box::new(Collector{model,values:RefCell::new(vec![]),pending:Cell::new([0;2]),environment:Cell::new(0)}));
        bus.client.set(Some(driver));alarm.set_alarm_client(driver);AirQualityDriver::set_client(driver,client);
        Self{clock,model,bus,alarm,driver,client}
    }
    fn advance(&self,end:u64,complete:bool){
        executor::drive(self.clock, end, executor::EVENT_LIMIT, &[
            executor::Event { name: "bus", next: &|| self.bus.pending.borrow().as_ref().map(|p| p.due), fire: &|| self.bus.complete() },
            executor::Event { name: "alarm", next: &|| self.alarm.due.get(), fire: &|| self.alarm.fire() },
        ], || complete&&self.client.pending.get()==[0;2], |now| self.model.borrow_mut().tick(now));
    }
    fn init(&self){let _=self.driver.startup();self.advance(self.clock.get()+WINDOW,false);}
    fn request(&self,ch:usize)->Result<(),ErrorCode>{
        let before=self.client.values.borrow().len();let mut n=self.client.pending.get();n[ch]+=1;self.client.pending.set(n);
        let result=if ch==0{self.driver.read_co2()}else{self.driver.read_tvoc()};
        if result.is_err(){assert_eq!(self.client.values.borrow().len(),before,"CALLBACK: rejected request delivered value");let mut n=self.client.pending.get();n[ch]-=1;self.client.pending.set(n);}
        result
    }
    fn complete(&self){self.complete_until(self.clock.get()+WINDOW);}
    fn complete_until(&self,end:u64){self.advance(end,true);if self.client.pending.get()!=[0;2]{panic!("NOT_COMPLETED: accepted operation lacks completion");}self.advance((self.clock.get()+5_000).min(end),false);}
    fn sample(&self,ch:usize){self.sample_until(ch,self.clock.get()+WINDOW);}
    fn sample_until(&self,ch:usize,end:u64){let before=self.client.values.borrow().len();executor::accept_request(self.clock, end, || self.request(ch), |until| self.advance(until, false), || self.client.values.borrow().len());self.complete_until(end.min(self.clock.get()+WINDOW));
        if !self.client.values.borrow()[before..].iter().any(|v|v.ch==ch&&v.value.is_ok()){panic!("DATA: healthy request completed with error channel={} time_us={} values={:?}",ch,self.clock.get(),&self.client.values.borrow()[before..]);}}
}
fn run(id:&str,scope:&str,f:impl FnOnce()){
    if std::env::args().nth(1).as_deref()!=Some(id){return;}
    let(status,message)=match catch_unwind(AssertUnwindSafe(f)){Ok(())=>("pass",String::new()),Err(e)=>{
        let s=e.downcast_ref::<String>().cloned().or_else(||e.downcast_ref::<&str>().map(|s|s.to_string())).unwrap_or_else(||"candidate panic".into());
        let status=if s.starts_with("NOT_COMPLETED:"){"not_completed"}else if s.starts_with("MODEL_UNSUPPORTED:"){"model_unsupported"}else if s.starts_with("NOT_EXERCISED:"){"not_exercised"}else{"fail"};(status,s)}};
    println!("CASE\t{}\t{}\t{}\t{}",id,scope,status,message.replace('\t'," ").replace('\n'," "));
}
fn main(){
    std::panic::set_hook(Box::new(|_|{}));
    run("__model/mailbox_ready_retention","internal",||{
        let mut m=Model::new(&VECTORS[0]);let mut b=[0;8];m.exchange(0,&[0xf4],0,&mut b);m.exchange(1000,&[1,0x10],0,&mut b);m.tick(PERIOD+1000);
        assert!(m.ready);m.exchange(PERIOD+1000,&[2],5,&mut b);assert_ne!(b[4]&8,0);assert!(!m.ready);
        m.exchange(PERIOD+1200,&[2],4,&mut b);assert_eq!(m.history.len(),2);assert_eq!(b[..4],VECTORS[0].bytes[..4]);
    });
    run("__model/register_mode_availability","internal",||{
        let mut b=[0;8];
        let mut m=Model::new(&VECTORS[0]);
        m.exchange(0,&[0],1,&mut b);assert_eq!(b[0]&0x80,0);
        m.exchange(0,&[0x20],1,&mut b);assert_eq!(b[0],0x81);
        // Selecting an application register does not access its payload.
        m.exchange(0,&[1],0,&mut b);
        for reg in [0x01u8,0x02,0x03,0x05,0x10,0x11,0xa0] {
            for read in [false,true] {
                let mut boot=Model::new(&VECTORS[0]);
                let result=catch_unwind(AssertUnwindSafe(||{
                    if read {boot.exchange(0,&[reg],1,&mut b);}
                    else {boot.exchange(0,&[reg,0],0,&mut b);}
                }));
                let error=result.expect_err("application-only access was accepted in boot mode");
                let text=error.downcast_ref::<String>().unwrap();
                assert!(text.starts_with("PROTOCOL: application-only mailbox"));
            }
        }
        m.exchange(0,&[0xf4],0,&mut b);
        m.exchange(1000,&[1,0x10],0,&mut b);m.fields();
        m.exchange(1000,&[0],1,&mut b);assert_ne!(b[0]&0x80,0);
        // A legal but unmodeled optional application operation is still blocked.
        let optional=catch_unwind(AssertUnwindSafe(||m.exchange(1000,&[5,0,0,0,0],0,&mut b)));
        let error=optional.expect_err("optional ENV_DATA silently accepted");
        assert!(error.downcast_ref::<String>().unwrap().starts_with("MODEL_UNSUPPORTED:"));
    });
    run("__model/error_ack","internal",||{let mut m=Model::new(&VECTORS[0]);let mut b=[0;8];m.app=true;m.error=0x20;m.mailbox(2,8,&mut b);assert_eq!(m.error,0x20);m.mailbox(0xe0,1,&mut b);assert_eq!(b[0],0x20);assert_eq!(m.error,0);});
    for v in VECTORS{for ch in 0..2{run(&format!("numeric/{}/{}",v.id,ch),"core",||{let r=Rig::new(v);r.init();r.sample(ch);});}}
    for ch in 0..2{
        run(&format!("requests/first_available/{}",ch),"core",||{
            let r=Rig::new(&VECTORS[0]);let _=r.driver.startup();let end=r.clock.get()+WINDOW;let mut accepted=false;
            while r.clock.get()<end{if r.request(ch).is_ok(){accepted=true;break;}r.advance(r.clock.get()+1000,false);}
            if !accepted{panic!("NOT_COMPLETED: no first request accepted in startup observation");}
            r.complete();if !r.client.values.borrow().iter().any(|v|v.ch==ch&&v.value.is_ok()){panic!("DATA: healthy initial request completed with error channel={} time_us={} values={:?}",ch,r.clock.get(),r.client.values.borrow());}
        });
        run(&format!("requests/concurrent/{}",ch),"core",||{let r=Rig::new(&VECTORS[0]);r.init();executor::accept_request(r.clock, r.clock.get()+WINDOW, || r.request(ch), |until| r.advance(until, false), || r.client.values.borrow().len());let _=r.request(ch);let _=r.request(1-ch);r.complete();});
        run(&format!("requests/input_changes/{}",ch),"core",||{
            let r=Rig::new(&VECTORS[0]);r.init();r.sample(1-ch);r.sample(ch);
            for v in VECTORS{r.model.borrow_mut().input=*v;let end=r.clock.get()+PROGRESS;let mut seen=false;
                while r.clock.get()<end{r.advance((r.clock.get()+200_000).min(end),false);if r.clock.get()>=end{break;}r.sample_until(ch,end);
                    if r.client.values.borrow().last().unwrap().value==Ok(v.expected[ch]){seen=true;break;}}
                if !seen{panic!("NOT_COMPLETED: stable gas-input change not reflected within observation window");}
            }
        });
        for at in [FaultAt::Any,FaultAt::Data]{for asynchronous in [false,true]{
            run(&format!("diagnostic/bus/{}/{:?}/{}",ch,at,asynchronous),"diagnostic",||{let r=Rig::new(&VECTORS[0]);r.init();r.bus.fault.set(Some(Fault{at,asynchronous}));
                let _=r.request(ch);r.advance(r.clock.get()+WINDOW,false);if !r.bus.fault_hit.get(){panic!("NOT_EXERCISED: requested fault site absent");}
                println!("OBS\tfault\tpending={:?} values={:?}",r.client.pending.get(),r.client.values.borrow());
            });
        }}
        run(&format!("diagnostic/sensor_error/{}",ch),"diagnostic",||{let r=Rig::new(&VECTORS[0]);r.init();r.model.borrow_mut().error=0x20;let _=r.request(ch);r.advance(r.clock.get()+WINDOW,false);println!("OBS\terror\treads={} pending={:?} values={:?}",r.model.borrow().error_reads,r.client.pending.get(),r.client.values.borrow());});
        run(&format!("diagnostic/stalled/{}",ch),"diagnostic",||{let r=Rig::new(&VECTORS[0]);r.model.borrow_mut().stalled=true;r.init();let _=r.request(ch);r.advance(r.clock.get()+WINDOW,false);println!("OBS\tstalled\tpending={:?}",r.client.pending.get());});
    }
    run("requests/cross_channel","core",||{let r=Rig::new(&VECTORS[0]);r.init();for _ in 0..3{r.sample(0);r.sample(1);}});
    for invalid in INVALID_OUTPUTS{run(&format!("diagnostic/range/{}",invalid.id),"diagnostic",||{let r=Rig::new(invalid);r.init();for ch in 0..2{let _=r.request(ch);r.advance(r.clock.get()+WINDOW,false);}println!("OBS\trange\tvalues={:?}",r.client.values.borrow());});}
    run("diagnostic/identity","diagnostic",||{let r=Rig::new(&VECTORS[0]);r.model.borrow_mut().hw_id=0x80;r.init();let v=r.request(0);r.advance(r.clock.get()+WINDOW,false);println!("OBS\tidentity\treads={} request={:?}",r.model.borrow().identity_reads,v);});
    run("diagnostic/app_missing","diagnostic",||{let r=Rig::new(&VECTORS[0]);r.model.borrow_mut().app_valid=false;r.init();let v=r.request(0);r.advance(r.clock.get()+WINDOW,false);println!("OBS\tapp_missing\trequest={:?}",v);});
    run("diagnostic/environment_api","diagnostic",||{let r=Rig::new(&VECTORS[0]);r.init();let v=r.driver.specify_environment(Some(25),Some(50));r.advance(r.clock.get()+WINDOW,false);println!("OBS\tenvironment\trequest={:?} callbacks={}",v,r.client.environment.get());});
}
