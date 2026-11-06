//! APDS9960 v2: raw-device observations and actual Tock HIL; no v1 policy helpers.
mod executor;
use kernel::hil::sensors::{ProximityClient, ProximityDriver};
use kernel::hil::time::Alarm;
use kernel::ErrorCode;
use std::cell::{Cell, RefCell};
use std::panic::{catch_unwind, AssertUnwindSafe};
mod apds9960;
mod support;
use support::{DeviceModel, FakeAlarm, Fault, FaultAt};
type Bus = support::FakeI2c<Model>;
type Driver = apds9960::Apds9960<'static, Bus, FakeAlarm>;
const PERIOD: u64 = 1200; // bounded model stimulus, not a physical worst-case claim
const EXIT: u64 = 7000;
const WINDOW: u64 = 1_000_000; // host observation policy, not a driver deadline
#[derive(Clone, Copy, Debug)]
struct Vector { id: &'static str, raw: u8, expected: u8 }
include!("vectors.rs");
#[derive(Clone, Copy)]
struct Reading { raw: u8, initialized: bool }
struct Model {
    regs: [u8; 256], pointer: u8, input: u8, sample: u8,
    next: Option<u64>, needs_exit: bool, initialized: bool,
    valid: bool, stalled: bool, pint: bool, outside_cycles: u64,
    last_read: Option<Reading>, reads: u64, identity_reads: u64,
}
impl Model {
    fn new(v: &Vector) -> Self {
        let mut regs = [0; 256];
        regs[0x81] = 0xff; regs[0x83] = 0xff;
        regs[0x8d] = 0x60; regs[0x8e] = 0x40; regs[0x90] = 1; regs[0x92] = 0xab;
        Self { regs, pointer: 0, input: v.raw, sample: 0, next: None, needs_exit: true,
            initialized: false, valid: false, stalled: false, pint: false, outside_cycles: 0,
            last_read: None, reads: 0, identity_reads: 0 }
    }
    fn tick(&mut self, now: u64) {
        if let Some(due) = self.next {
            if now >= due {
                let count = (now - due) / PERIOD + 1;
                self.next = Some(due + count * PERIOD);
                if !self.stalled {
                    self.sample = self.input; self.valid = true; self.initialized = true;
                    if self.sample < self.regs[0x89] || self.sample > self.regs[0x8b] {
                        self.outside_cycles += count;
                    } else { self.outside_cycles = 0; }
                    let persistence = (self.regs[0x8c] >> 4) as u64;
                    if persistence == 0 || self.outside_cycles >= persistence { self.pint = true; }
                }
            }
        }
    }
    fn fields(&self) {
        assert_eq!(self.regs[0x8e], 0x47, "CONFIG: pulse length/count");
        assert_eq!(self.regs[0x8f] & 0xcc, 0x04, "CONFIG: gain/LED drive");
        assert_eq!(self.regs[0x90] & 0x30, 0x10, "CONFIG: LED boost");
        assert_eq!(self.regs[0x9f] & 0x2f, 0, "CONFIG: photodiodes/compensation");
        assert_eq!(self.regs[0x9d] & 0x7f, 0, "CONFIG: UR offset");
        assert_eq!(self.regs[0x9e] & 0x7f, 0, "CONFIG: DL offset");
    }
    fn read(&mut self, reg: u8, now: u64) -> u8 {
        self.tick(now);
        match reg {
            0x92 => { self.identity_reads += 1; self.regs[0x92] },
            0x93 => (if self.valid { 2 } else { 0 }) | (if self.pint { 0x20 } else { 0 }),
            0x9c => {
                self.reads += 1;
                self.last_read = Some(Reading { raw: self.sample, initialized: self.initialized });
                self.valid = false; self.sample
            },
            _ => self.regs[reg as usize],
        }
    }
    fn write(&mut self, reg: u8, value: u8, now: u64) {
        assert!(!matches!(reg, 0x92..=0x9c | 0xae..=0xaf | 0xfc..=0xff), "PROTOCOL: read-only write");
        let old = self.regs[reg as usize];
        let mask = match reg { 0x8e => 0xff, 0x8f => 0xcc, 0x90 => 0x30,
            0x9d | 0x9e => 0x7f, 0x9f => 0x2f, _ => 0 };
        assert!(self.regs[0x80] & 4 == 0 || (old ^ value) & mask == 0,
            "PROTOCOL: change active field with PEN enabled");
        match reg {
            0x80 => assert_eq!(value & 0x80, 0, "PROTOCOL: ENABLE reserved bit"),
            0x8d => assert_eq!(value & 0xfd, 0x60, "PROTOCOL: CONFIG1 write reserved bits (p23 table)"),
            0x8f => assert_eq!(value & 0x30, 0, "PROTOCOL: CONTROL reserved bits"),
            0x90 => assert_eq!(value & 0x0f, 1, "PROTOCOL: CONFIG2 reserved bits"),
            0x9f => assert_eq!(value & 0xc0, 0, "PROTOCOL: CONFIG3 reserved bits"),
            _ => (),
        }
        self.regs[reg as usize] = value;
        if reg == 0x80 {
            // Extra engines are outside this bounded model, not proven device bugs.
            if value & 0x4a != 0 { panic!("MODEL_UNSUPPORTED: ALS/gesture/wait engine scheduling"); }
            if value & 1 == 0 { self.needs_exit = true; }
            if old & 5 != 5 && value & 5 == 5 {
                self.fields();
                let delay = if self.needs_exit { EXIT } else { 0 };
                self.needs_exit = false;
                self.next = Some(now + delay + PERIOD); // PEN + PON, not PON alone
                self.valid = false;
            } else if value & 5 != 5 { self.next = None; self.valid = false; }
        }
    }
}
impl DeviceModel for Model {
    fn exchange(&mut self, now: u64, tx: &[u8], len: usize, buffer: &mut [u8]) {
        // Device already powered/reset before driver creation. No hidden t=0 POR requirement.
        self.tick(now);
        if let Some(reg) = tx.first() {
            if matches!(*reg, 0xe5..=0xe7) {
                assert!(len == 0, "PROTOCOL: read from clear-command register");
                if tx.len() != 1 { panic!("MODEL_UNSUPPORTED: non-address-only clear command"); }
                if *reg != 0xe6 { self.pint = false; self.outside_cycles = 0; }
                return;
            }
            self.pointer = *reg;
            for value in &tx[1..] { self.write(self.pointer, *value, now); self.pointer = self.pointer.wrapping_add(1); }
        }
        for byte in &mut buffer[..len] { *byte = self.read(self.pointer, now); self.pointer = self.pointer.wrapping_add(1); }
    }
    fn fault_matches(&self, at: FaultAt, tx: &[u8], len: usize) -> bool {
        let p = tx.first().copied().unwrap_or(self.pointer) as usize;
        match at { FaultAt::Any => true, FaultAt::Data => len > 0 && p <= 0x9c && p + len > 0x9c,
            FaultAt::Configuration => tx.len() > 1 && p <= 0x9f }
    }
}
struct Collector {
    model: &'static RefCell<Model>, values: RefCell<Vec<u8>>, outstanding: Cell<usize>,
    driver: Cell<Option<&'static dyn ProximityDriver<'static>>>, reenter: Cell<bool>,
}
impl ProximityClient for Collector {
    fn callback(&self, value: u8) {
        assert!(self.outstanding.get() > 0, "CALLBACK: unsolicited/duplicate callback");
        let reading = self.model.borrow().last_read.expect("DATA: callback without any PDATA read");
        assert!(reading.initialized, "DATA: callback before any valid conversion");
        assert_eq!(value, reading.raw, "NUMERIC: callback differs from most recent PDATA read");
        self.outstanding.set(self.outstanding.get() - 1);
        self.values.borrow_mut().push(value);
        if self.reenter.replace(false) {
            self.outstanding.set(self.outstanding.get() + 1);
            assert!(self.driver.get().unwrap().read_proximity().is_ok(), "CALLBACK: real consumer reentry rejected");
        }
    }
}
struct Rig { clock: &'static Cell<u64>, model: &'static RefCell<Model>, bus: &'static Bus,
    alarm: &'static FakeAlarm, driver: &'static Driver, client: &'static Collector }
impl Rig {
    fn new(v: &Vector) -> Self {
        let clock = Box::leak(Box::new(Cell::new(100_000)));
        let model = Box::leak(Box::new(RefCell::new(Model::new(v))));
        let bus = Box::leak(Box::new(Bus { clock, model, pending: RefCell::new(None), client: Cell::new(None),
            fault: Cell::new(None), fault_hit: Cell::new(false), enabled: Cell::new(false) }));
        let alarm = Box::leak(Box::new(FakeAlarm { clock, due: Cell::new(None), client: Cell::new(None) }));
        let driver = Box::leak(Box::new(apds9960::Apds9960::new(bus, alarm, Box::leak(vec![0;3].into_boxed_slice()))));
        let client = Box::leak(Box::new(Collector { model, values: RefCell::new(vec![]), outstanding: Cell::new(0),
            driver: Cell::new(Some(driver)), reenter: Cell::new(false) }));
        bus.client.set(Some(driver)); alarm.set_alarm_client(driver); ProximityDriver::set_client(driver, client);
        Self { clock, model, bus, alarm, driver, client }
    }
    fn advance(&self, end: u64, stop_on_completion: bool) {
        executor::drive(self.clock, end, executor::EVENT_LIMIT, &[
            executor::Event { name: "bus", next: &|| self.bus.pending.borrow().as_ref().map(|p| p.due), fire: &|| self.bus.complete() },
            executor::Event { name: "alarm", next: &|| self.alarm.due.get(), fire: &|| self.alarm.fire() },
        ], || stop_on_completion && self.client.outstanding.get() == 0, |now| self.model.borrow_mut().tick(now));
    }
    fn init(&self) {
        let _ = self.driver.startup(); // follows the actual component; no imposed return type
        self.advance(self.clock.get() + WINDOW, false);
        if self.model.borrow().regs[0x80] & 5 != 5 { panic!("NOT_COMPLETED: selected continuous engine not initialized"); }
        self.model.borrow().fields();
    }
    fn request(&self) -> Result<(), ErrorCode> {
        let before = self.client.values.borrow().len();
        self.client.outstanding.set(self.client.outstanding.get() + 1);
        let result = self.driver.read_proximity();
        if result.is_err() {
            assert_eq!(self.client.values.borrow().len(), before, "CALLBACK: rejected request produced a value");
            self.client.outstanding.set(self.client.outstanding.get() - 1);
        }
        result
    }
    fn complete(&self) {
        self.advance(self.clock.get() + WINDOW, true);
        if self.client.outstanding.get() != 0 { panic!("NOT_COMPLETED: accepted request has no result in observation window"); }
        self.advance(self.clock.get() + 5000, false); // observe duplicate delivery, allow background work
    }
    fn sample(&self) {
        executor::accept_request(self.clock, self.clock.get()+WINDOW, || self.request(), |until| self.advance(until, false), || self.client.values.borrow().len());
        self.complete();
    }
    fn observe_fault(&self, at: FaultAt, asynchronous: bool) {
        self.bus.fault_hit.set(false); self.bus.fault.set(Some(Fault { at, asynchronous }));
        let _ = self.request();
        self.advance(self.clock.get() + WINDOW, false);
        if !self.bus.fault_hit.get() { panic!("NOT_EXERCISED: requested fault site not reached"); }
        // Any delivered result already passed the independent raw-read observer.
        // Failure completion/retry policy is diagnostic; no forced scalar callback or idle state.
    }
}
fn run(id: &str, scope: &str, f: impl FnOnce()) {
    let selected = std::env::args().nth(1);
    if selected.as_deref() != Some(id) { return; }
    let result = catch_unwind(AssertUnwindSafe(f));
    let (status, message) = match result {
        Ok(()) => ("pass", String::new()),
        Err(e) => {
            let s = e.downcast_ref::<String>().cloned().or_else(|| e.downcast_ref::<&str>().map(|s|s.to_string())).unwrap_or_else(||"candidate panic".into());
            let status = if s.starts_with("NOT_COMPLETED:") { "not_completed" }
                else if s.starts_with("MODEL_UNSUPPORTED:") { "model_unsupported" }
                else if s.starts_with("NOT_EXERCISED:") { "not_exercised" }
                else { "fail" };
            (status, s)
        }
    };
    println!("CASE\t{}\t{}\t{}\t{}", id, scope, status, message.replace('\t'," ").replace('\n'," "));
}
fn main() {
    std::panic::set_hook(Box::new(|_|{}));
    run("__model/busy_returns_buffer", "internal", || {
        use kernel::hil::i2c::I2CDevice;
        let clock = Box::leak(Box::new(Cell::new(100_000)));
        let model = Box::leak(Box::new(RefCell::new(Model::new(&VECTORS[1]))));
        let bus = Bus { clock, model, pending: RefCell::new(None), client: Cell::new(None),
            fault: Cell::new(None), fault_hit: Cell::new(false), enabled: Cell::new(false) };
        bus.enable();
        let first=Box::leak(vec![0x92u8,0,0].into_boxed_slice());
        assert!(bus.write_read(first,1,1).is_ok());
        let second=Box::leak(vec![0x93u8,0,0].into_boxed_slice());
        let pointer=second.as_ptr();
        let (error,returned)=bus.write_read(second,1,1).unwrap_err();
        assert_eq!(error,kernel::hil::i2c::Error::Busy);
        assert_eq!(returned.as_ptr(),pointer);assert!(bus.pending.borrow().is_some());
    });
    run("__model/engine_exit_and_validity", "internal", || {
        let mut m=Model::new(&VECTORS[8]);
        m.write(0x8e,0x47,0); m.write(0x8f,4,0); m.write(0x90,0x11,0);
        m.write(0x80,1,100); m.tick(20_000);
        assert!(!m.valid && m.next.is_none(), "PON alone must not start a conversion");
        m.write(0x80,5,20_000); m.tick(20_000+EXIT+PERIOD-1);
        assert!(!m.valid, "PEN requires its own first engine exit");
        m.tick(20_000+EXIT+PERIOD); assert!(m.valid);
        assert_eq!(m.read(0x9c,20_000+EXIT+PERIOD),170); assert!(!m.valid);
        m.tick(20_000+EXIT+2*PERIOD); assert!(m.valid, "subsequent conversion may reassert PVALID");
    });
    run("__model/threshold_and_pointer", "internal", || {
        let mut m=Model::new(&VECTORS[1]);
        m.write(0x8e,0x47,0);m.write(0x8f,4,0);m.write(0x90,0x11,0);
        m.write(0x89,0,0);m.write(0x8b,1,0);m.write(0x8c,0x10,0);
        m.write(0x80,5,0);m.tick(EXIT+PERIOD);
        assert!(m.valid && !m.pint, "in-window sample is still valid");
        let mut b=[0u8;3];m.exchange(EXIT+PERIOD,&[0x9c],0,&mut b);
        m.exchange(EXIT+PERIOD,&[],1,&mut b);assert_eq!(b[0],1);assert!(!m.valid);
        m.pint=true;assert_eq!(m.read(0x93,EXIT+PERIOD)&0x22,0x20,"PINT may outlive PVALID");
    });
    for v in VECTORS {
        run(&format!("numeric/{}/proximity",v.id), "core", || {
            let r=Rig::new(v); r.init(); r.sample();
            assert_eq!(r.client.values.borrow()[0], v.expected, "NUMERIC: fixed physical input");
        });
    }
    run("requests/repeated_input_changes", "core", || {
        let r=Rig::new(&VECTORS[0]); r.init();
        for v in VECTORS {
            r.model.borrow_mut().input=v.raw;
            let end=r.clock.get()+WINDOW; let mut seen=false;
            while r.clock.get() < end {
                r.advance((r.clock.get()+20_000).min(end),false);
                r.sample();
                if r.client.values.borrow().last()==Some(&v.expected) { seen=true; break; }
            }
            if !seen { panic!("NOT_COMPLETED: no response to stable input change in observation window"); }
        }
    });
    run("requests/reentrant_callback", "core", || {
        let r=Rig::new(&VECTORS[2]); r.init(); r.client.reenter.set(true); r.sample();
        assert_eq!(r.client.values.borrow().len(),2,"CALLBACK: missing reentrant delivery");
    });
    run("requests/first_available", "core", || {
        let r=Rig::new(&VECTORS[2]); let _=r.driver.startup();
        executor::accept_request(r.clock, r.clock.get()+WINDOW, || r.request(),
            |until| r.advance(until,false), || r.client.values.borrow().len());
        r.complete();
    });
    run("requests/concurrent", "core", || {
        let r=Rig::new(&VECTORS[2]); r.init();
        executor::accept_request(r.clock, r.clock.get()+WINDOW, || r.request(), |until| r.advance(until, false), || r.client.values.borrow().len());
        let _=r.request(); r.complete(); // reject or queue; all accepted requests must complete
    });
    run("protocol/unread_ready", "core", || {
        let r=Rig::new(&VECTORS[1]); r.init(); r.advance(r.clock.get()+20_000,false); r.sample();
    });
    run("protocol/conversion_boundary", "core", || {
        let r=Rig::new(&VECTORS[1]); r.init();
        let due=r.model.borrow().next.expect("NOT_COMPLETED: no continuous conversion");
        r.advance(due.saturating_sub(50).max(r.clock.get()),false); r.sample();
    });
    for stale in [false,true] {
        run(if stale {"safety/pint_without_new_data"} else {"safety/no_valid_data"},"core",|| {
            let r=Rig::new(&VECTORS[8]);
            if stale {
                // A latched PINT has a real prior conversion. Do not fabricate
                // an impossible cold-reset PINT with no initialized PDATA.
                r.init(); r.sample();
                r.model.borrow_mut().valid=false;
                r.model.borrow_mut().pint=true;
                r.model.borrow_mut().stalled=true;
            } else {
                r.model.borrow_mut().stalled=true; r.init();
            }
            let _=r.request(); r.advance(r.clock.get()+WINDOW,false);
        });
    }
    for at in [FaultAt::Any,FaultAt::Data] { for asynchronous in [false,true] {
        run(&format!("fault/{:?}/{}",at,if asynchronous {"async"} else {"immediate"}),"core",|| {
            let r=Rig::new(&VECTORS[8]); r.init(); r.observe_fault(at,asynchronous);
        });
    }}
    run("diagnostic/startup_repeat", "diagnostic", || { let r=Rig::new(&VECTORS[1]); r.init(); let _=r.driver.startup(); r.advance(r.clock.get()+WINDOW,false); r.sample(); });
    run("diagnostic/interrupt_request", "diagnostic", || { let r=Rig::new(&VECTORS[1]); r.init(); let result=r.driver.read_proximity_on_interrupt(0,1); println!("OBS\tinterrupt_result\t{:?}",result); r.advance(r.clock.get()+WINDOW,false); });
    for id in [0u8,0xff,0x9c] {
        run(&format!("diagnostic/identity_{:02x}",id),"diagnostic",|| {
            let r=Rig::new(&VECTORS[1]); r.model.borrow_mut().regs[0x92]=id;
            let _=r.driver.startup(); r.advance(r.clock.get()+WINDOW,false);
            println!("OBS\tidentity\tid={} reads={} enabled={}",id,r.model.borrow().identity_reads,r.model.borrow().regs[0x80]);
        });
    }
    for at in [FaultAt::Any,FaultAt::Configuration] { for asynchronous in [false,true] {
        run(&format!("diagnostic/initialization/{:?}/{}",at,if asynchronous {"async"} else {"immediate"}),"diagnostic",|| {
            let r=Rig::new(&VECTORS[8]); r.bus.fault.set(Some(Fault{at,asynchronous}));
            let _=r.driver.startup(); r.advance(r.clock.get()+WINDOW,false);
            println!("OBS\tinitialization_fault\thit={} enabled={} outstanding={}",r.bus.fault_hit.get(),r.model.borrow().regs[0x80],r.client.outstanding.get());
        });
    }}
}
