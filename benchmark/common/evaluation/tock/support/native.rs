//! Shared native-HIL asynchronous bus, virtual alarm, event budget and case reporting.
//! Register semantics and numerical obligations remain in each device profile.
use kernel::hil::i2c;
use kernel::hil::time::{Alarm, AlarmClient, Freq1MHz, Ticks, Ticks32, Time};
use kernel::ErrorCode;
use std::cell::{Cell, RefCell};
use std::panic::{catch_unwind, AssertUnwindSafe};
const BUS_US: u64 = 100;

pub(crate) trait DeviceModel {
    fn exchange(&mut self, now: u64, tx: &[u8], read_len: usize, buffer: &mut [u8]);
    fn fault_matches(&self, at: FaultAt, tx: &[u8], read_len: usize) -> bool;
    fn faulted(&mut self, _now: u64, _fault: Fault) {}
    // A documented device response (e.g. conversion-busy NACK), distinct from
    // injected transport faults. Existing profiles retain successful exchange.
    fn response_error(&mut self, _now: u64, _tx: &[u8], _read_len: usize) -> Option<i2c::Error> {
        None
    }
}
#[derive(Clone, Copy, Debug)]
pub(crate) enum FaultAt {
    Any,
    Data,
    Configuration,
}
#[derive(Clone, Copy, Debug)]
pub(crate) struct Fault {
    pub(crate) at: FaultAt,
    pub(crate) asynchronous: bool,
}
pub(crate) struct Pending {
    pub(crate) buffer: &'static mut [u8],
    pub(crate) tx: Vec<u8>,
    pub(crate) read_len: usize,
    pub(crate) due: u64,
    pub(crate) fail: bool,
}
pub(crate) struct FakeI2c<M: DeviceModel + 'static> {
    pub(crate) clock: &'static Cell<u64>,
    pub(crate) model: &'static RefCell<M>,
    pub(crate) pending: RefCell<Option<Pending>>,
    pub(crate) client: Cell<Option<&'static dyn i2c::I2CClient>>,
    pub(crate) fault: Cell<Option<Fault>>,
    pub(crate) fault_hit: Cell<bool>,
    pub(crate) enabled: Cell<bool>,
}
impl<M: DeviceModel + 'static> FakeI2c<M> {
    fn submit(
        &self,
        buffer: &'static mut [u8],
        write_len: usize,
        read_len: usize,
    ) -> Result<(), (i2c::Error, &'static mut [u8])> {
        if self.pending.borrow().is_some() {
            return Err((i2c::Error::Busy, buffer));
        }
        assert!(
            write_len <= buffer.len() && read_len <= buffer.len(),
            "PROTOCOL: I2C operation exceeds owned buffer"
        );
        assert!(
            self.enabled.get(),
            "PROTOCOL: I2C operation without enabling the device"
        );
        let tx = buffer[..write_len].to_vec();
        println!("OBS\ti2c_submit\ttime_us={} tx={:02x?} read_len={} buffer={:p}",
                 self.clock.get(), tx, read_len, buffer.as_ptr());
        let hit = self
            .fault
            .get()
            .filter(|f| self.model.borrow().fault_matches(f.at, &tx, read_len));
        if let Some(fault) = hit {
            self.fault.set(None);
            self.fault_hit.set(true);
            self.model.borrow_mut().faulted(self.clock.get(), fault);
            if !fault.asynchronous {
                return Err((i2c::Error::AddressNak, buffer));
            }
        }
        self.pending.replace(Some(Pending {
            buffer,
            tx,
            read_len,
            due: self.clock.get() + BUS_US,
            fail: hit.is_some(),
        }));
        Ok(())
    }
    pub(crate) fn complete(&self) {
        let pending = self
            .pending
            .borrow_mut()
            .take()
            .expect("missing pending transaction");
        self.clock.set(self.clock.get().max(pending.due));
        let error = if pending.fail {
            Some(i2c::Error::AddressNak)
        } else {
            self.model.borrow_mut().response_error(
                self.clock.get(), &pending.tx, pending.read_len,
            )
        };
        if error.is_none() {
            self.model.borrow_mut().exchange(
                self.clock.get(),
                &pending.tx,
                pending.read_len,
                pending.buffer,
            );
        }
        println!("OBS\ti2c_complete\ttime_us={} error={:?} buffer={:p}",
                 self.clock.get(), error, pending.buffer.as_ptr());
        self.client
            .get()
            .expect("I2C client was not registered")
            .command_complete(
                pending.buffer,
                error.map_or(Ok(()), Err),
            );
    }
}
impl<M: DeviceModel + 'static> i2c::I2CDevice for FakeI2c<M> {
    fn enable(&self) {
        self.enabled.set(true);
    }
    fn disable(&self) {
        self.enabled.set(false);
    }
    fn write_read(
        &self,
        b: &'static mut [u8],
        w: usize,
        r: usize,
    ) -> Result<(), (i2c::Error, &'static mut [u8])> {
        self.submit(b, w, r)
    }
    fn write(&self, b: &'static mut [u8], n: usize) -> Result<(), (i2c::Error, &'static mut [u8])> {
        self.submit(b, n, 0)
    }
    fn read(&self, b: &'static mut [u8], n: usize) -> Result<(), (i2c::Error, &'static mut [u8])> {
        self.submit(b, 0, n)
    }
}
pub(crate) struct FakeAlarm {
    pub(crate) clock: &'static Cell<u64>,
    pub(crate) due: Cell<Option<u64>>,
    pub(crate) client: Cell<Option<&'static dyn AlarmClient>>,
}
impl Time for FakeAlarm {
    type Frequency = Freq1MHz;
    type Ticks = Ticks32;
    fn now(&self) -> Ticks32 {
        Ticks32::from(self.clock.get() as u32)
    }
}
impl Alarm<'static> for FakeAlarm {
    fn set_alarm_client(&self, c: &'static dyn AlarmClient) {
        self.client.set(Some(c));
    }
    fn set_alarm(&self, reference: Ticks32, dt: Ticks32) {
        let due = crate::executor::alarm_deadline(
            self.clock.get(), reference.into_u32(), dt.into_u32());
        self.due.set(Some(due));
    }
    fn get_alarm(&self) -> Ticks32 {
        Ticks32::from(self.due.get().unwrap_or(self.clock.get()) as u32)
    }
    fn disarm(&self) -> Result<(), ErrorCode> {
        self.due.set(None);
        Ok(())
    }
    fn is_armed(&self) -> bool {
        self.due.get().is_some()
    }
    fn minimum_dt(&self) -> Ticks32 {
        Ticks32::from(1)
    }
}
impl FakeAlarm {
    pub(crate) fn fire(&self) {
        let due = self.due.take().expect("no alarm");
        self.clock.set(self.clock.get().max(due));
        self.client.get().expect("alarm client missing").alarm();
    }
}

