//! Method-owned finite native-interface observer, using public Tock HILs.
//! No device commands, timing constants, formulas or scoring fixtures live here.
use kernel::hil::{i2c, time};
use kernel::hil::time::Ticks;
use kernel::ErrorCode;
use std::cell::{Cell, RefCell};
use std::collections::HashMap;
use std::rc::Rc;

thread_local! { static RATE: Cell<u32> = Cell::new(32768); }
pub struct Frequency;
impl time::Frequency for Frequency {
    fn frequency() -> u32 { RATE.with(|rate| rate.get()) }
}

struct Pending {
    id: usize,
    buffer: &'static mut [u8],
    read_len: usize,
    due: u64,
}

pub struct State {
    pub tick: u64,
    pub events: Vec<String>,
    pending: Option<Pending>,
    alarm: Option<(usize, u64, u32)>,
    buffers: HashMap<usize, usize>,
    io_serial: usize,
    alarm_serial: usize,
    pub reject_next: bool,
    pub io_latency_ticks: u64,
}

impl State {
    pub fn new(frequency: u32) -> Self {
        assert!(frequency > 0);
        RATE.with(|rate| rate.set(frequency));
        Self { tick: 0, events: Vec::new(), pending: None, alarm: None,
            buffers: HashMap::new(), io_serial: 0, alarm_serial: 0,
            reject_next: false, io_latency_ticks: 1 }
    }
    pub fn record(&mut self, kind: &str, fields: String) {
        self.events.push(format!("{{\"phase\":\"{}\",\"tick\":{},\"frequency\":{},{} }}",
            kind, self.tick, RATE.with(|rate| rate.get()), fields));
    }
    pub fn buffer_id(&mut self, data: &[u8]) -> usize {
        let pointer = data.as_ptr() as usize;
        let next = self.buffers.len();
        *self.buffers.entry(pointer).or_insert(next)
    }
    pub fn alarm_deadline(&self) -> Option<u64> { self.alarm.as_ref().map(|a| a.1) }
    pub fn next(&self) -> Option<&'static str> {
        match (&self.pending, &self.alarm) {
            (Some(io), Some((_, due, _))) => Some(if io.due <= *due { "io" } else { "alarm" }),
            (Some(_), None) => Some("io"),
            (None, Some(_)) => Some("alarm"),
            (None, None) => None,
        }
    }
    pub fn record_output(&mut self, channel: &str, value: Option<i128>, failed: bool) {
        let value = value.map(|x| x.to_string()).unwrap_or("null".into());
        self.record("output", format!("\"channel\":\"{}\",\"value\":{},\"failed\":{}", channel, value, failed));
    }
}

pub struct Bus { pub state: Rc<RefCell<State>> }
impl Bus {
    fn submit(&self, data: &'static mut [u8], write_len: usize, read_len: usize)
        -> Result<(), (i2c::Error, &'static mut [u8])> {
        let mut state = self.state.borrow_mut();
        state.io_serial += 1;
        let id = state.io_serial;
        let buffer = state.buffer_id(data);
        state.record("submit", format!("\"operation\":{},\"buffer\":{},\"write\":{:?},\"write_len\":{},\"read_len\":{}",
            id, buffer, &data[..write_len.min(data.len())], write_len, read_len));
        let error = if write_len > data.len() || read_len > data.len() { Some(i2c::Error::Overrun) }
            else if state.pending.is_some() || state.reject_next { Some(i2c::Error::Busy) } else { None };
        state.reject_next = false;
        if let Some(error) = error {
            state.record("rejected", format!("\"operation\":{},\"buffer\":{},\"returned_buffer\":{},\"error\":\"{:?}\"", id, buffer, buffer, error));
            return Err((error, data));
        }
        state.record("accepted", format!("\"operation\":{},\"buffer\":{}", id, buffer));
        state.record("physical_start", format!("\"operation\":{}", id));
        let due = state.tick + state.io_latency_ticks;
        state.pending = Some(Pending { id, buffer: data, read_len, due });
        Ok(())
    }
    pub fn complete(&self, client: &dyn i2c::I2CClient, payload: &[u8], failed: bool) {
        let mut pending = self.state.borrow_mut().pending.take().expect("no accepted I2C operation");
        if !failed {
            assert_eq!(payload.len(), pending.read_len, "stimulus must exactly fill the submitted read");
            pending.buffer[..pending.read_len].copy_from_slice(payload);
        }
        {
            let mut state = self.state.borrow_mut();
            state.tick = state.tick.max(pending.due);
            let buffer = state.buffer_id(pending.buffer);
            state.record(if failed { "physical_failure" } else { "physical_success" }, format!("\"operation\":{}", pending.id));
            state.record(if failed { "callback_failure" } else { "callback_success" },
                format!("\"operation\":{},\"buffer\":{},\"returned_buffer\":{},\"data\":{:?}",
                        pending.id, buffer, buffer, &pending.buffer[..pending.read_len]));
        }
        client.command_complete(pending.buffer, if failed { Err(i2c::Error::DataNak) } else { Ok(()) });
    }
    pub fn read_length(&self) -> Option<usize> { self.state.borrow().pending.as_ref().map(|p| p.read_len) }
}
impl i2c::I2CDevice for Bus {
    fn enable(&self) { self.state.borrow_mut().record("enable", "\"resource\":\"i2c\"".into()); }
    fn disable(&self) { self.state.borrow_mut().record("disable", "\"resource\":\"i2c\"".into()); }
    fn write(&self, data: &'static mut [u8], len: usize) -> Result<(), (i2c::Error, &'static mut [u8])> { self.submit(data, len, 0) }
    fn read(&self, data: &'static mut [u8], len: usize) -> Result<(), (i2c::Error, &'static mut [u8])> { self.submit(data, 0, len) }
    fn write_read(&self, data: &'static mut [u8], write_len: usize, read_len: usize) -> Result<(), (i2c::Error, &'static mut [u8])> { self.submit(data, write_len, read_len) }
}

pub struct Alarm<'a> { pub state: Rc<RefCell<State>>, client: Cell<Option<&'a dyn time::AlarmClient>>, last: Cell<u32> }
impl<'a> Alarm<'a> {
    pub fn new(state: Rc<RefCell<State>>) -> Self { Self { state, client: Cell::new(None), last: Cell::new(0) } }
    pub fn fire(&self) {
        let (id, due, _) = self.state.borrow_mut().alarm.take().expect("no armed alarm");
        { let mut state = self.state.borrow_mut();state.tick = state.tick.max(due);state.record("alarm_callback", format!("\"operation\":{}", id)); }
        self.client.get().expect("alarm client not registered").alarm();
    }
}
impl<'a> time::Time for Alarm<'a> {
    type Frequency = Frequency;
    type Ticks = time::Ticks32;
    fn now(&self) -> Self::Ticks { time::Ticks32::from(self.state.borrow().tick as u32) }
}
impl<'a> time::Alarm<'a> for Alarm<'a> {
    fn set_alarm_client(&self, client: &'a dyn time::AlarmClient) { self.client.set(Some(client)); }
    fn set_alarm(&self, reference: time::Ticks32, dt: time::Ticks32) {
        let mut state = self.state.borrow_mut();state.alarm_serial += 1;let id = state.alarm_serial;
        let dt = time::Ticks32::from(dt.into_u32().max(2));
        let deadline = reference.wrapping_add(dt);
        let now = time::Ticks32::from(state.tick as u32);
        let due = state.tick + if now.within_range(reference, deadline) { deadline.wrapping_sub(now).into_u32() as u64 } else { 0 };
        self.last.set(deadline.into_u32());
        state.record("alarm_arm", format!("\"operation\":{},\"reference\":{},\"dt\":{},\"deadline\":{}", id, reference.into_u32(), dt.into_u32(), due));
        state.alarm = Some((id, due, deadline.into_u32()));
    }
    fn get_alarm(&self) -> time::Ticks32 { time::Ticks32::from(self.last.get()) }
    fn disarm(&self) -> Result<(), ErrorCode> { self.state.borrow_mut().alarm = None;Ok(()) }
    fn is_armed(&self) -> bool { self.state.borrow().alarm.is_some() }
    fn minimum_dt(&self) -> time::Ticks32 { time::Ticks32::from(2) }
}
