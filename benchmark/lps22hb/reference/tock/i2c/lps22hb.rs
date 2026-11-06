// SPDX-License-Identifier: Apache-2.0
//! Evaluator-only reference; never copy into a method's prepared workspace.
//!
//! Continuous 1 Hz operation, with BDU and the Rev 6 I2C restriction: disable
//! IF_ADD_INC, read output bytes individually, and read PRESS_OUT_H last.

use core::cell::Cell;
use kernel::hil::i2c::{self, I2CClient, I2CDevice};
use kernel::hil::sensors::{PressureClient, PressureDriver, TemperatureClient, TemperatureDriver};
use kernel::hil::time::{Alarm, AlarmClient, Frequency, Ticks};
use kernel::utilities::cells::TakeCell;
use kernel::ErrorCode;

pub const BUFFER_SIZE: usize = 8;
const FIRST_POLL_MS: u32 = 20;
const OUTPUT_ORDER: [(u8, usize); 5] = [(0x2b, 3), (0x2c, 4), (0x28, 0), (0x29, 1), (0x2a, 2)];

#[derive(Clone, Copy, PartialEq)]
enum State {
    Off,
    Identity,
    Interface,
    Configure,
    Ready,
    Status,
    Wait,
    Output,
}

#[derive(Clone, Copy)]
enum Channel {
    Temperature,
    Pressure,
}

pub struct Lps22hb<'a, I: I2CDevice, A: Alarm<'a>> {
    i2c: &'a I,
    alarm: &'a A,
    buffer: TakeCell<'static, [u8]>,
    state: Cell<State>,
    initialized: Cell<bool>,
    pending: Cell<Option<Channel>>,
    request_start: Cell<A::Ticks>,
    first_wait: Cell<bool>,
    output_index: Cell<usize>,
    sample: Cell<[u8; 5]>,
    temperature_client: Cell<Option<&'a dyn TemperatureClient>>,
    pressure_client: Cell<Option<&'a dyn PressureClient>>,
}

impl<'a, I: I2CDevice, A: Alarm<'a>> Lps22hb<'a, I, A> {
    pub fn new(i2c: &'a I, alarm: &'a A, buffer: &'static mut [u8]) -> Self {
        Self {
            i2c,
            alarm,
            buffer: TakeCell::new(buffer),
            state: Cell::new(State::Off),
            initialized: Cell::new(false),
            pending: Cell::new(None),
            request_start: Cell::new(A::Ticks::from(0)),
            first_wait: Cell::new(true),
            output_index: Cell::new(0),
            sample: Cell::new([0; 5]),
            temperature_client: Cell::new(None),
            pressure_client: Cell::new(None),
        }
    }

    pub fn startup(&self) -> Result<(), ErrorCode> {
        match self.state.get() {
            State::Ready => return Ok(()),
            State::Off => (),
            _ => return Err(ErrorCode::BUSY),
        }
        let buffer = self.buffer.take().ok_or(ErrorCode::BUSY)?;
        if buffer.len() < BUFFER_SIZE {
            self.buffer.replace(buffer);
            return Err(ErrorCode::SIZE);
        }
        let result = self.read_register(buffer, 0x0f, State::Identity);
        if result.is_err() {
            self.restore_idle();
        }
        result
    }

    fn read_register(
        &self,
        buffer: &'static mut [u8],
        register: u8,
        next: State,
    ) -> Result<(), ErrorCode> {
        buffer[0] = register;
        self.state.set(next);
        self.i2c.enable();
        match self.i2c.write_read(buffer, 1, 1) {
            Ok(()) => Ok(()),
            Err((error, buffer)) => {
                self.buffer.replace(buffer);
                Err(error.into())
            }
        }
    }

    fn write_register(
        &self,
        buffer: &'static mut [u8],
        register: u8,
        value: u8,
        next: State,
    ) -> Result<(), ErrorCode> {
        buffer[0] = register;
        buffer[1] = value;
        self.state.set(next);
        self.i2c.enable();
        match self.i2c.write(buffer, 2) {
            Ok(()) => Ok(()),
            Err((error, buffer)) => {
                self.buffer.replace(buffer);
                Err(error.into())
            }
        }
    }

    fn restore_idle(&self) {
        self.i2c.disable();
        self.state.set(if self.initialized.get() { State::Ready } else { State::Off });
    }

    fn fail_accepted(&self, error: ErrorCode) {
        let channel = self.pending.take();
        self.restore_idle();
        // Restore state and ownership before callbacks: clients may reenter.
        match channel {
            Some(Channel::Temperature) => {
                if let Some(client) = self.temperature_client.get() {
                    client.callback(Err(error));
                }
            }
            Some(Channel::Pressure) => {
                if let Some(client) = self.pressure_client.get() {
                    client.callback(Err(error));
                }
            }
            None => (),
        }
    }

    fn wait(&self, buffer: &'static mut [u8]) {
        self.buffer.replace(buffer);
        self.i2c.disable();
        self.state.set(State::Wait);
        let milliseconds = if self.first_wait.replace(false) { FIRST_POLL_MS } else { 20 };
        let ticks = (A::Frequency::frequency() as u64 * milliseconds as u64 + 999) / 1000;
        self.alarm.set_alarm(
            self.alarm.now(),
            A::Ticks::from_or_max(ticks).max(self.alarm.minimum_dt()),
        );
    }

    fn timed_out(&self) -> bool {
        let elapsed = self.alarm.now().wrapping_sub(self.request_start.get()).into_u32() as u64;
        elapsed * 1000 >= A::Frequency::frequency() as u64 * 2100
    }

    fn request(&self, channel: Channel) -> Result<(), ErrorCode> {
        if self.state.get() != State::Ready {
            return Err(if self.state.get() == State::Off { ErrorCode::OFF } else { ErrorCode::BUSY });
        }
        let buffer = self.buffer.take().ok_or(ErrorCode::BUSY)?;
        self.pending.set(Some(channel));
        self.request_start.set(self.alarm.now());
        self.first_wait.set(true);
        let result = self.read_register(buffer, 0x27, State::Status);
        if result.is_err() {
            // A synchronously rejected request is not accepted and must not
            // produce an asynchronous client callback.
            self.pending.set(None);
            self.restore_idle();
        }
        result
    }

    fn finish_sample(&self, buffer: &'static mut [u8]) {
        let bytes = self.sample.get();
        let temperature = i16::from_le_bytes([bytes[3], bytes[4]]) as i32;
        let raw_pressure = u32::from_le_bytes([bytes[0], bytes[1], bytes[2], 0]);
        let pressure = raw_pressure / 4096;
        let channel = self.pending.take();
        self.buffer.replace(buffer);
        self.restore_idle();
        match channel {
            Some(Channel::Temperature) => {
                if let Some(client) = self.temperature_client.get() {
                    client.callback(Ok(temperature));
                }
            }
            Some(Channel::Pressure) => {
                if let Some(client) = self.pressure_client.get() {
                    client.callback(Ok(pressure));
                }
            }
            None => (),
        }
    }
}

impl<'a, I: I2CDevice, A: Alarm<'a>> I2CClient for Lps22hb<'a, I, A> {
    fn command_complete(&self, buffer: &'static mut [u8], status: Result<(), i2c::Error>) {
        if let Err(error) = status {
            self.buffer.replace(buffer);
            self.fail_accepted(error.into());
            return;
        }
        let result = match self.state.get() {
            State::Identity => {
                if buffer[0] != 0xb1 {
                    self.buffer.replace(buffer);
                    self.fail_accepted(ErrorCode::NODEVICE);
                    return;
                }
                // Datasheet Rev 6 p.37, footnote 2: with BDU over I2C,
                // IF_ADD_INC must be zero and outputs read one byte at a time.
                self.write_register(buffer, 0x11, 0x00, State::Interface)
            }
            State::Interface => self.write_register(buffer, 0x10, 0x12, State::Configure),
            State::Configure => {
                self.initialized.set(true);
                self.buffer.replace(buffer);
                self.restore_idle();
                return;
            }
            State::Status => {
                // Official STATUS: P_DA bit 0, T_DA bit 1. Both outputs are
                // consumed to release BDU, regardless of the requested HIL.
                if buffer[0] & 0x03 != 0x03 {
                    if self.timed_out() {
                        self.buffer.replace(buffer);
                        self.fail_accepted(ErrorCode::BUSY);
                    } else {
                        self.wait(buffer);
                    }
                    return;
                }
                self.output_index.set(0);
                self.read_register(buffer, OUTPUT_ORDER[0].0, State::Output)
            }
            State::Output => {
                let index = self.output_index.get();
                let mut sample = self.sample.get();
                sample[OUTPUT_ORDER[index].1] = buffer[0];
                self.sample.set(sample);
                if index + 1 == OUTPUT_ORDER.len() {
                    self.finish_sample(buffer);
                    return;
                }
                self.output_index.set(index + 1);
                self.read_register(buffer, OUTPUT_ORDER[index + 1].0, State::Output)
            }
            _ => {
                self.buffer.replace(buffer);
                self.fail_accepted(ErrorCode::FAIL);
                return;
            }
        };
        if let Err(error) = result {
            self.fail_accepted(error);
        }
    }
}

impl<'a, I: I2CDevice, A: Alarm<'a>> AlarmClient for Lps22hb<'a, I, A> {
    fn alarm(&self) {
        if self.state.get() != State::Wait {
            return;
        }
        if let Some(buffer) = self.buffer.take() {
            if let Err(error) = self.read_register(buffer, 0x27, State::Status) {
                self.fail_accepted(error);
            }
        } else {
            self.fail_accepted(ErrorCode::NOMEM);
        }
    }
}

impl<'a, I: I2CDevice, A: Alarm<'a>> TemperatureDriver<'a> for Lps22hb<'a, I, A> {
    fn set_client(&self, client: &'a dyn TemperatureClient) {
        self.temperature_client.set(Some(client));
    }
    fn read_temperature(&self) -> Result<(), ErrorCode> {
        self.request(Channel::Temperature)
    }
}

impl<'a, I: I2CDevice, A: Alarm<'a>> PressureDriver<'a> for Lps22hb<'a, I, A> {
    fn set_client(&self, client: &'a dyn PressureClient) {
        self.pressure_client.set(Some(client));
    }
    fn read_atmospheric_pressure(&self) -> Result<(), ErrorCode> {
        self.request(Channel::Pressure)
    }
}
