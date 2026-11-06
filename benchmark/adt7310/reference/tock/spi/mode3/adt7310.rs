// Licensed under the Apache License, Version 2.0 or the MIT License.
// Host-only reference control; not an agent input or scoring oracle.
use core::cell::Cell;
use kernel::hil::sensors::{TemperatureClient, TemperatureDriver};
use kernel::hil::spi::{ClockPhase, ClockPolarity, SpiMasterClient, SpiMasterDevice};
use kernel::hil::time::{Alarm, AlarmClient, Frequency, Ticks};
use kernel::utilities::cells::TakeCell;
use kernel::ErrorCode;

#[derive(Clone, Copy, PartialEq)]
enum State { Off, Identity, Configuration, Ready, Status, Waiting, Data }
pub struct Adt7310<'a, S: SpiMasterDevice<'a>, A: Alarm<'a>> {
    spi: &'a S, alarm: &'a A, tx: TakeCell<'static, [u8]>, rx: TakeCell<'static, [u8]>,
    state: Cell<State>, requested: Cell<bool>, polls: Cell<u16>,
    client: Cell<Option<&'a dyn TemperatureClient>>,
}
impl<'a, S: SpiMasterDevice<'a>, A: Alarm<'a>> Adt7310<'a, S, A> {
    pub fn new(spi: &'a S, alarm: &'a A, tx: &'static mut [u8], rx: &'static mut [u8]) -> Self {
        Self { spi, alarm, tx: TakeCell::new(tx), rx: TakeCell::new(rx), state: Cell::new(State::Off),
               requested: Cell::new(false), polls: Cell::new(0), client: Cell::new(None) }
    }
    fn transfer(&self, bytes: &[u8], state: State) -> Result<(), ErrorCode> {
        let tx = self.tx.take().ok_or(ErrorCode::BUSY)?;
        let rx = match self.rx.take() { Some(rx) => rx, None => { self.tx.replace(tx); return Err(ErrorCode::BUSY); } };
        if tx.len() < bytes.len() || rx.len() < bytes.len() {
            self.tx.replace(tx); self.rx.replace(rx); return Err(ErrorCode::SIZE);
        }
        tx[..bytes.len()].copy_from_slice(bytes); self.state.set(state);
        match self.spi.read_write_bytes(tx, Some(rx), bytes.len()) {
            Ok(()) => Ok(()),
            Err((error, tx, rx)) => { self.tx.replace(tx); if let Some(rx) = rx { self.rx.replace(rx); } Err(error) }
        }
    }
    fn failed(&self, error: ErrorCode) {
        let _ = self.alarm.disarm();
        if self.requested.replace(false) {
            self.state.set(State::Ready);
            if let Some(client) = self.client.get() { client.callback(Err(error)); }
        } else { self.state.set(State::Off); }
    }
    pub fn startup(&self) -> Result<(), ErrorCode> {
        if self.state.get() != State::Off { return Err(ErrorCode::BUSY); }
        self.spi.configure(ClockPolarity::IdleHigh, ClockPhase::SampleTrailing, 1_000_000)?;
        let result = self.transfer(&[0x58, 0], State::Identity);
        if result.is_err() { self.state.set(State::Off); } result
    }
}
impl<'a, S: SpiMasterDevice<'a>, A: Alarm<'a>> SpiMasterClient for Adt7310<'a, S, A> {
    fn read_write_done(&self, tx: &'static mut [u8], rx: Option<&'static mut [u8]>, len: usize, status: Result<(), ErrorCode>) {
        let state = self.state.get();
        let need = if state == State::Data { 3 } else { 2 };
        let mut bytes = [0u8; 3];
        let valid = rx.as_ref().map_or(false, |r| r.len() >= need) && len >= need;
        if let Some(rx) = rx {
            if valid { bytes[..need].copy_from_slice(&rx[..need]); }
            self.rx.replace(rx);
        }
        self.tx.replace(tx);
        if let Err(e) = status { self.failed(e); return; }
        if !valid { self.failed(ErrorCode::SIZE); return; }
        let next = match state {
            State::Identity => {
                if bytes[1] & 0xf8 != 0xc0 { self.failed(ErrorCode::NODEVICE); return; }
                self.transfer(&[0x08, 0x80], State::Configuration)
            }
            State::Configuration => { self.state.set(State::Ready); Ok(()) }
            State::Status => {
                if bytes[1] & 0x80 == 0 { self.transfer(&[0x50, 0, 0], State::Data) }
                else if self.polls.get() >= 100 { self.failed(ErrorCode::FAIL); return; }
                else {
                    self.polls.set(self.polls.get() + 1); self.state.set(State::Waiting);
                    let ticks = (A::Frequency::frequency() as u64 * 10_000 + 999_999) / 1_000_000;
                    self.alarm.set_alarm(self.alarm.now(), A::Ticks::from_or_max(ticks).max(self.alarm.minimum_dt())); Ok(())
                }
            }
            State::Data => {
                let raw = i16::from_be_bytes([bytes[1], bytes[2]]) as i32;
                let value = raw * 100 / 128;
                self.requested.set(false); self.state.set(State::Ready);
                if let Some(client) = self.client.get() { client.callback(Ok(value)); } Ok(())
            }
            _ => { self.failed(ErrorCode::FAIL); return; }
        };
        if let Err(e) = next { self.failed(e); }
    }
}
impl<'a, S: SpiMasterDevice<'a>, A: Alarm<'a>> AlarmClient for Adt7310<'a, S, A> {
    fn alarm(&self) {
        if self.state.get() == State::Waiting {
            if let Err(e) = self.transfer(&[0x40, 0], State::Status) { self.failed(e); }
        }
    }
}
impl<'a, S: SpiMasterDevice<'a>, A: Alarm<'a>> TemperatureDriver<'a> for Adt7310<'a, S, A> {
    fn set_client(&self, client: &'a dyn TemperatureClient) { self.client.set(Some(client)); }
    fn read_temperature(&self) -> Result<(), ErrorCode> {
        match self.state.get() { State::Ready => (), State::Off => return Err(ErrorCode::OFF), _ => return Err(ErrorCode::BUSY) }
        self.polls.set(0); self.requested.set(true);
        let result = self.transfer(&[0x40, 0], State::Status);
        if result.is_err() { self.requested.set(false); self.state.set(State::Ready); } result
    }
}
