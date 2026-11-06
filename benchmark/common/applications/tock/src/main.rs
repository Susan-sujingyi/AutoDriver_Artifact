#![no_std]
#![no_main]

extern crate libtock_small_panic;
use core::{cell::Cell, fmt::Write};
use libtock_console::Console;
use libtock_platform::{share, subscribe::OneId, DefaultConfig, ErrorCode, Subscribe, Syscalls, Upcall};
use libtock_runtime::{set_main, stack_size, TockSyscalls as S};
mod protocol;
use protocol::{Outcome, Pending};

// A channel describes the existing public Tock syscall ABI, not a device formula.
struct Channel { name: &'static str, driver: u32, command: u32, unit: &'static str, kind: u8 }
include!(concat!(env!("OUT_DIR"), "/profile.rs"));
set_main! {main}
stack_size! {8192}

fn clock() -> Result<u32, ErrorCode> { S::command(0, 2, 0, 0).to_result() }
fn stop_alarm() { let _ = S::command(0, 3, 0, 0); }
fn delay(ms: u32, hz: u32) -> Result<(), ErrorCode> {
    let limit = protocol::ticks(ms, hz).ok_or(ErrorCode::Invalid)?;
    let alarm: Cell<Option<(u32, u32)>> = Cell::new(None);
    share::scope::<Subscribe<S, 0, 0>, _, _>(|handle| {
        S::subscribe::<_, _, DefaultConfig, 0, 0>(handle, &alarm)?;
        S::command(0, 5, limit, 0).to_result::<u32, ErrorCode>()?;
        while alarm.get().is_none() { S::yield_wait(); }
        Ok(())
    })
}

struct Listener<'a>(&'a Cell<Pending>);
impl<const D: u32> Upcall<OneId<D, 0>> for Listener<'_> {
    fn upcall(&self, a: u32, b: u32, c: u32) {
        let mut p = self.0.get();
        // A clock failure cannot yield a successful reading.
        let now = clock().unwrap_or(p.start.wrapping_add(p.limit));
        p.callback(now, [a, b, c]); self.0.set(p);
    }
}

struct Trace { seq: u32 }
impl Trace {
    fn emit(&mut self, args: core::fmt::Arguments) {
        // Console is synchronous; host collection also has a deadline in case
        // the kernel console or alarm itself fails to deliver an upcall.
        let mut w = Console::<S>::writer();
        write!(w, "ADBHW1 {{\"seq\":{},", self.seq).unwrap();
        w.write_fmt(args).unwrap(); writeln!(w, "}}").unwrap(); self.seq += 1;
    }
}

fn sample<const D: u32>(ch: &Channel, id: u32, hz: u32, trace: &mut Trace) -> bool {
    let limit = match protocol::ticks(REQUEST_TIMEOUT_MS, hz) { Some(n) => n, None => return false };
    let start = match clock() { Ok(n) => n, Err(_) => return false };
    let pending = Cell::new(Pending::new(start, limit));
    let listener = Listener(&pending);
    let alarm: Cell<Option<(u32, u32)>> = Cell::new(None);
    trace.emit(format_args!("\"event\":\"REQUEST\",\"id\":{},\"channel\":\"{}\",\"tick\":{}", id, ch.name, start));
    let result = share::scope::<(Subscribe<S, D, 0>, Subscribe<S, 0, 0>), Result<(), ErrorCode>, _>(|handle| {
        let (sensor, timer) = handle.split();
        S::subscribe::<_, _, DefaultConfig, D, 0>(sensor, &listener)?;
        S::subscribe::<_, _, DefaultConfig, 0, 0>(timer, &alarm)?;
        S::command(0, 5, limit, 0).to_result::<u32, ErrorCode>()?;
        S::command(D, ch.command, 0, 0).to_result::<(), ErrorCode>()?;
        trace.emit(format_args!("\"event\":\"ACCEPT\",\"id\":{}", id));
        loop {
            let mut p = pending.get(); p.tick(clock()?); pending.set(p);
            if p.outcome != Outcome::Waiting { break; }
            S::yield_wait();
        }
        // Observe already queued duplicate upcalls, without unbounded draining.
        for _ in 0..4 { S::yield_no_wait(); }
        Ok(())
    });
    stop_alarm();
    if let Err(e) = result {
        trace.emit(format_args!("\"event\":\"RESULT\",\"id\":{},\"status\":\"request_error\",\"error\":{}", id, e as u32));
        return false;
    }
    let p = pending.get();
    match p.outcome {
        Outcome::Value(a) => {
            if ch.kind == 3 && a[0] != 0 {
                trace.emit(format_args!("\"event\":\"RESULT\",\"id\":{},\"status\":\"callback_error\",\"error\":{},\"callbacks\":{},\"elapsed_ticks\":{}", id, a[0], p.callbacks, p.elapsed));
                return false;
            }
            let values: [i64; 3] = match ch.kind {
                1 => [a[0] as i32 as i64, 0, 0],
                2 => [a[0] as i32 as i64, a[1] as i32 as i64, a[2] as i32 as i64],
                3 => [a[1] as i64, 0, 0],
                _ => [a[0] as i64, 0, 0],
            };
            trace.emit(format_args!("\"event\":\"RESULT\",\"id\":{},\"status\":\"value\",\"callbacks\":{},\"elapsed_ticks\":{},\"unit\":\"{}\",\"dimensions\":{},\"values\":[{},{},{}],\"raw_upcall\":[{},{},{}]", id, p.callbacks, p.elapsed, ch.unit, if ch.kind == 2 {3} else {1}, values[0], values[1], values[2], a[0], a[1], a[2]));
            true
        },
        _ => {
            trace.emit(format_args!("\"event\":\"RESULT\",\"id\":{},\"status\":\"{}\",\"callbacks\":{},\"elapsed_ticks\":{}", id, if p.outcome == Outcome::Duplicate {"duplicate_callback"} else {"timeout"}, p.callbacks, p.elapsed));
            false // Never issue another request after an ambiguous timeout.
        }
    }
}

fn main() {
    let mut trace = Trace { seq: 0 };
    let hz = S::command(0, 1, 0, 0).to_result::<u32, ErrorCode>().unwrap_or(0);
    trace.emit(format_args!("\"event\":\"BEGIN\",\"app_id\":\"{}\",\"profile_id\":\"{}\",\"device\":\"{}\",\"rounds\":{},\"channels\":{},\"hz\":{}", APP_ID, PROFILE_ID, DEVICE, ROUNDS, CHANNELS.len(), hz));
    let mut ok = hz != 0 && delay(STARTUP_MS, hz).is_ok();
    let mut attempted = 0;
    'sampling: for round in 0..ROUNDS {
        if !ok { break; }
        for ch in CHANNELS {
            attempted += 1;
            ok = match ch.driver {
                0x60000 => sample::<0x60000>(ch, attempted, hz, &mut trace),
                0x60001 => sample::<0x60001>(ch, attempted, hz, &mut trace),
                0x60002 => sample::<0x60002>(ch, attempted, hz, &mut trace),
                0x60004 => sample::<0x60004>(ch, attempted, hz, &mut trace),
                0x60005 => sample::<0x60005>(ch, attempted, hz, &mut trace),
                0x60007 => sample::<0x60007>(ch, attempted, hz, &mut trace),
                0x60008 => sample::<0x60008>(ch, attempted, hz, &mut trace),
                _ => false,
            };
            if !ok { break 'sampling; }
        }
        if round + 1 < ROUNDS { ok = delay(INTERVAL_MS, hz).is_ok(); }
    }
    trace.emit(format_args!("\"event\":\"END\",\"status\":\"{}\",\"attempted\":{}", if ok {"completed"} else {"stopped_on_error"}, attempted));
}
