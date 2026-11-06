// SPDX-License-Identifier: Apache-2.0
//! Evaluator-only BH1750FVI one-time high-resolution reference.
use core::cell::Cell;
use kernel::hil::i2c::{self, I2CClient, I2CDevice};
use kernel::hil::sensors::{AmbientLight, AmbientLightClient};
use kernel::hil::time::{Alarm, AlarmClient, Frequency, Ticks};
use kernel::utilities::cells::TakeCell;
use kernel::ErrorCode;

pub const BUFFER_SIZE: usize = 2;
const WAIT_US: u32 = 180_000;
// Optional documented setup commands, independently varied by legal controls.
const PREPARATION: &[u8] = &[];
#[derive(Clone, Copy, PartialEq)]
enum State { Off, Ready, Preparing, Command, Waiting, Reading }

pub struct Bh1750<'a, I: I2CDevice, A: Alarm<'a>> {
    i2c: &'a I,
    alarm: &'a A,
    buffer: TakeCell<'static, [u8]>,
    state: Cell<State>,
    preparation_index: Cell<usize>,
    client: Cell<Option<&'a dyn AmbientLightClient>>,
}
impl<'a, I: I2CDevice, A: Alarm<'a>> Bh1750<'a, I, A> {
    pub fn new(i2c: &'a I, alarm: &'a A, buffer: &'static mut [u8]) -> Self {
        Self { i2c, alarm, buffer: TakeCell::new(buffer), state: Cell::new(State::Off),
            preparation_index: Cell::new(0), client: Cell::new(None) }
    }
    pub fn startup(&self) -> Result<(), ErrorCode> {
        match self.state.get() {
            State::Ready => return Ok(()),
            State::Off => (),
            _ => return Err(ErrorCode::BUSY),
        }
        let buffer = self.buffer.take().ok_or(ErrorCode::BUSY)?;
        let enough = buffer.len() >= BUFFER_SIZE;
        self.buffer.replace(buffer);
        if !enough { return Err(ErrorCode::SIZE); }
        // The board has already completed VCC/DVI reset; MTreg POR is 69.
        // Power On is optional and no identity/status register exists.
        self.state.set(State::Ready);
        Ok(())
    }
    fn idle(&self) {
        self.i2c.disable();
        self.state.set(State::Ready);
    }
    fn fail(&self) {
        // AmbientLightClient cannot represent an error; zero is valid light.
        self.idle();
    }
    fn next_command(&self, buffer: &'static mut [u8]) -> Result<(), ErrorCode> {
        let index = self.preparation_index.get();
        if index < PREPARATION.len() {
            buffer[0] = PREPARATION[index];
            self.state.set(State::Preparing);
        } else {
            buffer[0] = 0x20;
            self.state.set(State::Command);
        }
        self.i2c.enable();
        match self.i2c.write(buffer, 1) {
            Ok(()) => Ok(()),
            Err((error, buffer)) => { self.buffer.replace(buffer); self.idle(); Err(error.into()) }
        }
    }
    fn request(&self) -> Result<(), ErrorCode> {
        match self.state.get() {
            State::Ready => (),
            State::Off => return Err(ErrorCode::OFF),
            _ => return Err(ErrorCode::BUSY),
        }
        let buffer = self.buffer.take().ok_or(ErrorCode::BUSY)?;
        self.preparation_index.set(0);
        self.next_command(buffer)
    }
}
impl<'a, I: I2CDevice, A: Alarm<'a>> I2CClient for Bh1750<'a, I, A> {
    fn command_complete(&self, buffer: &'static mut [u8], status: Result<(), i2c::Error>) {
        if status.is_err() { self.buffer.replace(buffer); self.fail(); return; }
        match self.state.get() {
            State::Preparing => {
                self.preparation_index.set(self.preparation_index.get() + 1);
                if self.next_command(buffer).is_err() { self.fail(); }
            }
            State::Command => {
                self.buffer.replace(buffer);
                self.i2c.disable();
                self.state.set(State::Waiting);
                let ticks = (A::Frequency::frequency() as u64 * WAIT_US as u64 + 999_999) / 1_000_000;
                self.alarm.set_alarm(self.alarm.now(), A::Ticks::from_or_max(ticks).max(self.alarm.minimum_dt()));
            }
            State::Reading => {
                let raw = u16::from_be_bytes([buffer[0], buffer[1]]) as u32;
                let lux = (raw * 5 / 6) as usize;
                self.buffer.replace(buffer);
                self.idle();
                if let Some(client) = self.client.get() { client.callback(lux); }
            }
            _ => { self.buffer.replace(buffer); self.fail(); }
        }
    }
}
impl<'a, I: I2CDevice, A: Alarm<'a>> AlarmClient for Bh1750<'a, I, A> {
    fn alarm(&self) {
        if self.state.get() != State::Waiting { return; }
        if let Some(buffer) = self.buffer.take() {
            self.state.set(State::Reading);
            self.i2c.enable();
            if let Err((_error, buffer)) = self.i2c.read(buffer, 2) {
                self.buffer.replace(buffer);
                self.fail();
            }
        } else { self.fail(); }
    }
}
impl<'a, I: I2CDevice, A: Alarm<'a>> AmbientLight<'a> for Bh1750<'a, I, A> {
    fn set_client(&self, client: &'a dyn AmbientLightClient) { self.client.set(Some(client)); }
    fn read_light_intensity(&self) -> Result<(), ErrorCode> { self.request() }
}
