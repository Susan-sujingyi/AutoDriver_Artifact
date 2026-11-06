//! BME280: forced measurements, retained completed frames and independent compensation.
//! No per-request conversion, eager calibration, ID probe or global-idle requirement.
mod executor;
mod task_config;
use kernel::hil::sensors;
use kernel::hil::time::Alarm;
use kernel::ErrorCode;
use std::cell::{Cell, RefCell};
use std::panic::{catch_unwind, AssertUnwindSafe};
mod bme280;
mod support;
use support::{FakeAlarm,Fault,FaultAt};
mod spi_support;
use spi_support::SpiDeviceModel;
use kernel::hil::spi::{ClockPolarity,ClockPhase,SpiMasterDevice};
type Bus = spi_support::FakeSpi<Model>;
type Driver = bme280::Bme280<'static,Bus,FakeAlarm>;
const CONVERSION: u64 = 43_800; // §9.1 max for T1/P16/H1, modeled stimulus
const WINDOW: u64 = 1_000_000;
const PROGRESS:u64=2000000; // supervisor budget; not a device/API deadline
#[derive(Clone,Copy,Debug)]
struct Vector { id:&'static str, registers:[u8;256], raw:[u32;3], expected:[i64;3], lower:[i64;3], upper:[i64;3] }
include!("vectors.rs");
const CHANNELS: [&str;3] = ["temperature","humidity","pressure"];
const MASKS: [u8;3] = [0x38,0xf8,0x3f];
#[derive(Clone,Copy)]
struct Acquisition { vector:Vector, epoch:u64, mask:u8 }
struct Model {
    spi_rate:Cell<u32>, regs:[u8;256], pointer:u8, input:Vector, converting:Option<Vector>, completed:Option<Vector>,
    due:Option<u64>, epoch:u64, reset_until:u64, stalled:bool, reads:Vec<Acquisition>,
    id_reads:usize, triggers:usize, calibration_reads:usize,
}
impl Model {
    fn new(v:&Vector)->Self {
        Self { spi_rate:Cell::new(1_000_000),regs:v.registers,pointer:0,input:*v,converting:None,completed:None,due:None,
            epoch:0,reset_until:0,stalled:false,reads:vec![],id_reads:0,triggers:0,calibration_reads:0 }
    }
    fn tick(&mut self,now:u64) {
        if !self.stalled && self.due.map_or(false,|due|now>=due) {
            self.completed=self.converting.take();self.due=None;self.regs[0xf4]&=!3;self.epoch+=1;
        }
    }
    fn selected(&self,value:u8) {
        assert!(value&3==1 || value&3==2,"CONFIG: selected forced mode");
        assert_eq!((value>>5)&7,1,"CONFIG: temperature oversampling");
        assert_eq!((value>>2)&7,5,"CONFIG: pressure oversampling");
        assert_eq!(self.regs[0xf2]&7,1,"CONFIG: humidity oversampling");
        assert_eq!(self.regs[0xf5]&0x1c,0,"CONFIG: filter off");
        assert_eq!(self.regs[0xf5]&1,0,"CONFIG: selected four-wire SPI");
    }
    fn write(&mut self,reg:u8,value:u8,now:u64) {
        self.tick(now);
        match reg {
            0xe0 => {
                if value==0xb6 {
                    self.regs[0xf2]=0;self.regs[0xf4]=0;self.regs[0xf5]=0;
                    self.reset_until=now+2000;self.due=None;self.completed=None;self.converting=None;
                }
            },
            0xf2|0xf4|0xf5 => {
                if now<self.reset_until {
                    panic!("MODEL_UNSUPPORTED: configuration during NVM copy; effect not modeled");
                }
                if reg==0xf5 && self.due.is_some() && self.regs[reg as usize]!=value {
                    panic!("MODEL_UNSUPPORTED: config update during active conversion");
                }
                if reg==0xf4 && value&3!=0 {
                    self.selected(value);
                    // §9.2 forcing faster than the attainable rate saturates;
                    // it does not restart the current measurement indefinitely.
                    if self.due.is_none() {
                        self.converting=Some(self.input);self.due=Some(now+CONVERSION);self.triggers+=1;
                    }
                }
                self.regs[reg as usize]=value;
            },
            0x88..=0xa1|0xd0|0xe1..=0xe7|0xf3|0xf7..=0xfe => panic!("PROTOCOL: write to read-only register"),
            _ => panic!("MODEL_UNSUPPORTED: undocumented register write {:02x}",reg),
        }
    }
    fn read(&mut self,reg:u8,now:u64)->u8 {
        self.tick(now);
        match reg {
            0xd0 => {self.id_reads+=1;self.regs[0xd0]},
            0xf3 => (if self.due.is_some(){8}else{0}) | (if now<self.reset_until{1}else{0}),
            0xf2|0xf4|0xf5 => self.regs[reg as usize],
            0x88..=0xa1|0xe1..=0xe7 => {
                if now<self.reset_until {panic!("MODEL_UNSUPPORTED: trim read during NVM copy");}
                self.calibration_reads+=1;self.regs[reg as usize]
            },
            0xf7..=0xfe => {
                let v=self.completed;
                if let Some(vector)=v {
                    if let Some(a)=self.reads.iter_mut().find(|a|a.epoch==self.epoch) {a.mask|=1<<(reg-0xf7);}
                    else {self.reads.push(Acquisition{vector,epoch:self.epoch,mask:1<<(reg-0xf7)});}
                }
                // Reset/skipped-channel sentinel bytes are readable but not valid samples.
                let [t,p,h]=v.map_or([0x80000,0x80000,0x8000],|v|v.raw);
                [ (p>>12) as u8,(p>>4) as u8,(p<<4) as u8,(t>>12) as u8,
                  (t>>4) as u8,(t<<4) as u8,(h>>8) as u8,h as u8 ][(reg-0xf7) as usize]
            },
            _ => panic!("MODEL_UNSUPPORTED: undocumented register read {:02x}",reg),
        }
    }
}
impl SpiDeviceModel for Model {
    fn validate_bus(&self,p:ClockPolarity,h:ClockPhase,rate:u32) {
        task_config::validate_spi(p, h, rate);self.spi_rate.set(rate);
    }
    fn exchange(&mut self,now:u64,tx:&[u8],rx:&mut[u8]) {
        assert!(tx.len()>=2,"PROTOCOL: control and data required");
        let start=now.saturating_sub((tx.len() as u64*8*1_000_000+self.spi_rate.get() as u64-1)/self.spi_rate.get() as u64+10);
        rx.fill(0xa5);
        if tx[0]&0x80!=0 {
            // Shadow a completed frame at the read start; conversion finishing
            // during this CS transaction becomes visible to a later read.
            self.tick(start);let reg=tx[0];
            assert!(reg as usize+tx.len()-1<=256,"PROTOCOL: address range overflow");
            for(i,byte)in rx[1..].iter_mut().enumerate(){*byte=self.read(reg+i as u8,start);}
            self.tick(now);
        } else {
            assert_eq!(tx.len()%2,0,"PROTOCOL: Bosch paired writes");
            for p in tx.chunks_exact(2){assert_eq!(p[0]&0x80,0,"PROTOCOL: write RW bit");self.write(p[0]|0x80,p[1],now);}
        }
    }
    fn fault_matches(&self,at:FaultAt,tx:&[u8])->bool {
        let reading=tx.first().map_or(false,|x|x&0x80!=0);let reg=tx.first().copied().unwrap_or(0)|0x80;
        match at{FaultAt::Any=>true,FaultAt::Data=>reading && reg<=0xfe && reg as usize+tx.len()-1>0xf7,
            FaultAt::Configuration=>!reading && tx.chunks_exact(2).any(|p|matches!(p[0]|0x80,0xe0|0xf2|0xf4|0xf5))}
    }
}
#[derive(Debug)]
struct Output {ch:usize,value:Result<i64,ErrorCode>}
struct Collector {model:&'static RefCell<Model>,values:RefCell<Vec<Output>>,outstanding:Cell<[usize;3]>}
impl Collector {
    fn add(&self,ch:usize,value:Result<i64,ErrorCode>) {
        let mut n=self.outstanding.get();assert!(n[ch]>0,"CALLBACK: unsolicited/duplicate/wrong channel");n[ch]-=1;self.outstanding.set(n);
        if let Ok(value)=value {
            let m=self.model.borrow();
            assert!(m.reads.iter().any(|a|a.mask&MASKS[ch]==MASKS[ch]),"DATA: no complete valid channel/temperature acquisition");
            assert!(m.reads.iter().any(|a|a.mask&MASKS[ch]==MASKS[ch] && value>=a.vector.lower[ch] && value<=a.vector.upper[ch]),
                    "NUMERIC: value {} outside independent documented compensation envelopes for channel {}",value,ch);
        }
        self.values.borrow_mut().push(Output{ch,value});
    }
}
impl sensors::TemperatureClient for Collector {fn callback(&self,v:Result<i32,ErrorCode>){self.add(0,v.map(i64::from));}}
impl sensors::HumidityClient for Collector {fn callback(&self,v:usize){self.add(1,Ok(i64::try_from(v).expect("NUMERIC: humidity integer overflow")));}}
impl sensors::PressureClient for Collector {fn callback(&self,v:Result<u32,ErrorCode>){self.add(2,v.map(i64::from));}}
struct Rig {clock:&'static Cell<u64>,model:&'static RefCell<Model>,bus:&'static Bus,alarm:&'static FakeAlarm,
    driver:&'static Driver,client:&'static Collector}
impl Rig {
    fn new(v:&Vector)->Self {
        let clock=Box::leak(Box::new(Cell::new(100_000)));
        let model=Box::leak(Box::new(RefCell::new(Model::new(v))));
        let bus=Box::leak(Box::new(Bus::new(clock,model)));
        let alarm=Box::leak(Box::new(FakeAlarm{clock,due:Cell::new(None),client:Cell::new(None)}));
        let driver=Box::leak(Box::new(bme280::Bme280::new(bus,alarm,Box::leak(vec![0;32].into_boxed_slice()),Box::leak(vec![0;32].into_boxed_slice()))));
        let client=Box::leak(Box::new(Collector{model,values:RefCell::new(vec![]),outstanding:Cell::new([0;3])}));
        bus.client.set(Some(driver));alarm.set_alarm_client(driver);
        sensors::TemperatureDriver::set_client(driver,client);sensors::HumidityDriver::set_client(driver,client);sensors::PressureDriver::set_client(driver,client);
        Self{clock,model,bus,alarm,driver,client}
    }
    fn advance(&self,end:u64,complete:bool) {
        executor::drive(self.clock, end, executor::EVENT_LIMIT, &[
            executor::Event { name: "bus", next: &|| self.bus.pending.borrow().as_ref().map(|p| p.due), fire: &|| self.bus.complete() },
            executor::Event { name: "alarm", next: &|| self.alarm.due.get(), fire: &|| self.alarm.fire() },
        ], || complete && self.client.outstanding.get()==[0;3], |now| self.model.borrow_mut().tick(now));
    }
    fn init(&self) {let _=self.driver.startup();self.advance(self.clock.get()+WINDOW,false);}
    fn request(&self,ch:usize)->Result<(),ErrorCode> {
        let before=self.client.values.borrow().len();let mut n=self.client.outstanding.get();n[ch]+=1;self.client.outstanding.set(n);
        let result=match ch {0=>sensors::TemperatureDriver::read_temperature(self.driver),1=>sensors::HumidityDriver::read_humidity(self.driver),_=>sensors::PressureDriver::read_atmospheric_pressure(self.driver)};
        if result.is_err() {assert_eq!(self.client.values.borrow().len(),before,"CALLBACK: callback on rejected request");let mut n=self.client.outstanding.get();n[ch]-=1;self.client.outstanding.set(n);}
        result
    }
    fn complete(&self) {self.complete_until(self.clock.get()+WINDOW);}
    fn complete_until(&self,end:u64) {
        self.advance(end,true);
        if self.client.outstanding.get()!=[0;3] {panic!("NOT_COMPLETED: accepted request has no completion");}
        self.advance((self.clock.get()+5_000).min(end),false); // duplicate callbacks; background events allowed
    }
    fn sample(&self,ch:usize) {self.sample_until(ch,self.clock.get()+WINDOW);}
    fn sample_until(&self,ch:usize,end:u64) {
        let start=self.client.values.borrow().len();
        executor::accept_request(self.clock, end, || self.request(ch), |until| self.advance(until, false), || self.client.values.borrow().len());self.complete_until(end.min(self.clock.get()+WINDOW));
        if !self.client.values.borrow()[start..].iter().any(|v|v.ch==ch && v.value.is_ok()) {panic!("DATA: healthy acquisition completed with error channel={} time_us={} values={:?}",ch,self.clock.get(),&self.client.values.borrow()[start..]);}
    }
    fn fault(&self,at:FaultAt,asynchronous:bool,ch:usize) {
        self.bus.fault_hit.set(false);self.bus.fault.set(Some(Fault{at,asynchronous}));
        let _=self.request(ch);self.advance(self.clock.get()+WINDOW,false);
        if !self.bus.fault_hit.get(){panic!("NOT_EXERCISED: transport injection was not reached");}
        // Delivered values pass the observer; retry/error/cache/no scalar-error
        // policies remain visible as diagnostics rather than prescribed errors.
        println!("OBS\tfault\toutstanding={:?} callbacks={}",self.client.outstanding.get(),self.client.values.borrow().len());
    }
}
fn run(id:&str,scope:&str,f:impl FnOnce()) {
    if std::env::args().nth(1).as_deref()!=Some(id){return;}
    let (status,message)=match catch_unwind(AssertUnwindSafe(f)){Ok(())=>("pass",String::new()),Err(e)=>{
        let s=e.downcast_ref::<String>().cloned().or_else(||e.downcast_ref::<&str>().map(|s|s.to_string())).unwrap_or_else(||"candidate panic".into());
        let status=if s.starts_with("NOT_COMPLETED:"){ "not_completed" }else if s.starts_with("MODEL_UNSUPPORTED:"){"model_unsupported"}else if s.starts_with("NOT_EXERCISED:"){"not_exercised"}else{"fail"};(status,s)}};
    println!("CASE\t{}\t{}\t{}\t{}",id,scope,status,message.replace('\t'," ").replace('\n'," "));
}
fn main() {
    std::panic::set_hook(Box::new(|_|{}));
    run("__model/retained_frame","internal",||{
        let mut m=Model::new(&VECTORS[0]);m.write(0xf2,1,0);m.write(0xf4,0x35,0);m.tick(CONVERSION);
        assert!(m.completed.is_some());let first=m.read(0xfa,CONVERSION);
        m.input=VECTORS[1];m.write(0xf4,0x35,CONVERSION+10);assert_eq!(m.read(0xfa,CONVERSION+20),first);
        m.tick(2*CONVERSION+10);assert_eq!(m.completed.unwrap().id,VECTORS[1].id);
    });
    run("__model/forced_saturation_and_split_read","internal",||{
        let mut m=Model::new(&VECTORS[0]);m.write(0xf2,1,0);m.write(0xf4,0x35,0);m.write(0xf4,0x35,20);
        assert_eq!(m.due,Some(CONVERSION));let mut b=[0;8];m.exchange(CONVERSION+100,&[0xfa,0,0,0],&mut b[..4]);
        assert_eq!(m.reads[0].mask,0x38);assert_eq!(m.triggers,1);
    });
    run("__model/busy_returns_buffer","internal",||{
        let clock=Box::leak(Box::new(Cell::new(100_000)));
        let model=Box::leak(Box::new(RefCell::new(Model::new(&VECTORS[0]))));
        let bus=Bus::new(clock,model);bus.configure(task_config::SPI_POLARITY,task_config::SPI_PHASE,1_000_000).unwrap();
        bus.read_write_bytes(Box::leak(vec![0xd0;2].into_boxed_slice()),Some(Box::leak(vec![0;2].into_boxed_slice())),2).unwrap();
        let b=Box::leak(vec![0xf3;2].into_boxed_slice());let ptr=b.as_ptr();
        let rx=Box::leak(vec![0;2].into_boxed_slice());let rp=rx.as_ptr();
        let(err,b,rx)=bus.read_write_bytes(b,Some(rx),2).unwrap_err();
        assert_eq!(err,ErrorCode::BUSY);assert_eq!(ptr,b.as_ptr());assert_eq!(rp,rx.unwrap().as_ptr());
    });
    for v in VECTORS {for ch in 0..3 {run(&format!("numeric/{}/{}",v.id,CHANNELS[ch]),"core",||{let r=Rig::new(v);r.init();r.sample(ch);});}}
    for ch in 0..3 {
        run(&format!("requests/first_available/{}",CHANNELS[ch]),"core",||{
            let r=Rig::new(&VECTORS[0]);let _=r.driver.startup();
            executor::accept_request(r.clock,r.clock.get()+WINDOW,||r.request(ch),
                |until|r.advance(until,false),||r.client.values.borrow().len());
            r.complete();
            assert!(r.client.values.borrow().iter().any(|v|v.ch==ch&&v.value.is_ok()),
                "DATA: healthy first acquisition completed with error channel={} values={:?}",ch,r.client.values.borrow());
        });
        run(&format!("requests/concurrent/{}",CHANNELS[ch]),"core",||{
            let r=Rig::new(&VECTORS[0]);r.init();executor::accept_request(r.clock, r.clock.get()+WINDOW, || r.request(ch), |until| r.advance(until, false), || r.client.values.borrow().len());
            for other in 0..3 {let _=r.request(other);}r.complete();
        });
        run(&format!("requests/input_changes/{}",CHANNELS[ch]),"core",||{
            let r=Rig::new(&VECTORS[0]);r.init();r.sample((ch+1)%3);r.sample(ch);
            for v in &VECTORS[1..5] {
                r.model.borrow_mut().input=*v;let end=r.clock.get()+PROGRESS;let mut seen=false;
                while r.clock.get()<end {
                    r.advance((r.clock.get()+20_000).min(end),false);if r.clock.get()>=end{break;}r.sample_until(ch,end);
                    let values=r.client.values.borrow();let last=values.last().unwrap();
                    if last.value.map_or(false,|x|x>=v.lower[ch] && x<=v.upper[ch]) {seen=true;break;}
                }
                if !seen {panic!("NOT_COMPLETED: stable input change not reflected within observation window");}
            }
        });
        run(&format!("safety/no_completed_measurement/{}",CHANNELS[ch]),"diagnostic",||{
            let r=Rig::new(&VECTORS[0]);r.model.borrow_mut().stalled=true;r.init();let _=r.request(ch);r.advance(r.clock.get()+WINDOW,false);
        });
        for at in [FaultAt::Any,FaultAt::Data] {for asynchronous in [false,true] {
            run(&format!("fault/{}/{:?}/{}",CHANNELS[ch],at,if asynchronous{"async"}else{"immediate"}),"diagnostic",||{
                let r=Rig::new(&VECTORS[0]);r.init();r.fault(at,asynchronous,ch);
            });
        }}
    }
    run("requests/cross_channel","core",||{let r=Rig::new(&VECTORS[0]);r.init();for _ in 0..3 {for ch in 0..3 {r.sample(ch);}}});
    run("diagnostic/repeated_startup","diagnostic",||{let r=Rig::new(&VECTORS[0]);r.init();let _=r.driver.startup();r.advance(r.clock.get()+WINDOW,false);r.sample(0);});
    run("diagnostic/identity","diagnostic",||{let r=Rig::new(&VECTORS[0]);r.model.borrow_mut().regs[0xd0]=0x58;r.init();let result=r.request(0);r.advance(r.clock.get()+WINDOW,false);println!("OBS\tidentity\treads={} result={:?}",r.model.borrow().id_reads,result);});
}
