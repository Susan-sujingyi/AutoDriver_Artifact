// SPDX-License-Identifier: Apache-2.0
//! Evaluator-only native CCS811 reference; never candidate-visible material.
use core::cell::Cell;
use kernel::hil::i2c::{self, I2CClient, I2CDevice};
use kernel::hil::sensors::{AirQualityClient, AirQualityDriver};
use kernel::hil::time::{Alarm, AlarmClient, Frequency, Ticks};
use kernel::utilities::cells::TakeCell;
use kernel::ErrorCode;
pub const BUFFER_SIZE: usize = 8;
const POLL_US: u32 = 100_000;
const MAX_POLLS: u32 = 20;
#[derive(Clone, Copy, PartialEq)]
enum State { Off, Por, Reset, ResetWait, BootStatus, Start, StartWait, AppStatus,
    Identity, Version, Mode, InitStatus, Ready, Poll, Wait, Sample, Error }
pub struct Ccs811<'a, I: I2CDevice, A: Alarm<'a>> {
    i2c: &'a I, alarm: &'a A, buffer: TakeCell<'static, [u8]>, state: Cell<State>,
    initialized: Cell<bool>, channel: Cell<Option<bool>>, polls: Cell<u32>,
    client: Cell<Option<&'a dyn AirQualityClient>>,
}
impl<'a, I: I2CDevice, A: Alarm<'a>> Ccs811<'a, I, A> {
    pub fn new(i2c: &'a I, alarm: &'a A, buffer: &'static mut [u8]) -> Self {
        Self { i2c, alarm, buffer: TakeCell::new(buffer), state: Cell::new(State::Off),
            initialized: Cell::new(false), channel: Cell::new(None), polls: Cell::new(0),
            client: Cell::new(None) }
    }
    pub fn startup(&self) -> Result<(), ErrorCode> {
        match self.state.get() { State::Ready => return Ok(()), State::Off => (),
            _ => return Err(ErrorCode::BUSY) }
        let buffer = self.buffer.take().ok_or(ErrorCode::BUSY)?;
        if buffer.len() < BUFFER_SIZE { self.buffer.replace(buffer); return Err(ErrorCode::SIZE); }
        self.wait(buffer, State::Por, 20_000);
        Ok(())
    }
    fn idle(&self) {
        let _ = self.alarm.disarm();
        self.i2c.disable();
        self.state.set(if self.initialized.get() { State::Ready } else { State::Off });
    }
    fn finish(&self, value: Result<u32, ErrorCode>) {
        let channel = self.channel.take();
        self.idle();
        if let (Some(tvoc), Some(client)) = (channel, self.client.get()) {
            if tvoc { client.tvoc_data_available(value); } else { client.co2_data_available(value); }
        }
    }
    fn read(&self, buffer: &'static mut [u8], reg: u8, len: usize, next: State) -> Result<(), ErrorCode> {
        buffer[0] = reg;
        self.state.set(next);
        self.i2c.enable();
        match self.i2c.write_read(buffer, 1, len) {
            Ok(()) => Ok(()),
            Err((error, buffer)) => { self.buffer.replace(buffer); Err(error.into()) }
        }
    }
    fn write(&self, buffer: &'static mut [u8], bytes: &[u8], next: State) -> Result<(), ErrorCode> {
        buffer[..bytes.len()].copy_from_slice(bytes);
        self.state.set(next);
        self.i2c.enable();
        match self.i2c.write(buffer, bytes.len()) {
            Ok(()) => Ok(()),
            Err((error, buffer)) => { self.buffer.replace(buffer); Err(error.into()) }
        }
    }
    fn wait(&self, buffer: &'static mut [u8], next: State, us: u32) {
        self.buffer.replace(buffer);
        self.i2c.disable();
        self.state.set(next);
        let ticks = (A::Frequency::frequency() as u64 * us as u64 + 999_999) / 1_000_000;
        self.alarm.set_alarm(self.alarm.now(), A::Ticks::from_or_max(ticks).max(self.alarm.minimum_dt()));
    }
    fn request(&self, tvoc: bool) -> Result<(), ErrorCode> {
        if self.state.get() != State::Ready {
            return Err(if self.state.get() == State::Off { ErrorCode::OFF } else { ErrorCode::BUSY });
        }
        let buffer = self.buffer.take().ok_or(ErrorCode::BUSY)?;
        self.channel.set(Some(tvoc));
        self.polls.set(0);
        let result = self.read(buffer, 0x00, 1, State::Poll);
        if result.is_err() { self.channel.set(None); self.idle(); }
        result
    }
    fn poll_status(&self, buffer: &'static mut [u8], status: u8) -> Result<(), ErrorCode> {
        if status & 1 != 0 { return self.read(buffer, 0xe0, 1, State::Error); }
        if status & 0x80 == 0 { self.buffer.replace(buffer); self.finish(Err(ErrorCode::OFF)); return Ok(()); }
        if status & 8 != 0 {
            let len = if self.channel.get() == Some(true) { 4 } else { 2 };
            return self.read(buffer, 0x02, len, State::Sample);
        }
        if self.polls.get() >= MAX_POLLS {
            self.buffer.replace(buffer); self.finish(Err(ErrorCode::FAIL));
        } else {
            self.polls.set(self.polls.get() + 1);
            self.wait(buffer, State::Wait, POLL_US);
        }
        Ok(())
    }
    fn sample(&self, buffer: &'static mut [u8]) {
        let tvoc = self.channel.get() == Some(true);
        let offset = if tvoc { 2 } else { 0 };
        let value = 256 * buffer[offset] as u32 + buffer[offset + 1] as u32;
        let valid = if tvoc { value <= 32768 } else { (400..=29206).contains(&value) };
        self.buffer.replace(buffer);
        self.finish(if valid { Ok(value) } else { Err(ErrorCode::INVAL) });
    }
}
impl<'a, I: I2CDevice, A: Alarm<'a>> I2CClient for Ccs811<'a, I, A> {
    fn command_complete(&self, buffer: &'static mut [u8], status: Result<(), i2c::Error>) {
        if let Err(error) = status { self.buffer.replace(buffer); self.finish(Err(error.into())); return; }
        let result = match self.state.get() {
            State::Reset => { self.wait(buffer, State::ResetWait, 2_000); return; }
            State::BootStatus => {
                let status = buffer[0];
                if status & 0x10 == 0 { self.buffer.replace(buffer); self.finish(Err(ErrorCode::NODEVICE)); return; }
                if status & 0x80 != 0 { self.read(buffer, 0x20, 1, State::Identity) }
                else { self.write(buffer, &[0xf4], State::Start) }
            }
            State::Start => { self.wait(buffer, State::StartWait, 1_000); return; }
            State::AppStatus => {
                if buffer[0] & 0x80 == 0 { self.buffer.replace(buffer); self.finish(Err(ErrorCode::OFF)); return; }
                self.read(buffer, 0x20, 1, State::Identity)
            }
            State::Identity => {
                if buffer[0] != 0x81 { self.buffer.replace(buffer); self.finish(Err(ErrorCode::NODEVICE)); return; }
                self.read(buffer, 0x24, 2, State::Version)
            }
            State::Version => self.write(buffer, &[0x01, 0x10], State::Mode),
            State::Mode => self.read(buffer, 0x00, 1, State::InitStatus),
            State::InitStatus => {
                if buffer[0] & 1 != 0 { self.read(buffer, 0xe0, 1, State::Error) }
                else { self.initialized.set(true); self.buffer.replace(buffer); self.idle(); return; }
            }
            State::Poll => { let status = buffer[0]; self.poll_status(buffer, status) }
            State::Sample => { self.sample(buffer); return; }
            State::Error => { self.buffer.replace(buffer); self.finish(Err(ErrorCode::FAIL)); return; }
            _ => { self.buffer.replace(buffer); self.finish(Err(ErrorCode::FAIL)); return; }
        };
        if let Err(error) = result { self.finish(Err(error)); }
    }
}
impl<'a, I: I2CDevice, A: Alarm<'a>> AlarmClient for Ccs811<'a, I, A> {
    fn alarm(&self) {
        let Some(buffer) = self.buffer.take() else { self.finish(Err(ErrorCode::NOMEM)); return; };
        let result = match self.state.get() {
            State::Por => self.write(buffer, &[0xff, 0x11, 0xe5, 0x72, 0x8a], State::Reset),
            State::ResetWait => self.read(buffer, 0x00, 1, State::BootStatus),
            State::StartWait => self.read(buffer, 0x00, 1, State::AppStatus),
            State::Wait => self.read(buffer, 0x00, 1, State::Poll),
            _ => { self.buffer.replace(buffer); self.finish(Err(ErrorCode::FAIL)); return; }
        };
        if let Err(error) = result { self.finish(Err(error)); }
    }
}
impl<'a, I: I2CDevice, A: Alarm<'a>> AirQualityDriver<'a> for Ccs811<'a, I, A> {
    fn set_client(&self, client: &'a dyn AirQualityClient) { self.client.set(Some(client)); }
    fn specify_environment(&self, _temp: Option<i32>, _humidity: Option<u32>) -> Result<(), ErrorCode> { Err(ErrorCode::NOSUPPORT) }
    fn read_co2(&self) -> Result<(), ErrorCode> { self.request(false) }
    fn read_tvoc(&self) -> Result<(), ErrorCode> { self.request(true) }
}
