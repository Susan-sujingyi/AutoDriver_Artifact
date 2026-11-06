// SPDX-License-Identifier: Apache-2.0
//! Evaluator-only APDS9960 polling proximity reference.

use core::cell::Cell;
use kernel::hil::i2c::{self, I2CClient, I2CDevice};
use kernel::hil::sensors::{ProximityClient, ProximityDriver};
use kernel::hil::time::{Alarm, AlarmClient, Frequency, Ticks};
use kernel::utilities::cells::TakeCell;
use kernel::ErrorCode;

pub const BUFFER_SIZE: usize = 3;
const POWER_ON_WAIT_US: u32 = 6000;
const POLL_WAIT_US: u32 = 2000;
const MAX_READY_WAITS: u8 = 45;
const CONFIGURATION: [(u8, u8); 7] = [
    (0x80, 0x00), // Disable engines while selecting the fixed configuration.
    (0x8d, 0x60), // Required CONFIG1 reserved-bit value, WLONG off.
    (0x8e, 0x47), // 8 us pulses, eight pulses per proximity acquisition.
    (0x8f, 0x04), // PGAIN 2x, LDRIVE 100 mA; ALS gain is unused.
    (0x90, 0x11), // LED boost 150%, required bit 0; saturation IRQs off.
    (0x9f, 0x00), // No sleep-after-interrupt or photodiode masking.
    (0x80, 0x05), // PON and PEN only: no ALS, gesture, wait or IRQ engines.
];

#[derive(Clone, Copy, PartialEq)]
enum State {
    Off,
    PowerOnWait,
    Identity,
    Configure,
    Ready,
    Status,
    ReadinessWait,
    Sample,
}

pub struct Apds9960<'a, I: I2CDevice, A: Alarm<'a>> {
    i2c: &'a I,
    alarm: &'a A,
    buffer: TakeCell<'static, [u8]>,
    state: Cell<State>,
    initialized: Cell<bool>,
    configuration_index: Cell<usize>,
    readiness_waits: Cell<u8>,
    client: Cell<Option<&'a dyn ProximityClient>>,
}

impl<'a, I: I2CDevice, A: Alarm<'a>> Apds9960<'a, I, A> {
    pub fn new(i2c: &'a I, alarm: &'a A, buffer: &'static mut [u8]) -> Self {
        Self {
            i2c,
            alarm,
            buffer: TakeCell::new(buffer),
            state: Cell::new(State::Off),
            initialized: Cell::new(false),
            configuration_index: Cell::new(0),
            readiness_waits: Cell::new(0),
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
        // Figure 7 permits I2C after the 5.7 ms power-on initialization.
        self.wait_us(buffer, State::PowerOnWait, POWER_ON_WAIT_US);
        Ok(())
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

    fn write_configuration(&self, buffer: &'static mut [u8]) -> Result<(), ErrorCode> {
        let (register, value) = CONFIGURATION[self.configuration_index.get()];
        buffer[0] = register;
        buffer[1] = value;
        self.state.set(State::Configure);
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
        // This HIL cannot represent an error; zero is a valid proximity value.
        self.restore_idle();
    }

    fn wait_us(&self, buffer: &'static mut [u8], next: State, microseconds: u32) {
        self.buffer.replace(buffer);
        self.i2c.disable();
        self.state.set(next);
        let ticks = (A::Frequency::frequency() as u64 * microseconds as u64 + 999999) / 1000000;
        self.alarm.set_alarm(self.alarm.now(), A::Ticks::from_or_max(ticks).max(self.alarm.minimum_dt()));
    }

    fn request(&self) -> Result<(), ErrorCode> {
        if self.state.get() != State::Ready {
            return Err(if self.state.get() == State::Off { ErrorCode::OFF } else { ErrorCode::BUSY });
        }
        let buffer = self.buffer.take().ok_or(ErrorCode::BUSY)?;
        self.readiness_waits.set(0);
        let result = self.read_register(buffer, 0x93, State::Status);
        if result.is_err() {
            self.restore_idle();
        }
        result
    }
}

impl<'a, I: I2CDevice, A: Alarm<'a>> I2CClient for Apds9960<'a, I, A> {
    fn command_complete(&self, buffer: &'static mut [u8], status: Result<(), i2c::Error>) {
        if let Err(error) = status {
            self.buffer.replace(buffer);
            self.fail_accepted(error.into());
            return;
        }
        let result = match self.state.get() {
            State::Identity => {
                let id = buffer[0];
                // 0xAB is documented by the manufacturer. 0x9C is optional
                // compatibility inherited from the source, not another
                // independently verified manufacturer identity.
                if id != 0xab && id != 0x9c {
                    self.buffer.replace(buffer);
                    self.fail_accepted(ErrorCode::NODEVICE);
                    return;
                }
                self.configuration_index.set(0);
                self.write_configuration(buffer)
            }
            State::Configure => {
                let index = self.configuration_index.get() + 1;
                if index == CONFIGURATION.len() {
                    self.initialized.set(true);
                    self.buffer.replace(buffer);
                    self.restore_idle();
                    return;
                }
                self.configuration_index.set(index);
                self.write_configuration(buffer)
            }
            State::Status => {
                // PVALID is new proximity data, whereas PINT is a threshold
                // condition and must not reject valid raw values 0 and 1.
                if buffer[0] & 0x02 == 0 {
                    let waits = self.readiness_waits.get();
                    if waits >= MAX_READY_WAITS {
                        self.buffer.replace(buffer);
                        self.fail_accepted(ErrorCode::BUSY);
                    } else {
                        self.readiness_waits.set(waits + 1);
                        self.wait_us(buffer, State::ReadinessWait, POLL_WAIT_US);
                    }
                    return;
                }
                self.read_register(buffer, 0x9c, State::Sample)
            }
            State::Sample => {
                let proximity = buffer[0];
                self.buffer.replace(buffer);
                self.restore_idle();
                if let Some(client) = self.client.get() {
                    client.callback(proximity);
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

impl<'a, I: I2CDevice, A: Alarm<'a>> AlarmClient for Apds9960<'a, I, A> {
    fn alarm(&self) {
        let (register, next) = match self.state.get() {
            State::PowerOnWait => (0x92, State::Identity),
            State::ReadinessWait => (0x93, State::Status),
            _ => return,
        };
        if let Some(buffer) = self.buffer.take() {
            if let Err(error) = self.read_register(buffer, register, next) {
                self.fail_accepted(error);
            }
        } else {
            self.fail_accepted(ErrorCode::NOMEM);
        }
    }
}

impl<'a, I: I2CDevice, A: Alarm<'a>> ProximityDriver<'a> for Apds9960<'a, I, A> {
    fn set_client(&self, client: &'a dyn ProximityClient) {
        self.client.set(Some(client));
    }
    fn read_proximity(&self) -> Result<(), ErrorCode> {
        self.request()
    }
    fn read_proximity_on_interrupt(
        &self,
        _low_threshold: u8,
        _high_threshold: u8,
    ) -> Result<(), ErrorCode> {
        Err(ErrorCode::NOSUPPORT)
    }
}
