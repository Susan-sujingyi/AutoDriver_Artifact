//! Independent DPS310 register/command model; Infineon V1.2 and disclosed policy.
mod executor;
use kernel::hil::sensors::{TemperatureClient,TemperatureDriver,PressureClient,PressureDriver};
use kernel::hil::time::Alarm;
use std::panic::{catch_unwind,AssertUnwindSafe};
use kernel::ErrorCode;
use std::cell::{Cell,RefCell};
mod dps310;mod support;
use support::{DeviceModel,FakeAlarm,Fault,FaultAt};
type FakeI2c=support::FakeI2c<Model>;
type Driver=dps310::Dps310<'static,FakeI2c,FakeAlarm>;
const WINDOW:u64=500_000;const STARTUP:u64=150_000;const PROGRESS:u64=5_000_000;
#[derive(Clone,Copy,Debug)]
struct Vector{id:&'static str,profile:usize,cal:[u8;18],raw:[i32;2],expected:[i64;2],lower:[i64;2],upper:[i64;2]}
#[derive(Clone,Copy)]struct Pair{profile:usize,t:i32,p:i32,expected:i64,lower:i64,upper:i64}
include!("vectors.rs");
#[derive(Clone,Copy,Debug)]struct Sample{generation:u64,at:u64,raw:i32,profile:usize}
#[derive(Clone,Copy)]struct Acquisition{ch:usize,sample:Sample,mask:u8}
struct Model{
    regs:[u8;128],pointer:u8,boot:u64,input:Vector,coef_source:u8,identity_reads:u32,source_reads:u32,
    cal_mask:u32,conversion_us:u64,pending:Option<(usize,u64,Vector)>,last_command:usize,
    ready:[bool;2],stuck:[bool;2],boot_stuck:u8,generation:u64,samples:[Sample;2],
    acquired:Vec<Acquisition>,invalid:[u64;2],corrected:bool,fault_site:&'static str,
    commands:[usize;2],
}
impl Model{
    fn new(v:&Vector)->Self{
        let mut regs=[0;128];regs[0x0d]=0x10;regs[0x10..0x22].copy_from_slice(&v.cal);
        let zero=Sample{generation:0,at:0,raw:0,profile:v.profile};
        Self{regs,pointer:0,boot:0,input:*v,coef_source:0x80,identity_reads:0,source_reads:0,
            cal_mask:0,conversion_us:14_800,pending:None,last_command:0,ready:[false;2],stuck:[false;2],
            boot_stuck:0,generation:0,samples:[zero;2],acquired:Vec::new(),invalid:[0;2],
            corrected:false,fault_site:"Any",commands:[0;2]}
    }
    fn tick(&mut self,now:u64){if let Some((ch,due,v))=self.pending{
        if now>=due&&!self.stuck[ch]{self.generation+=1;self.samples[ch]=Sample{generation:self.generation,at:due,raw:v.raw[ch],profile:v.profile};
            self.ready[ch]=true;self.pending=None;self.regs[8]=0;}
    }}
    fn status(&self,now:u64)->u8{
        (if now>=self.boot+12_000&&self.boot_stuck&0x40==0{0x40}else{0})|
        (if now>=self.boot+40_000&&self.boot_stuck&0x80==0{0x80}else{0})|
        (if self.ready[0]{0x20}else{0})|(if self.ready[1]{0x10}else{0})|self.regs[8]
    }
    fn configuration(&self){
        // Fixed known device does not require an ID probe.
        // Needed trims may be loaded lazily; independent numeric outputs are judged.
        // Actual selected sensor must match its factory calibration, not a required read trace.
        assert_eq!(self.regs[6]&0x0f,3,"CONFIG: selected pressure 8x");
        assert_eq!(self.regs[7]&0x8f,3|self.coef_source,"CONFIG: temperature 8x and factory calibration source");
        assert_eq!(self.regs[9]&0x7f,0,"CONFIG: selected shift/FIFO/interrupt/SPI options disabled");
        // Vendor workaround trace audited separately: no established universal silicon effect.
    }
    fn write(&mut self,now:u64,reg:u8,value:u8){
        match reg{
            6=>{if value&0x80!=0{panic!("MODEL_UNSUPPORTED: undocumented PRS_CFG reserved bit");}self.regs[6]=value;}
            7|9=>self.regs[reg as usize]=value,
            8=>{
                if value&8!=0{panic!("MODEL_UNSUPPORTED: MEAS_CFG reserved bit");}let mode=value&7;
                if mode>=5{panic!("CONFIG: selected command acquisition, background mode requested");}if mode>2{panic!("MODEL_UNSUPPORTED: undefined command mode");}
                if mode==0{self.pending=None;self.regs[8]=0;return;}
                self.configuration();if self.status(now)&0xc0!=0xc0{panic!("MODEL_UNSUPPORTED: acquisition before available calibration or initialization");}
                if self.pending.is_some(){panic!("MODEL_UNSUPPORTED: overlapping conversion commands");}let ch=if mode==2{0}else{1};
                self.ready[ch]=false;self.regs[8]=mode;self.last_command=ch;self.commands[ch]+=1;
                self.pending=Some((ch,now+self.conversion_us,self.input));
            }
            0x0c=>{if value&0x7f==0{return;}if value&0x7f!=9{panic!("MODEL_UNSUPPORTED: reset command");}self.boot=now;
                self.pending=None;self.ready=[false;2];self.corrected=false;self.invalid=[self.generation;2];
                self.cal_mask=0;self.source_reads=0;for r in [6,7,8,9,0x0e,0x0f,0x62]{self.regs[r]=0;}}
            0x0e=>{self.regs[0x0e]=value;}
            0x0f=>{self.regs[0x0f]=value;}
            0x62=>{self.corrected=value==2&&(self.regs[0x0e],self.regs[0x0f])==(0xa5,0x96);self.regs[0x62]=value;}
            _=>panic!("MODEL_UNSUPPORTED: write register {:02x}",reg),
        }
    }
    fn read(&mut self,now:u64,reg:u8)->u8{
        match reg{
            0..=5=>{let ch=if reg<3{1}else{0};let offset=(reg%3) as usize;
                ((self.samples[ch].raw as u32)>>(16-8*offset)) as u8}
            8=>self.status(now),0x0d=>{self.identity_reads+=1;self.regs[0x0d]},
            0x10..=0x21=>{if self.status(now)&0x80==0{panic!("MODEL_UNSUPPORTED: coefficients unavailable before COEF_RDY");}self.cal_mask|=1<<(reg-0x10);self.regs[reg as usize]},
            0x28=>{if self.status(now)&0x80==0{panic!("MODEL_UNSUPPORTED: factory source unavailable before COEF_RDY");}self.source_reads+=1;self.coef_source},
            _=>self.regs[reg as usize],
        }
    }
}
impl DeviceModel for Model{
    fn response_error(&mut self,now:u64,_tx:&[u8],_len:usize)->Option<kernel::hil::i2c::Error>{
        if now-100<self.boot+2_500{Some(kernel::hil::i2c::Error::AddressNak)}else{None}
    }
    fn exchange(&mut self,now:u64,tx:&[u8],len:usize,b:&mut [u8]){
        self.tick(now);
        if tx.len()>1{assert_eq!(len,0,"write/read configuration not selected");
            for (i,value) in tx[1..].iter().enumerate(){self.write(now,tx[0]+i as u8,*value);}return;}
        if let Some(reg)=tx.first(){self.pointer=*reg;}
        if self.pointer as usize+len>128{panic!("MODEL_UNSUPPORTED: register range");}
        let mut masks=[0u8;2];
        for (i,byte) in b[..len].iter_mut().enumerate(){let reg=self.pointer+i as u8;*byte=self.read(now,reg);
            if reg<=5{let ch=if reg<3{1}else{0};masks[ch]|=1<<(reg%3);}}
        for ch in 0..2{let sample=self.samples[ch];
            if masks[ch]!=0&&sample.generation>self.invalid[ch]{
                if let Some(a)=self.acquired.iter_mut().find(|a|a.ch==ch&&a.sample.generation==sample.generation){a.mask|=masks[ch];}
                else{self.acquired.push(Acquisition{ch,sample,mask:masks[ch]});}}
            if masks[ch]&4!=0{self.ready[ch]=false;}}
        self.pointer=self.pointer.wrapping_add(len as u8);
    }
    fn fault_matches(&self,_at:FaultAt,tx:&[u8],len:usize)->bool{
        let reg=tx.first().copied().unwrap_or(self.pointer);let writes=|address:u8,value:u8|tx.len()>1&&reg<=address&&(address-reg) as usize+1<tx.len()&&tx[(address-reg) as usize+1]==value;let contains=|address:u8|reg<=address&&reg as usize+len>address as usize;
        match self.fault_site{
            "Calibration"=>len>0&&reg<=0x21&&reg as usize+len>0x10,
            "Source"=>len>0&&contains(0x28),
            "Configuration"=>tx.len()>1&&reg<=9&&reg as usize+tx.len()-1>6,
            "Workaround"=>tx.len()>1&&(reg==0x0e||reg==0x0f||reg==0x62),
            "UnlockB"=>writes(0x0f,0x96),"Correction"=>writes(0x62,2),"RelockA"=>writes(0x0e,0),"RelockB"=>writes(0x0f,0),
            "TemperatureCommand"=>tx.len()>1&&reg<=8&&(8-reg) as usize+1<tx.len()&&tx[(8-reg) as usize+1]&7==2,
            "PressureCommand"=>tx.len()>1&&reg<=8&&(8-reg) as usize+1<tx.len()&&tx[(8-reg) as usize+1]&7==1,
            "TemperatureStatus"=>len>0&&contains(8)&&self.last_command==0&&self.commands[0]>0,
            "PressureStatus"=>len>0&&contains(8)&&self.last_command==1&&self.commands[1]>0,
            "TemperatureData"=>len>0&&reg<=5&&reg as usize+len>3,
            "PressureData"=>len>0&&reg<=2,
            _=>true,
        }
    }
}
#[derive(Debug)]struct Output{channel:usize,value:Result<i64,ErrorCode>}
struct Collector{model:&'static RefCell<Model>,pending:Cell<[usize;2]>,values:RefCell<Vec<Output>>}
impl Collector{fn add(&self,ch:usize,v:Result<i64,ErrorCode>){let mut n=self.pending.get();
    assert!(n[ch]>0,"CALLBACK: unsolicited, duplicate or wrong channel");n[ch]-=1;self.pending.set(n);
    if let Ok(x)=v{let m=self.model.borrow();m.configuration();
        let acquired=m.acquired.iter().filter(|a|a.ch==ch&&a.mask==7).collect::<Vec<_>>();
        assert!(!acquired.is_empty(),"DATA: no complete acquired result for selected channel");
        let valid=if ch==0{acquired.iter().any(|a|VECTORS.iter().any(|v|v.profile==a.sample.profile&&v.raw[0]==a.sample.raw&&x>=v.lower[0]&&x<=v.upper[0]))}
        else{assert!(m.acquired.iter().any(|a|a.ch==0&&a.mask==7),"DATA: pressure compensation lacks an acquired temperature");
            acquired.iter().any(|a|m.acquired.iter().filter(|t|t.ch==0&&t.mask==7).any(|t|PAIRS.iter().any(|p|
                p.profile==a.sample.profile&&t.sample.profile==p.profile&&p.p==a.sample.raw&&p.t==t.sample.raw&&x>=p.lower&&x<=p.upper)))};
        assert!(valid,"NUMERIC: signed packing, scale, polynomial or actually acquired operands mismatch");
    }self.values.borrow_mut().push(Output{channel:ch,value:v});
}}
impl TemperatureClient for Collector{fn callback(&self,v:Result<i32,ErrorCode>){self.add(0,v.map(i64::from));}}
impl PressureClient for Collector{fn callback(&self,v:Result<u32,ErrorCode>){self.add(1,v.map(i64::from));}}
struct Rig{clock:&'static Cell<u64>,model:&'static RefCell<Model>,bus:&'static FakeI2c,alarm:&'static FakeAlarm,driver:&'static Driver,client:&'static Collector}
impl Rig{
    fn new(v:&Vector)->Self{let clock=Box::leak(Box::new(Cell::new(100_000)));let model=Box::leak(Box::new(RefCell::new(Model::new(v))));
        let bus=Box::leak(Box::new(FakeI2c{clock,model,pending:RefCell::new(None),client:Cell::new(None),fault:Cell::new(None),fault_hit:Cell::new(false),enabled:Cell::new(false)}));
        let alarm=Box::leak(Box::new(FakeAlarm{clock,due:Cell::new(None),client:Cell::new(None)}));
        let driver=Box::leak(Box::new(dps310::Dps310::new(bus,alarm,Box::leak(vec![0;18].into_boxed_slice()))));
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
    run("__model/retained_data","internal",||{let mut m=Model::new(&VECTORS[0]);let mut b=[0;18];m.regs[6]=3;m.regs[7]=0x83;
        m.exchange(100_000,&[8,2],0,&mut b);m.exchange(114_900,&[3],3,&mut b);assert_eq!(m.status(114_900)&0x20,0);let old=b[..3].to_vec();
        m.exchange(115_000,&[3],3,&mut b);assert_eq!(&b[..3],&old);assert_eq!(m.acquired[0].mask,7);});
    run("__model/separate_conversions","internal",||{let mut m=Model::new(&VECTORS[0]);let mut b=[0;18];m.regs[6]=3;m.regs[7]=0x83;
        m.exchange(100_000,&[8,2],0,&mut b);m.exchange(115_000,&[3],3,&mut b);assert_eq!(m.samples[1].generation,0);
        m.exchange(115_100,&[8,1],0,&mut b);m.exchange(130_000,&[0],3,&mut b);assert!(m.samples[1].generation>m.samples[0].generation);});
    run("__model/workaround_audit_only","internal",||{let mut m=Model::new(&VECTORS[0]);m.regs[6]=3;m.regs[7]=0x83;m.configuration();
        assert!(!m.corrected);for(r,v)in[(0x0e,0xa5),(0x0f,0x96),(0x62,2),(0x0e,0),(0x0f,0)]{m.write(100_000,r,v);}assert!(m.corrected);m.configuration();});
    for v in VECTORS{for ch in 0..2{run(&format!("numeric/{}/{}",v.id,ch),"core",||{let r=Rig::new(v);r.init();r.sample(ch);});}}
    for ch in 0..2{
        run(&format!("requests/first_available/{}",ch),"core",||{let r=Rig::new(&VECTORS[0]);let _=r.driver.startup();let end=r.clock.get()+STARTUP;let mut accepted=false;
            while r.clock.get()<end{if r.request(ch).is_ok(){accepted=true;break;}r.advance((r.clock.get()+100).min(end),false);}if !accepted{panic!("NOT_COMPLETED: first request never accepted");}r.complete_until(0,r.clock.get()+WINDOW);});
        run(&format!("requests/concurrent/{}",ch),"core",||{let r=Rig::new(&VECTORS[0]);r.init();executor::accept_request(r.clock, r.clock.get()+WINDOW, || r.request(ch), |until| r.advance(until, false), || r.client.values.borrow().len());
            let _=r.request(ch);let _=r.request(1-ch);r.complete_until(0,r.clock.get()+WINDOW);});
        run(&format!("requests/input_changes/{}",ch),"core",||{let r=Rig::new(&VECTORS[0]);r.init();r.sample(1-ch);
            for v in VECTORS.iter().filter(|v|v.profile==VECTORS[0].profile){r.model.borrow_mut().input=*v;let end=r.clock.get()+PROGRESS;let mut seen=false;
                while r.clock.get()<end{r.sample_until(ch,end);if r.matches(ch,v){seen=true;break;}r.advance((r.clock.get()+10000).min(end),false);}
                if !seen{panic!("NOT_COMPLETED: old value under stable input change");}}});
        run(&format!("timing/slow_30ms/{}",ch),"core",||{let r=Rig::new(&VECTORS[0]);r.model.borrow_mut().conversion_us=30_000;r.init();r.sample(ch);});
        for site in ["TemperatureCommand","TemperatureStatus","TemperatureData","PressureCommand","PressureStatus","PressureData"]{
            if ch==0&&site.starts_with("Pressure"){continue;}
            for asynchronous in [false,true]{run(&format!("diagnostic/bus/{}/{}/{}",ch,site,asynchronous),"diagnostic",||{
                let r=Rig::new(&VECTORS[0]);r.init();r.model.borrow_mut().fault_site=site;r.bus.fault.set(Some(Fault{at:FaultAt::Any,asynchronous}));let result=r.request(ch);r.advance(r.clock.get()+WINDOW,false);
                if !r.bus.fault_hit.get(){panic!("NOT_EXERCISED: injection site absent");}println!("OBS\tbus_fault\trequest={:?} pending={:?} values={:?}",result,r.client.pending.get(),r.client.values.borrow());});}}
        run(&format!("diagnostic/stalled/{}",ch),"diagnostic",||{let r=Rig::new(&VECTORS[0]);r.model.borrow_mut().stuck[ch]=true;r.init();let result=r.request(ch);r.advance(r.clock.get()+WINDOW,false);
            println!("OBS\tstalled\trequest={:?} pending={:?}",result,r.client.pending.get());});
    }
    run("compensation/temperature_changes","core",||{let r=Rig::new(named("same_pressure_cold"));r.init();r.sample(0);
        for id in ["same_pressure_cold","same_pressure_hot","same_pressure_cold","same_pressure_hot"]{let v=named(id);r.model.borrow_mut().input=*v;let end=r.clock.get()+PROGRESS;let mut seen=false;
            while r.clock.get()<end{r.sample_until(1,end);if r.matches(1,v){seen=true;break;}r.advance((r.clock.get()+10000).min(end),false);}
            if !seen{panic!("NOT_COMPLETED: stable temperature change not reflected in pressure compensation");}}});
    for source in [0u8,0x80]{run(&format!("configuration/temperature_source_{:02x}",source),"core",||{let r=Rig::new(&VECTORS[0]);r.model.borrow_mut().coef_source=source;r.init();r.sample(0);r.sample(1);});}
    run("initialization/cold_boot","core",||{let r=Rig::new(&VECTORS[0]);r.model.borrow_mut().boot=r.clock.get();r.init();r.sample(0);r.sample(1);});
    run("diagnostic/workaround","diagnostic",||{let r=Rig::new(&VECTORS[0]);r.init();let m=r.model.borrow();
        println!("OBS\tvendor_workaround\tcorrection_written={} relocked={} physical_effect=not_established; omitted trace is not numeric-failure proof",m.corrected,m.regs[0x0e]==0&&m.regs[0x0f]==0);});
    for id in [0u8,0x11,0xff]{run(&format!("diagnostic/identity_{:02x}",id),"diagnostic",||{let r=Rig::new(&VECTORS[0]);r.model.borrow_mut().regs[0x0d]=id;r.init();let result=r.request(0);r.advance(r.clock.get()+WINDOW,false);
        println!("OBS\tidentity\treads={} request={:?}",r.model.borrow().identity_reads,result);});}
    for flag in [0x40u8,0x80]{run(&format!("diagnostic/boot_stalled_{:02x}",flag),"diagnostic",||{let r=Rig::new(&VECTORS[0]);r.model.borrow_mut().boot_stuck=flag;r.init();let result=r.request(0);r.advance(r.clock.get()+WINDOW,false);println!("OBS\tboot_stalled\trequest={:?} pending={:?}",result,r.client.pending.get());});}
    for site in ["Any","Calibration","Source","Configuration","Workaround","UnlockB","Correction","RelockA","RelockB"]{for asynchronous in [false,true]{run(&format!("diagnostic/init/{}/{}",site,asynchronous),"diagnostic",||{
        let r=Rig::new(&VECTORS[0]);r.model.borrow_mut().fault_site=site;r.bus.fault.set(Some(Fault{at:FaultAt::Any,asynchronous}));r.init();
        if !r.bus.fault_hit.get(){panic!("NOT_EXERCISED: startup injection absent");}let result=r.request(0);r.advance(r.clock.get()+WINDOW,false);println!("OBS\tinit_fault\trequest={:?} pending={:?}",result,r.client.pending.get());});}}
}
