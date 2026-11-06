// SPDX-License-Identifier: Apache-2.0
//! Evaluator-only SHT30 high-repeatability single-shot reference.
use core::cell::Cell;
use kernel::hil::i2c::{self, I2CClient, I2CDevice};
use kernel::hil::sensors::{TemperatureDriver, TemperatureClient, HumidityDriver, HumidityClient};
use kernel::hil::time::{Alarm, AlarmClient, Frequency, Ticks};
use kernel::utilities::cells::TakeCell;
use kernel::ErrorCode;
pub const BUFFER_SIZE: usize = 6;
const POR_US: u32 = 2000;
const WAIT_US: u32 = 15000;
const MAX_READ_NAKS: u8 = 60;
const PREPARATION: &[u16] = &[];
const TEMP_ONLY: bool = false;
const CACHE_OTHER: bool = false;
#[derive(Clone, Copy, PartialEq)]
enum State { Off, PowerWait, Ready, Preparing, PrepareWait, Command, Waiting, Reading }
#[derive(Clone, Copy, PartialEq)]
enum Channel { Temperature, Humidity }
impl Channel { fn index(self) -> usize { if self == Self::Temperature { 0 } else { 1 } } }

pub struct Sht3x<'a, I: I2CDevice, A: Alarm<'a>> {
    i2c: &'a I,
    alarm: &'a A,
    buffer: TakeCell<'static, [u8]>,
    state: Cell<State>,
    channel: Cell<Channel>,
    preparation_index: Cell<usize>,
    retries: Cell<u8>,
    cache: Cell<[Option<i64>; 2]>,
    temperature: Cell<Option<&'a dyn TemperatureClient>>,
    humidity: Cell<Option<&'a dyn HumidityClient>>,
}
impl<'a, I: I2CDevice, A: Alarm<'a>> Sht3x<'a, I, A> {
    pub fn new(i2c: &'a I, alarm: &'a A, buffer: &'static mut [u8]) -> Self {
        Self { i2c, alarm, buffer: TakeCell::new(buffer), state: Cell::new(State::Off),
            channel: Cell::new(Channel::Temperature), preparation_index: Cell::new(0),
            retries: Cell::new(0), cache: Cell::new([None; 2]),
            temperature: Cell::new(None), humidity: Cell::new(None) }
    }
    pub fn startup(&self) -> Result<(), ErrorCode> {
        match self.state.get() { State::Ready => return Ok(()), State::Off => (), _ => return Err(ErrorCode::BUSY) }
        let buffer = self.buffer.take().ok_or(ErrorCode::BUSY)?;
        let enough = buffer.len() >= BUFFER_SIZE;
        self.buffer.replace(buffer);
        if !enough { return Err(ErrorCode::SIZE); }
        self.wait(State::PowerWait, POR_US);
        Ok(())
    }
    fn wait(&self, state: State, us: u32) {
        self.i2c.disable(); self.state.set(state);
        let ticks = (A::Frequency::frequency() as u64 * us as u64 + 999999) / 1000000;
        self.alarm.set_alarm(self.alarm.now(), A::Ticks::from_or_max(ticks).max(self.alarm.minimum_dt()));
    }
    fn idle(&self) { self.i2c.disable(); self.state.set(State::Ready); }
    fn emit(&self, channel: Channel, value: i64) {
        match channel {
            Channel::Temperature => if let Some(c) = self.temperature.get() { c.callback(Ok(value as i32)); },
            Channel::Humidity => if let Some(c) = self.humidity.get() { c.callback(value as usize); },
        }
    }
    fn fail(&self) {
        self.cache.set([None; 2]);
        self.idle();
        if self.channel.get() == Channel::Temperature {
            if let Some(c) = self.temperature.get() { c.callback(Err(ErrorCode::FAIL)); }
        }
    }
    fn command(&self, buffer: &'static mut [u8]) -> Result<(), ErrorCode> {
        let index = self.preparation_index.get();
        let command = if index < PREPARATION.len() {
            self.state.set(State::Preparing); PREPARATION[index]
        } else { self.state.set(State::Command); 0x2400 };
        buffer[0] = (command >> 8) as u8; buffer[1] = command as u8;
        self.i2c.enable();
        match self.i2c.write(buffer, 2) {
            Ok(()) => Ok(()),
            Err((error, buffer)) => { self.buffer.replace(buffer); self.cache.set([None; 2]); self.idle(); Err(error.into()) }
        }
    }
    fn request(&self, channel: Channel) -> Result<(), ErrorCode> {
        match self.state.get() { State::Ready => (), State::Off => return Err(ErrorCode::OFF), _ => return Err(ErrorCode::BUSY) }
        if CACHE_OTHER {
            let mut cache = self.cache.get();
            if let Some(value) = cache[channel.index()].take() {
                self.cache.set(cache); self.emit(channel, value); return Ok(());
            }
        }
        let buffer = self.buffer.take().ok_or(ErrorCode::BUSY)?;
        self.channel.set(channel); self.retries.set(0); self.preparation_index.set(0);
        self.command(buffer)
    }
    fn crc(bytes: &[u8]) -> u8 {
        let mut crc = 0xff;
        for value in bytes { crc ^= value; for _ in 0..8 { crc = if crc & 0x80 != 0 { (crc << 1) ^ 0x31 } else { crc << 1 }; } }
        crc
    }
}
impl<'a, I: I2CDevice, A: Alarm<'a>> I2CClient for Sht3x<'a, I, A> {
    fn command_complete(&self, buffer: &'static mut [u8], status: Result<(), i2c::Error>) {
        if let Err(error) = status {
            self.buffer.replace(buffer);
            if self.state.get() == State::Reading && error == i2c::Error::AddressNak && self.retries.get() < MAX_READ_NAKS {
                self.retries.set(self.retries.get() + 1); self.wait(State::Waiting, 1000);
            } else { self.fail(); }
            return;
        }
        match self.state.get() {
            State::Preparing => { self.preparation_index.set(self.preparation_index.get() + 1); self.buffer.replace(buffer); self.wait(State::PrepareWait, 2000); }
            State::Command => { self.buffer.replace(buffer); self.wait(State::Waiting, WAIT_US); }
            State::Reading => {
                let c = self.channel.get();
                let offset = c.index() * 3;
                if Self::crc(&buffer[offset..offset + 2]) != buffer[offset + 2] {
                    self.buffer.replace(buffer); self.fail(); return;
                }
                let t = u16::from_be_bytes([buffer[0], buffer[1]]) as i64;
                let h = u16::from_be_bytes([buffer[3], buffer[4]]) as i64;
                let values = [-4500 + t * 17500 / 65535, h * 10000 / 65535];
                if CACHE_OTHER && !(TEMP_ONLY && c == Channel::Temperature) {
                    let other = 1 - c.index(); let o = other * 3;
                    if Self::crc(&buffer[o..o + 2]) == buffer[o + 2] {
                        let mut cache = self.cache.get(); cache[other] = Some(values[other]); self.cache.set(cache);
                    }
                }
                self.buffer.replace(buffer); self.idle(); self.emit(c, values[c.index()]);
            }
            _ => { self.buffer.replace(buffer); self.fail(); }
        }
    }
}
impl<'a, I: I2CDevice, A: Alarm<'a>> AlarmClient for Sht3x<'a, I, A> {
    fn alarm(&self) {
        match self.state.get() {
            State::PowerWait => self.state.set(State::Ready),
            State::PrepareWait => if let Some(buffer) = self.buffer.take() { if self.command(buffer).is_err() { self.fail(); } },
            State::Waiting => if let Some(buffer) = self.buffer.take() {
                self.state.set(State::Reading); self.i2c.enable();
                let len = if TEMP_ONLY && self.channel.get() == Channel::Temperature { 3 } else { 6 };
                if let Err((_error, buffer)) = self.i2c.read(buffer, len) { self.buffer.replace(buffer); self.fail(); }
            },
            _ => (),
        }
    }
}
impl<'a, I: I2CDevice, A: Alarm<'a>> TemperatureDriver<'a> for Sht3x<'a, I, A> {
    fn set_client(&self, c: &'a dyn TemperatureClient) { self.temperature.set(Some(c)); }
    fn read_temperature(&self) -> Result<(), ErrorCode> { self.request(Channel::Temperature) }
}
impl<'a, I: I2CDevice, A: Alarm<'a>> HumidityDriver<'a> for Sht3x<'a, I, A> {
    fn set_client(&self, c: &'a dyn HumidityClient) { self.humidity.set(Some(c)); }
    fn read_humidity(&self) -> Result<(), ErrorCode> { self.request(Channel::Humidity) }
}
