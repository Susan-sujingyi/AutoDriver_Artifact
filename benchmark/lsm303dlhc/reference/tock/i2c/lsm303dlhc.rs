// Licensed under the Apache License, Version 2.0 or the MIT License.
// Host-only reference control; not a source of scoring truth or agent input.
use core::cell::Cell;
use kernel::hil::i2c::{self, I2CClient, I2CDevice};
use kernel::hil::sensors::{NineDof, NineDofClient};
use kernel::hil::time::{Alarm, AlarmClient, Frequency, Ticks};
use kernel::utilities::cells::TakeCell;
use kernel::ErrorCode;

pub const BUFFER_SIZE: usize = 8;
const SETUP: [(u8, u8); 3] = [(0x00, 0x10), (0x01, 0x20), (0x02, 0x00)];
#[derive(Clone, Copy, PartialEq)]
enum State { Off, Configure, Ready, Status, Waiting, Data }
pub struct Lsm303dlhc<'a, I: I2CDevice, A: Alarm<'a>> {
    i2c: &'a I,
    alarm: &'a A,
    buffer: TakeCell<'static, [u8]>,
    state: Cell<State>,
    step: Cell<usize>,
    polls: Cell<u32>,
    client: Cell<Option<&'a dyn NineDofClient>>,
}
impl<'a, I: I2CDevice, A: Alarm<'a>> Lsm303dlhc<'a, I, A> {
    pub fn new(i2c: &'a I, alarm: &'a A, buffer: &'static mut [u8]) -> Self {
        Self { i2c, alarm, buffer: TakeCell::new(buffer), state: Cell::new(State::Off),
            step: Cell::new(0), polls: Cell::new(0), client: Cell::new(None) }
    }
    fn finish(&self) {
        let _ = self.alarm.disarm();
        self.i2c.disable();
        self.state.set(if self.state.get() == State::Configure { State::Off } else { State::Ready });
    }
    fn submit(&self, b: &'static mut [u8], w: usize, r: usize) -> Result<(), ErrorCode> {
        self.i2c.enable();
        let result = if r == 0 { self.i2c.write(b, w) } else { self.i2c.write_read(b, w, r) };
        match result {
            Ok(()) => Ok(()),
            Err((e, b)) => { self.buffer.replace(b); self.finish(); Err(e.into()) }
        }
    }
    fn configure(&self, b: &'static mut [u8]) -> Result<(), ErrorCode> {
        let step = self.step.get();
        if step == SETUP.len() {
            self.buffer.replace(b); self.state.set(State::Ready); self.i2c.disable(); return Ok(());
        }
        b[0] = SETUP[step].0; b[1] = SETUP[step].1;
        self.step.set(step + 1); self.state.set(State::Configure); self.submit(b, 2, 0)
    }
    pub fn startup(&self) -> Result<(), ErrorCode> {
        match self.state.get() { State::Ready => return Ok(()), State::Off => (), _ => return Err(ErrorCode::BUSY) }
        let b = self.buffer.take().ok_or(ErrorCode::BUSY)?;
        if b.len() < BUFFER_SIZE { self.buffer.replace(b); return Err(ErrorCode::SIZE); }
        self.step.set(0); self.configure(b)
    }
    fn status(&self, b: &'static mut [u8]) -> Result<(), ErrorCode> {
        b[0] = 0x09; self.state.set(State::Status); self.submit(b, 1, 1)
    }
}
impl<'a, I: I2CDevice, A: Alarm<'a>> I2CClient for Lsm303dlhc<'a, I, A> {
    fn command_complete(&self, b: &'static mut [u8], result: Result<(), i2c::Error>) {
        if result.is_err() { self.buffer.replace(b); self.finish(); return; }
        match self.state.get() {
            State::Configure => { let _ = self.configure(b); }
            State::Status => {
                if b[0] & 1 != 0 {
                    b[0] = 0x03; self.state.set(State::Data); let _ = self.submit(b, 1, 6);
                } else if self.polls.get() >= 100 {
                    self.buffer.replace(b); self.finish();
                } else {
                    self.polls.set(self.polls.get() + 1); self.buffer.replace(b);
                    self.state.set(State::Waiting); self.i2c.disable();
                    let ticks = (A::Frequency::frequency() as u64 * 5_000 + 999_999) / 1_000_000;
                    self.alarm.set_alarm(self.alarm.now(), A::Ticks::from_or_max(ticks).max(self.alarm.minimum_dt()));
                }
            }
            State::Data => {
                let x = i16::from_be_bytes([b[0], b[1]]) as i64;
                let z = i16::from_be_bytes([b[2], b[3]]) as i64;
                let y = i16::from_be_bytes([b[4], b[5]]) as i64;
                // Datasheet Table 75: XY 1100 and Z 980 LSB/gauss; 1 gauss = 100000 nT.
                let values = [x * 100_000 / 1100, y * 100_000 / 1100, z * 100_000 / 980];
                self.buffer.replace(b); self.finish();
                if let Some(client) = self.client.get() {
                    client.callback(values[0] as i32 as u32 as usize,
                                    values[1] as i32 as u32 as usize,
                                    values[2] as i32 as u32 as usize);
                }
            }
            _ => { self.buffer.replace(b); self.finish(); }
        }
    }
}
impl<'a, I: I2CDevice, A: Alarm<'a>> AlarmClient for Lsm303dlhc<'a, I, A> {
    fn alarm(&self) {
        if self.state.get() == State::Waiting {
            if let Some(b) = self.buffer.take() { let _ = self.status(b); }
        }
    }
}
impl<'a, I: I2CDevice, A: Alarm<'a>> NineDof<'a> for Lsm303dlhc<'a, I, A> {
    fn set_client(&self, client: &'a dyn NineDofClient) { self.client.set(Some(client)); }
    fn read_magnetometer(&self) -> Result<(), ErrorCode> {
        match self.state.get() { State::Ready => (), State::Off => return Err(ErrorCode::OFF), _ => return Err(ErrorCode::BUSY) }
        let b = self.buffer.take().ok_or(ErrorCode::BUSY)?;
        self.polls.set(0); self.status(b)
    }
}
