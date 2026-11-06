// SPDX-License-Identifier: Apache-2.0
//! Evaluator-only ISL29035 reference. Not supplied to a candidate method.
//! Continuous ambient-light mode, 4000 lux range, 8-bit/410 us integration.

use core::cell::Cell;
use kernel::hil::i2c::{self, I2CClient, I2CDevice};
use kernel::hil::sensors::{AmbientLight, AmbientLightClient};
use kernel::hil::time::{Alarm, AlarmClient, Frequency, Ticks};
use kernel::utilities::cells::TakeCell;
use kernel::ErrorCode;

pub const BUFFER_SIZE: usize = 3;
const SAMPLE_WAIT_US: u32 = 1000;

#[derive(Clone, Copy, PartialEq)]
enum State {
    Off,
    Identity,
    Brownout,
    PowerDown,
    Resolution,
    Enable,
    Ready,
    SelectOutput,
    Wait,
    Sample,
}

pub struct Isl29035<'a, I: I2CDevice, A: Alarm<'a>> {
    i2c: &'a I,
    alarm: &'a A,
    buffer: TakeCell<'static, [u8]>,
    state: Cell<State>,
    initialized: Cell<bool>,
    client: Cell<Option<&'a dyn AmbientLightClient>>,
}

impl<'a, I: I2CDevice, A: Alarm<'a>> Isl29035<'a, I, A> {
    pub fn new(i2c: &'a I, alarm: &'a A, buffer: &'static mut [u8]) -> Self {
        Self {
            i2c,
            alarm,
            buffer: TakeCell::new(buffer),
            state: Cell::new(State::Off),
            initialized: Cell::new(false),
            client: Cell::new(None),
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

    fn fail_accepted(&self, _error: ErrorCode) {
        // AmbientLightClient carries only a scalar lux value. In particular,
        // callback(0) would falsely report a valid darkness measurement.
        self.restore_idle();
    }

    fn wait_for_sample(&self, buffer: &'static mut [u8]) {
        self.buffer.replace(buffer);
        self.i2c.disable();
        self.state.set(State::Wait);
        // Round up to preserve the minimum integration wait on the board RTC.
        let ticks = (A::Frequency::frequency() as u64 * SAMPLE_WAIT_US as u64 + 999999) / 1000000;
        self.alarm.set_alarm(self.alarm.now(), A::Ticks::from_or_max(ticks).max(self.alarm.minimum_dt()));
    }

    fn request(&self) -> Result<(), ErrorCode> {
        if self.state.get() != State::Ready {
            return Err(if self.state.get() == State::Off { ErrorCode::OFF } else { ErrorCode::BUSY });
        }
        let buffer = self.buffer.take().ok_or(ErrorCode::BUSY)?;
        // COMMAND-I is a documented combined byte read; its interrupt flag
        // is not a conversion-ready indication and is deliberately ignored.
        // A refusal here rejects the request synchronously. The final data
        // acquisition also uses a combined read, requiring no register-pointer
        // retention across a STOP and the integration wait.
        let result = self.read_register(buffer, 0x00, State::SelectOutput);
        if result.is_err() {
            self.restore_idle();
        }
        result
    }
}

impl<'a, I: I2CDevice, A: Alarm<'a>> I2CClient for Isl29035<'a, I, A> {
    fn command_complete(&self, buffer: &'static mut [u8], status: Result<(), i2c::Error>) {
        if let Err(error) = status {
            self.buffer.replace(buffer);
            self.fail_accepted(error.into());
            return;
        }
        let result = match self.state.get() {
            State::Identity => {
                let id = buffer[0];
                if id & 0x38 != 0x28 {
                    self.buffer.replace(buffer);
                    self.fail_accepted(ErrorCode::NODEVICE);
                    return;
                }
                // BOUT is bit 7; preserve all other bits of this register.
                self.write_register(buffer, 0x0f, id & 0x7f, State::Brownout)
            }
            State::Brownout => self.write_register(buffer, 0x00, 0x00, State::PowerDown),
            State::PowerDown => self.write_register(buffer, 0x01, 0x09, State::Resolution),
            State::Resolution => self.write_register(buffer, 0x00, 0xa0, State::Enable),
            State::Enable => {
                self.initialized.set(true);
                self.buffer.replace(buffer);
                self.restore_idle();
                return;
            }
            State::SelectOutput => {
                self.wait_for_sample(buffer);
                return;
            }
            State::Sample => {
                let raw = buffer[0] as usize;
                let lux = raw * 4000 / 256;
                self.buffer.replace(buffer);
                self.restore_idle();
                // Restore the state and buffer before a client can reenter.
                if let Some(client) = self.client.get() {
                    client.callback(lux);
                }
                return;
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

impl<'a, I: I2CDevice, A: Alarm<'a>> AlarmClient for Isl29035<'a, I, A> {
    fn alarm(&self) {
        if self.state.get() != State::Wait {
            return;
        }
        if let Some(buffer) = self.buffer.take() {
            self.state.set(State::Sample);
            self.i2c.enable();
            buffer[0] = 0x02;
            if let Err((error, buffer)) = self.i2c.write_read(buffer, 1, 1) {
                self.buffer.replace(buffer);
                self.fail_accepted(error.into());
            }
        } else {
            self.fail_accepted(ErrorCode::NOMEM);
        }
    }
}

impl<'a, I: I2CDevice, A: Alarm<'a>> AmbientLight<'a> for Isl29035<'a, I, A> {
    fn set_client(&self, client: &'a dyn AmbientLightClient) {
        self.client.set(Some(client));
    }
    fn read_light_intensity(&self) -> Result<(), ErrorCode> {
        self.request()
    }
}
