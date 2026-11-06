//! Single-threaded executor for the frozen Tock HIL models.
//! Device profiles supply event sources, never their own scheduling policy.
use kernel::deferred_call::DeferredCall;
use std::cell::Cell;
use std::collections::VecDeque;
use kernel::ErrorCode;

pub(crate) struct Event<'a> {
    pub name: &'static str,
    pub next: &'a dyn Fn() -> Option<u64>,
    pub fire: &'a dyn Fn(),
}

pub(crate) const EVENT_LIMIT: usize = 50_000;

/// All deadlines use unwrapped microseconds. At ties, source order wins;
/// overdue events run at the current time without moving the clock backwards.
/// Ready hardware runs before deferred calls, as in the native kernel loop.
/// Each dispatch re-reads every deadline, so callbacks may cancel or re-arm.
pub(crate) fn drive(
    clock: &Cell<u64>, end: u64, limit: usize, sources: &[Event<'_>],
    stop: impl Fn() -> bool, tick: impl Fn(u64),
) -> usize {
    let end = end.max(clock.get());
    let mut history = VecDeque::new();
    let mut count = 0;
    loop {
        if stop() { return count; }
        let next = sources.iter().enumerate()
            .filter_map(|(i, s)| (s.next)().map(|t| (t.max(clock.get()), i)))
            .min();
        let deferred = DeferredCall::has_tasks();
        if !deferred && next.map_or(true, |(t, _)| t > end) {
            clock.set(end.max(clock.get()));
            tick(clock.get());
            return count;
        }
        if count == limit {
            panic!("NOT_COMPLETED: reason=event_budget time_us={} end_us={} events={} recent={:?}",
                   clock.get(), end, count, history);
        }
        let hardware = next.filter(|(t, _)| *t <= end && (!deferred || *t == clock.get()));
        let name = if let Some((at, index)) = hardware {
            clock.set(at);
            (sources[index].fire)();
            sources[index].name
        } else {
            assert!(deferred, "MODEL: missing executable event");
            assert!(DeferredCall::service_next_pending().is_some(),
                    "MODEL_UNSUPPORTED: deferred call has no registered client");
            "deferred_call"
        };
        tick(clock.get());
        count += 1;
        if history.len() == 16 { history.pop_front(); }
        history.push_back((clock.get(), name));
    }
}

/// Convert the native wrapping Alarm reference/delta into an unwrapped
/// deadline. A past deadline is delivered at minimum_dt, never one wrap later.
pub(crate) fn alarm_deadline(now: u64, reference: u32, delta: u32) -> u64 {
    let elapsed = (now as u32).wrapping_sub(reference);
    now.checked_add(u64::from(delta.saturating_sub(elapsed).max(1)))
        .expect("MODEL: virtual clock overflow")
}

/// Retry only requests which the native interface has not accepted. Completion
/// stays with the caller; an accepted logical request is never resubmitted.
/// The deadline is an observation resource, not a device timing requirement.
pub(crate) fn accept_request(
    clock: &Cell<u64>, deadline: u64, submit: impl Fn() -> Result<(), ErrorCode>,
    advance: impl Fn(u64), callbacks: impl Fn() -> usize,
) {
    let before = callbacks();
    let mut attempts: usize = 0;
    loop {
        attempts += 1;
        let result = submit();
        if attempts.is_power_of_two() || result.is_ok() || clock.get() >= deadline {
            println!("OBS\trequest_admission\ttime_us={} attempt={} result={:?}",
                     clock.get(), attempts, result);
        }
        match result {
            Ok(()) => return,
            Err(error) => {
                assert_eq!(callbacks(), before, "CALLBACK: synchronous completion of rejected request");
                if !matches!(error, ErrorCode::BUSY | ErrorCode::OFF) {
                    panic!("NOT_COMPLETED: reason=request_rejected time_us={} error={:?} attempts={}",
                           clock.get(), error, attempts);
                }
                if clock.get() >= deadline {
                    panic!("NOT_COMPLETED: reason=request_admission_deadline time_us={} error={:?} attempts={}",
                           clock.get(), error, attempts);
                }
                let next = clock.get().saturating_add(1000).min(deadline);
                advance(next);
                assert!(clock.get() >= next, "MODEL: admission supervisor did not advance time");
                assert_eq!(callbacks(), before, "CALLBACK: asynchronous completion of rejected request");
            }
        }
    }
}
