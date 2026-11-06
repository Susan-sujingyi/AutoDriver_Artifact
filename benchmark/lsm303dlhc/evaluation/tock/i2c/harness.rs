//! Independent finite register model, ST DocID018771 Rev2 sections5.1.3/7.2.
mod executor;
use kernel::hil::sensors::{NineDof, NineDofClient};
use kernel::hil::time::Alarm;
use std::cell::{Cell, RefCell};
use std::panic::{catch_unwind, AssertUnwindSafe};
mod lsm303dlhc;
mod support;
use support::{DeviceModel, FakeAlarm, Fault, FaultAt};
type Bus = support::FakeI2c<Model>;
type Driver = lsm303dlhc::Lsm303dlhc<'static, Bus, FakeAlarm>;
#[derive(Clone, Copy, Debug)]
struct Vector { id: &'static str, raw: [u8; 6], lower: [i64; 3], upper: [i64; 3] }
include!("vectors.rs");

struct Model {
    regs: [u8; 13], pointer: u8, input: Vector, sample: Vector,
    started: Option<u64>, first: u64, generation: u64, ready: bool,
    locked: bool, read_mask: u8, acquired: Vec<Vector>, stall: bool,
}
impl Model {
    fn new(v: Vector) -> Self {
        let mut regs = [0u8; 13]; regs[0] = 0x10; regs[1] = 0x20; regs[2] = 3;
        regs[10] = 0x48; regs[11] = 0x34; regs[12] = 0x33;
        Self { regs, pointer: 0, input: v, sample: VECTORS[0], started: None,
            first: 66_667, generation: 0, ready: false, locked: false,
            read_mask: 0, acquired: Vec::new(), stall: false }
    }
    fn tick(&mut self, now: u64) {
        if let Some(start) = self.started {
            if !self.stall && !self.locked && now >= start + self.first {
                let generation = 1 + (now - start - self.first) / 66_667;
                if generation > self.generation {
                    self.generation = generation; self.sample = self.input;
                    self.ready = true; self.read_mask = 0;
                }
            }
        }
    }
    fn config(&self) {
        assert_eq!(self.regs[0], 0x10, "CONFIG: selected 15Hz, temperature disabled");
        assert_eq!(self.regs[1], 0x20, "CONFIG: selected +/-1.3gauss");
        assert_eq!(self.regs[2] & 3, 0, "CONFIG: continuous magnetic mode");
    }
    fn read(&mut self, reg: u8) -> u8 {
        match reg {
            0..=2 | 10..=12 => self.regs[reg as usize],
            9 => (if self.ready { 1 } else { 0 }) | (if self.locked { 2 } else { 0 }),
            3..=8 => {
                // Reading/discarding power-up registers is legal; only an actual
                // acquired conversion can justify a user-visible result.
                if self.generation == 0 { return 0; }
                self.locked = true;
                self.read_mask |= 1 << (reg - 3);
                let value = self.sample.raw[(reg-3) as usize];
                if self.read_mask == 0x3f {
                    self.acquired.push(self.sample); self.locked = false; self.ready = false;
                    self.read_mask = 0;
                }
                value
            }
            0x31 | 0x32 => panic!("MODEL_UNSUPPORTED: optional temperature channel"),
            _ => 0, // Section5.1.3: invalid register reads return zero.
        }
    }
    fn next(reg: u8) -> u8 { if reg == 8 { 3 } else if reg >= 12 { 0 } else { reg+1 } }
    fn write(&mut self, reg: u8, value: u8, now: u64) {
        if reg > 2 { return; } // Invalid/read-only writes are ignored by the device.
        let value = value & [0x9c, 0xe0, 3][reg as usize];
        let old = self.regs[reg as usize]; self.regs[reg as usize] = value;
        if reg == 2 && value & 3 == 0 && old & 3 != 0 {
            self.started = Some(now); self.generation = 0; self.ready = false;
        } else if reg == 2 && value & 3 != 0 {
            self.started = None; self.ready = false;
        }
    }
}
impl DeviceModel for Model {
    fn exchange(&mut self, now: u64, tx: &[u8], n: usize, buffer: &mut [u8]) {
        self.tick(now);
        if !tx.is_empty() {
            self.pointer = tx[0];
            for &value in &tx[1..] {
                self.write(self.pointer, value, now); self.pointer = Self::next(self.pointer);
            }
        }
        for byte in &mut buffer[..n] { *byte = self.read(self.pointer); self.pointer = Self::next(self.pointer); }
    }
    fn fault_matches(&self, at: FaultAt, tx: &[u8], n: usize) -> bool {
        let reg = tx.first().copied().unwrap_or(self.pointer);
        match at { FaultAt::Any => true, FaultAt::Data => n > 0 && (3..=8).contains(&reg),
                   FaultAt::Configuration => tx.len() > 1 && reg < 3 }
    }
}
struct Client { values: RefCell<Vec<[i64; 3]>>, model: &'static RefCell<Model> }
impl NineDofClient for Client {
    fn callback(&self, x: usize, y: usize, z: usize) {
        let values = [x as u32 as i32 as i64, y as u32 as i32 as i64, z as u32 as i32 as i64];
        let model = self.model.borrow();
        model.config();
        assert!(model.acquired.iter().any(|v| (0..3).all(|a| values[a] >= v.lower[a] && values[a] <= v.upper[a])),
            "NUMERIC: callback must match acquired, correctly scaled XYZ sample: {values:?}");
        self.values.borrow_mut().push(values);
    }
}
struct Rig { clock: &'static Cell<u64>, model: &'static RefCell<Model>, bus: &'static Bus,
             alarm: &'static FakeAlarm, driver: &'static Driver, client: &'static Client }
impl Rig {
    fn new(v: Vector) -> Self {
        let clock = Box::leak(Box::new(Cell::new(0)));
        let model = Box::leak(Box::new(RefCell::new(Model::new(v))));
        let bus = Box::leak(Box::new(Bus { clock, model, pending: RefCell::new(None),
            client: Cell::new(None), fault: Cell::new(None), fault_hit: Cell::new(false), enabled: Cell::new(false) }));
        let alarm = Box::leak(Box::new(FakeAlarm { clock, due: Cell::new(None), client: Cell::new(None) }));
        let driver = Box::leak(Box::new(Driver::new(bus, alarm, Box::leak(Box::new([0; 8])))));
        bus.client.set(Some(driver)); alarm.set_alarm_client(driver);
        let client = Box::leak(Box::new(Client { values: RefCell::new(Vec::new()), model }));
        driver.set_client(client);
        Self { clock, model, bus, alarm, driver, client }
    }
    fn advance(&self, end: u64) {
        executor::drive(self.clock, end, executor::EVENT_LIMIT, &[
            executor::Event { name: "bus", next: &|| self.bus.pending.borrow().as_ref().map(|p| p.due), fire: &|| self.bus.complete() },
            executor::Event { name: "alarm", next: &|| self.alarm.due.get(), fire: &|| self.alarm.fire() },
        ], || false, |now| self.model.borrow_mut().tick(now));
    }
    fn init(&self) {
        let _ = self.driver.startup(); self.advance(250_000);
        assert!(self.client.values.borrow().is_empty(), "CALLBACK: unsolicited startup callback");
    }
    fn count(&self) -> usize { self.client.values.borrow().len() }
    fn request(&self) -> [i64; 3] {
        let before = self.count();
        let end=self.clock.get()+500_000;
        executor::accept_request(self.clock,end,||self.driver.read_magnetometer(),|until|self.advance(until),||self.count());
        self.advance(end);
        let delta = self.count()-before;
        if delta == 0 { panic!("NOT_COMPLETED: accepted request has no result in supervision window"); }
        assert_eq!(delta, 1, "CALLBACK: accepted request must complete exactly once");
        self.client.values.borrow()[before]
    }
}
fn run(id: &str) {
    if let Some(name) = id.strip_prefix("numeric/") {
        let v = *VECTORS.iter().find(|v| v.id == name).expect("unknown vector");
        let r = Rig::new(v); r.init(); r.request(); return;
    }
    if id.starts_with("model/") {
        let v = VECTORS[1]; let mut m = Model::new(v); m.write(2,0,0); m.tick(70_000);
        match id {
            "model/axis_order" => { let mut b=[0;6]; m.exchange(70_000,&[3],6,&mut b); assert_eq!(b,v.raw); assert_eq!(m.acquired.len(),1); }
            "model/lock" => { let a=m.read(3);m.input=VECTORS[2];m.tick(200_000);assert_eq!(m.read(3),a);assert!(m.locked);for reg in 4..=8{m.read(reg);}m.tick(300_000);assert_eq!(m.sample.raw,VECTORS[2].raw); }
            "model/config_rejection" => { m.regs[1]=0x40;assert!(catch_unwind(AssertUnwindSafe(||m.config())).is_err()); }
            _ => panic!("unknown self-check"),
        }
        return;
    }
    let r=Rig::new(VECTORS[3]);
    if id=="transport/async_config" { r.bus.fault.set(Some(Fault{at:FaultAt::Configuration,asynchronous:true})); }
    if id=="protocol/delayed_ready" { r.model.borrow_mut().first=310_000; }
    if id=="requests/early" {
        let _=r.driver.startup(); let before=r.count(); let result=r.driver.read_magnetometer();
        r.advance(500_000);
        if result.is_err() { assert_eq!(r.count(),before,"CALLBACK: callback after rejected request"); }
        else {
            if r.count()==before { panic!("NOT_COMPLETED: early accepted request has no result"); }
            assert_eq!(r.count(),before+1,"CALLBACK: early accepted request completion");
        }
        r.request(); return;
    }
    r.init();
    match id {
        "requests/repeated" => { for _ in 0..5 {r.request();} }
        "requests/concurrent" => {
            let before=r.count();let a=r.driver.read_magnetometer();let b=r.driver.read_magnetometer();
            let accepted=usize::from(a.is_ok())+usize::from(b.is_ok());assert!(accepted>0,"NOT_COMPLETED: healthy requests rejected");
            r.advance(r.clock.get()+1_000_000);
            if r.count()-before<accepted { panic!("NOT_COMPLETED: concurrent accepted requests have missing results"); }
            assert_eq!(r.count()-before,accepted,"CALLBACK: completions vs accepted requests");r.request();
        }
        "requests/no_unsolicited" => { r.advance(r.clock.get()+500_000);assert_eq!(r.count(),0,"CALLBACK: unsolicited callback");r.request();let before=r.count();r.advance(r.clock.get()+500_000);assert_eq!(r.count(),before,"CALLBACK: duplicate/unsolicited callback"); }
        "data/stable_change" => {
            r.request(); r.model.borrow_mut().input=VECTORS[2];
            let end=r.clock.get()+1_000_000;let mut changed=false;
            while r.clock.get()<end {let v=r.request();if (0..3).all(|a|v[a]>=VECTORS[2].lower[a]&&v[a]<=VECTORS[2].upper[a]){changed=true;break;}}
            assert!(changed,"DATA: stable changed input never reaches client");
        }
        "protocol/delayed_ready" | "protocol/configuration" => {r.request();r.model.borrow().config();}
        "transport/async_config" => {assert!(r.bus.fault_hit.get(),"NOT_EXERCISED: configuration transaction absent");println!("OBS\tfault reached; startup recovery not mandated");}
        "device/stalled" => {r.model.borrow_mut().stall=true;r.model.borrow_mut().ready=false;let result=r.driver.read_magnetometer();r.advance(r.clock.get()+1_000_000);println!("OBS\tstalled request={result:?} callbacks={}",r.count());}
        "transport/immediate_data" | "transport/async_data" | "transport/repeated_after_fault" => {
            r.bus.fault.set(Some(Fault{at:FaultAt::Data,asynchronous:id!="transport/immediate_data"}));
            let first=r.driver.read_magnetometer();r.advance(r.clock.get()+500_000);
            assert!(r.bus.fault_hit.get(),"NOT_EXERCISED: no matching data transfer");
            println!("OBS\tfault hit request={first:?} callbacks={}",r.count());
            if id=="transport/repeated_after_fault"{let result=r.driver.read_magnetometer();r.advance(r.clock.get()+500_000);println!("OBS\tafter fault request={result:?} callbacks={}",r.count());}
        }
        _ => panic!("unknown case {id}"),
    }
}
fn main() {
    std::panic::set_hook(Box::new(|_| {}));
    let id=std::env::args().nth(1).expect("case id");
    let scope=if id.starts_with("model/"){"internal"}else if id.starts_with("transport/")||id.starts_with("device/"){"diagnostic"}else{"core"};
    let(status,detail)=match catch_unwind(AssertUnwindSafe(||run(&id))) {
        Ok(())=>("pass",String::new()),
        Err(e)=>{let s=e.downcast_ref::<String>().cloned().or_else(||e.downcast_ref::<&str>().map(|s|s.to_string())).unwrap_or_else(||"panic".into());
            let status=if s.contains("MODEL_UNSUPPORTED:"){"model_unsupported"}else if s.contains("NOT_COMPLETED:"){"not_completed"}else if s.contains("NOT_EXERCISED:"){"not_exercised"}else{"fail"};(status,s)}
    };
    println!("CASE\t{id}\t{scope}\t{status}\t{}",detail.replace('\n'," ").replace('\t'," "));
}
