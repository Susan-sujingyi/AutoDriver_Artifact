// SPDX-License-Identifier: Apache-2.0
// Integer compensation adapted from Zephyr's bme280.c:
// Copyright (c) 2016, 2017 Intel Corporation
// Copyright (c) 2017 IpTronix S.r.l.
// Copyright (c) 2021 Nordic Semiconductor ASA
// That implementation cites Bosch BME280 datasheet section 4.2.3.

//! Evaluator-only reference: never include this file in an agent workspace.
//! A fresh forced conversion serves exactly one accepted channel request.
//! SpiMasterDevice binds the selected chip-select. Initialization is
//! asynchronous; repeated startup calls are BUSY until ready, then idempotent.

use core::cell::Cell;
use kernel::hil::spi::{SpiMasterClient, SpiMasterDevice, ClockPhase, ClockPolarity};
use kernel::hil::sensors::{
    HumidityClient, HumidityDriver, PressureClient, PressureDriver, TemperatureClient,
    TemperatureDriver,
};
use kernel::hil::time::{Alarm, AlarmClient, Frequency, Ticks};
use kernel::utilities::cells::TakeCell;
use kernel::ErrorCode;

#[derive(Clone, Copy, PartialEq)]
enum State {
    Off,
    Id,
    Reset,
    ResetWait,
    CalibrationTp,
    CalibrationHumidity,
    Config,
    HumidityConfig,
    Ready,
    Trigger,
    ConversionWait,
    ConversionStatus,
    Sample,
}

#[derive(Clone, Copy)]
enum Channel {
    Temperature,
    Humidity,
    Pressure,
}

#[derive(Clone, Copy, Default)]
struct Calibration {
    t1: u16,
    t2: i16,
    t3: i16,
    p1: u16,
    p2: i16,
    p3: i16,
    p4: i16,
    p5: i16,
    p6: i16,
    p7: i16,
    p8: i16,
    p9: i16,
    h1: u8,
    h2: i16,
    h3: u8,
    h4: i16,
    h5: i16,
    h6: i8,
}

pub struct Bme280<'a, I: SpiMasterDevice<'a>, A: Alarm<'a>> {
    spi: &'a I,
    tx: TakeCell<'static,[u8]>,
    read_length: Cell<usize>,
    alarm: &'a A,
    buffer: TakeCell<'static, [u8]>,
    state: Cell<State>,
    initialized: Cell<bool>,
    pending: Cell<Option<Channel>>,
    readiness_polls: Cell<u8>,
    calibration: Cell<Calibration>,
    temperature_client: Cell<Option<&'a dyn TemperatureClient>>,
    humidity_client: Cell<Option<&'a dyn HumidityClient>>,
    pressure_client: Cell<Option<&'a dyn PressureClient>>,
}

impl<'a, I: SpiMasterDevice<'a>, A: Alarm<'a>> Bme280<'a, I, A> {
    pub fn new(spi: &'a I, alarm: &'a A, tx: &'static mut [u8], buffer: &'static mut [u8]) -> Self {
        Self {
            spi, tx:TakeCell::new(tx), read_length:Cell::new(0),
            alarm,
            buffer: TakeCell::new(buffer),
            state: Cell::new(State::Off),
            initialized: Cell::new(false),
            pending: Cell::new(None),
            readiness_polls: Cell::new(0),
            calibration: Cell::new(Calibration::default()),
            temperature_client: Cell::new(None),
            humidity_client: Cell::new(None),
            pressure_client: Cell::new(None),
        }
    }

    pub fn startup(&self) -> Result<(), ErrorCode> {
        match self.state.get() {
            State::Ready => return Ok(()),
            State::Off => (),
            _ => return Err(ErrorCode::BUSY),
        }
        self.spi.configure(ClockPolarity::IdleLow,ClockPhase::SampleLeading,1_000_000)?;
        let buffer = self.buffer.take().ok_or(ErrorCode::BUSY)?;
        if buffer.len() < 26 {
            self.buffer.replace(buffer);
            return Err(ErrorCode::SIZE);
        }
        let result = self.read_register(buffer, 0xd0, 1, State::Id);
        if result.is_err() {
            self.restore_idle();
        }
        result
    }

    // Submission errors return ownership immediately. Callers decide whether
    // the operation was already accepted and therefore requires a callback.
    fn transfer(&self,buffer:&'static mut [u8],reg:u8,value:u8,length:usize,next:State)->Result<(),ErrorCode> {
        let tx=match self.tx.take(){Some(tx)=>tx,None=>{self.buffer.replace(buffer);return Err(ErrorCode::BUSY)}};
        let wire_len=if length==0{2}else{length+1};
        if tx.len()<wire_len || buffer.len()<wire_len {self.tx.replace(tx);self.buffer.replace(buffer);return Err(ErrorCode::SIZE)}
        tx[..wire_len].fill(0);tx[0]=if length==0{reg&0x7f}else{reg|0x80};if length==0{tx[1]=value;}
        self.state.set(next);self.read_length.set(length);
        match self.spi.read_write_bytes(tx,Some(buffer),wire_len) {
            Ok(())=>Ok(()),Err((e,tx,rx))=>{self.tx.replace(tx);if let Some(rx)=rx{self.buffer.replace(rx);}Err(e)}
        }
    }
    fn read_register(&self,buffer:&'static mut [u8],register:u8,length:usize,next:State)->Result<(),ErrorCode> {
        self.transfer(buffer,register,0,length,next)
    }
    fn write_register(&self,buffer:&'static mut [u8],register:u8,value:u8,next:State)->Result<(),ErrorCode> {
        self.transfer(buffer,register,value,0,next)
    }

    fn restore_idle(&self) {
        
        self.state.set(if self.initialized.get() {
            State::Ready
        } else {
            State::Off
        });
    }

    fn fail_accepted(&self, error: ErrorCode) {
        let channel = self.pending.take();
        self.restore_idle();
        // State and buffer are restored before notifying clients, which may
        // reenter the capsule and submit the next request from their callback.
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
            // This Tock HumidityClient has no error-valued notification.
            Some(Channel::Humidity) | None => (),
        }
    }

    fn wait_ms(&self, buffer: &'static mut [u8], next: State, milliseconds: u32) {
        self.buffer.replace(buffer);
        
        self.state.set(next);
        // Round upward: ticks_from_ms rounds down and can violate the 2 ms
        // reset minimum on, for example, a 32768 Hz timer.
        let ticks = (A::Frequency::frequency() as u64 * milliseconds as u64 + 999) / 1000;
        let delta = A::Ticks::from_or_max(ticks).max(self.alarm.minimum_dt());
        self.alarm.set_alarm(self.alarm.now(), delta);
    }

    fn request(&self, channel: Channel) -> Result<(), ErrorCode> {
        if self.state.get() != State::Ready {
            return Err(if self.state.get() == State::Off {
                ErrorCode::OFF
            } else {
                ErrorCode::BUSY
            });
        }
        let buffer = self.buffer.take().ok_or(ErrorCode::BUSY)?;
        self.pending.set(Some(channel));
        self.readiness_polls.set(0);
        // osrs_t=1 (1x), osrs_p=5 (16x), mode=1 (forced).
        let result = self.write_register(buffer, 0xf4, 0x35, State::Trigger);
        if result.is_err() {
            self.pending.set(None);
            self.restore_idle();
        }
        result
    }

    fn calibration_tp(&self, bytes: &[u8]) {
        let word = |offset| u16::from_le_bytes([bytes[offset], bytes[offset + 1]]);
        let mut c = self.calibration.get();
        c.t1 = word(0);
        c.t2 = word(2) as i16;
        c.t3 = word(4) as i16;
        c.p1 = word(6);
        c.p2 = word(8) as i16;
        c.p3 = word(10) as i16;
        c.p4 = word(12) as i16;
        c.p5 = word(14) as i16;
        c.p6 = word(16) as i16;
        c.p7 = word(18) as i16;
        c.p8 = word(20) as i16;
        c.p9 = word(22) as i16;
        c.h1 = bytes[25]; // 0xa1 in the 26-byte 0x88..0xa1 burst.
        self.calibration.set(c);
    }

    fn calibration_humidity(&self, bytes: &[u8]) {
        let mut c = self.calibration.get();
        c.h2 = i16::from_le_bytes([bytes[0], bytes[1]]);
        c.h3 = bytes[2];
        let h4 = ((bytes[3] as u16) << 4) | ((bytes[4] & 0x0f) as u16);
        let h5 = ((bytes[5] as u16) << 4) | ((bytes[4] >> 4) as u16);
        // H4 and H5 are signed 12-bit quantities sharing register 0xe5.
        c.h4 = ((h4 << 4) as i16) >> 4;
        c.h5 = ((h5 << 4) as i16) >> 4;
        c.h6 = bytes[6] as i8;
        self.calibration.set(c);
    }

    fn temperature(c: Calibration, raw: i64) -> (i32, i64) {
        let var1 = (((raw >> 3) - ((c.t1 as i64) << 1)) * c.t2 as i64) >> 11;
        let delta = (raw >> 4) - c.t1 as i64;
        let var2 = (((delta * delta) >> 12) * c.t3 as i64) >> 14;
        let fine = var1 + var2;
        (((fine * 5 + 128) >> 8) as i32, fine)
    }

    fn pressure(c: Calibration, raw: i64, fine: i64) -> Result<u32, ErrorCode> {
        // Wider intermediates retain the datasheet integer rounding while
        // preventing arithmetic panics for unusual, but representable, trims.
        let mut var1 = fine as i128 - 128000;
        let mut var2 = var1 * var1 * c.p6 as i128;
        var2 += (var1 * c.p5 as i128) << 17;
        var2 += (c.p4 as i128) << 35;
        var1 = ((var1 * var1 * c.p3 as i128) >> 8) + ((var1 * c.p2 as i128) << 12);
        var1 = (((1i128 << 47) + var1) * c.p1 as i128) >> 33;
        if var1 == 0 {
            return Err(ErrorCode::FAIL);
        }
        let mut pressure = 1048576 - raw as i128;
        pressure = (((pressure << 31) - var2) * 3125) / var1;
        var1 = (c.p9 as i128 * (pressure >> 13) * (pressure >> 13)) >> 25;
        var2 = (c.p8 as i128 * pressure) >> 19;
        pressure = ((pressure + var1 + var2) >> 8) + ((c.p7 as i128) << 4);
        // The compensation result is Pa * 256; PressureClient consumes hPa.
        let hpa = pressure / 25600;
        if hpa < 0 || hpa > u32::MAX as i128 {
            Err(ErrorCode::FAIL)
        } else {
            Ok(hpa as u32)
        }
    }

    fn humidity(c: Calibration, raw: i64, fine: i64) -> usize {
        let mut h = fine as i128 - 76800;
        let left =
            (((raw as i128) << 14) - ((c.h4 as i128) << 20) - c.h5 as i128 * h + 16384) >> 15;
        let right = (((((h * c.h6 as i128) >> 10) * (((h * c.h3 as i128) >> 11) + 32768)) >> 10)
            + 2097152)
            * c.h2 as i128;
        h = left * ((right + 8192) >> 14);
        h -= ((((h >> 15) * (h >> 15)) >> 7) * c.h1 as i128) >> 4;
        h = h.clamp(0, 419430400);
        // Q22.10 percent -> hundredths of percent, bounded to 0..10000.
        (((h >> 12) * 100) / 1024) as usize
    }

    fn finish_sample(&self, buffer: &'static mut [u8]) {
        let raw_pressure =
            ((buffer[0] as i64) << 12) | ((buffer[1] as i64) << 4) | ((buffer[2] as i64) >> 4);
        let raw_temperature =
            ((buffer[3] as i64) << 12) | ((buffer[4] as i64) << 4) | ((buffer[5] as i64) >> 4);
        let raw_humidity = ((buffer[6] as i64) << 8) | buffer[7] as i64;
        let c = self.calibration.get();
        let (temperature, fine) = Self::temperature(c, raw_temperature);
        let channel = self.pending.take();
        self.buffer.replace(buffer);
        self.restore_idle();
        match channel {
            Some(Channel::Temperature) => {
                if let Some(client) = self.temperature_client.get() {
                    client.callback(Ok(temperature));
                }
            }
            Some(Channel::Humidity) => {
                if let Some(client) = self.humidity_client.get() {
                    client.callback(Self::humidity(c, raw_humidity, fine));
                }
            }
            Some(Channel::Pressure) => {
                if let Some(client) = self.pressure_client.get() {
                    client.callback(Self::pressure(c, raw_pressure, fine));
                }
            }
            None => (),
        }
    }
}

impl<'a, I: SpiMasterDevice<'a>, A: Alarm<'a>> SpiMasterClient for Bme280<'a, I, A> {
    fn read_write_done(&self,tx:&'static mut[u8],rx:Option<&'static mut[u8]>,length:usize,status:Result<(),ErrorCode>) {
        self.tx.replace(tx);
        let Some(buffer)=rx else{self.fail_accepted(ErrorCode::NOMEM);return;};
        let n=self.read_length.get();let wire_len=if n==0{2}else{n+1};
        if length<wire_len || buffer.len()<wire_len {self.buffer.replace(buffer);self.fail_accepted(ErrorCode::SIZE);return;}
        if status.is_ok() && n>0 {buffer.copy_within(1..n+1,0);}
        if let Err(error) = status {
            self.buffer.replace(buffer);
            self.fail_accepted(error.into());
            return;
        }
        let result = match self.state.get() {
            State::Id => {
                if buffer[0] != 0x60 {
                    self.buffer.replace(buffer);
                    self.fail_accepted(ErrorCode::NODEVICE);
                    return;
                }
                self.write_register(buffer, 0xe0, 0xb6, State::Reset)
            }
            State::Reset => {
                self.wait_ms(buffer, State::ResetWait, 2);
                return;
            }
            State::CalibrationTp => {
                self.calibration_tp(buffer);
                self.read_register(buffer, 0xe1, 7, State::CalibrationHumidity)
            }
            State::CalibrationHumidity => {
                self.calibration_humidity(buffer);
                self.write_register(buffer, 0xf5, 0, State::Config)
            }
            State::Config => self.write_register(buffer, 0xf2, 1, State::HumidityConfig),
            State::HumidityConfig => {
                self.initialized.set(true);
                self.buffer.replace(buffer);
                self.restore_idle();
                return;
            }
            State::Trigger => {
                self.wait_ms(buffer, State::ConversionWait, 50);
                return;
            }
            State::ConversionStatus => {
                if buffer[0] & 0x09 != 0 {
                    // A stuck measuring/im_update flag must not cause either
                    // a stale sample callback or an unbounded BUSY state.
                    let polls = self.readiness_polls.get();
                    // Limit scheduled waits to 140 ms, leaving room within
                    // the task's 150 ms deadline for bus transactions.
                    if polls >= 18 {
                        self.buffer.replace(buffer);
                        self.fail_accepted(ErrorCode::BUSY);
                    } else {
                        self.readiness_polls.set(polls + 1);
                        self.wait_ms(buffer, State::ConversionWait, 5);
                    }
                    return;
                }
                self.read_register(buffer, 0xf7, 8, State::Sample)
            }
            State::Sample => {
                self.finish_sample(buffer);
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

impl<'a, I: SpiMasterDevice<'a>, A: Alarm<'a>> AlarmClient for Bme280<'a, I, A> {
    fn alarm(&self) {
        let (register, length, next) = match self.state.get() {
            State::ResetWait => (0x88, 26, State::CalibrationTp),
            State::ConversionWait => (0xf3, 1, State::ConversionStatus),
            _ => return,
        };
        if let Some(buffer) = self.buffer.take() {
            if let Err(error) = self.read_register(buffer, register, length, next) {
                self.fail_accepted(error);
            }
        } else {
            self.fail_accepted(ErrorCode::NOMEM);
        }
    }
}

impl<'a, I: SpiMasterDevice<'a>, A: Alarm<'a>> TemperatureDriver<'a> for Bme280<'a, I, A> {
    fn set_client(&self, client: &'a dyn TemperatureClient) {
        self.temperature_client.set(Some(client));
    }

    fn read_temperature(&self) -> Result<(), ErrorCode> {
        self.request(Channel::Temperature)
    }
}

impl<'a, I: SpiMasterDevice<'a>, A: Alarm<'a>> HumidityDriver<'a> for Bme280<'a, I, A> {
    fn set_client(&self, client: &'a dyn HumidityClient) {
        self.humidity_client.set(Some(client));
    }

    fn read_humidity(&self) -> Result<(), ErrorCode> {
        self.request(Channel::Humidity)
    }
}

impl<'a, I: SpiMasterDevice<'a>, A: Alarm<'a>> PressureDriver<'a> for Bme280<'a, I, A> {
    fn set_client(&self, client: &'a dyn PressureClient) {
        self.pressure_client.set(Some(client));
    }

    fn read_atmospheric_pressure(&self) -> Result<(), ErrorCode> {
        self.request(Channel::Pressure)
    }
}
