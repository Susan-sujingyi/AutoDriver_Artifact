//! Allocation-free request state, shared by the board application and host tests.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Outcome { Waiting, Value([u32; 3]), Timeout, Duplicate }

#[derive(Clone, Copy, Debug)]
pub struct Pending {
    pub start: u32,
    pub limit: u32,
    pub callbacks: u32,
    pub outcome: Outcome,
    pub elapsed: u32,
}

impl Pending {
    pub fn new(start: u32, limit: u32) -> Self {
        assert!(limit > 0 && limit < 0x80000000);
        Self { start, limit, callbacks: 0, outcome: Outcome::Waiting, elapsed: 0 }
    }
    pub fn callback(&mut self, now: u32, args: [u32; 3]) {
        self.callbacks += 1;
        if self.outcome == Outcome::Timeout { return; }
        if self.callbacks > 1 { self.outcome = Outcome::Duplicate; return; }
        self.elapsed = now.wrapping_sub(self.start);
        self.outcome = if self.elapsed >= self.limit { Outcome::Timeout } else { Outcome::Value(args) };
    }
    pub fn tick(&mut self, now: u32) {
        if self.outcome == Outcome::Waiting && now.wrapping_sub(self.start) >= self.limit {
            self.elapsed = now.wrapping_sub(self.start);
            self.outcome = Outcome::Timeout;
        }
    }
}

pub fn ticks(ms: u32, hz: u32) -> Option<u32> {
    let n = (u64::from(ms) * u64::from(hz)).div_ceil(1000);
    if hz == 0 || n == 0 || n >= 0x80000000 { None } else { Some(n as u32) }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test] fn zero_and_negative_values_are_data() {
        for v in [0, (-98i32) as u32, 255] {
            let mut p = Pending::new(100, 20); p.callback(101, [v, 0, 0]);
            assert_eq!(p.outcome, Outcome::Value([v, 0, 0]));
        }
    }
    #[test] fn late_callback_cannot_reopen_timeout() {
        let mut p = Pending::new(100, 20); p.tick(120); p.callback(121, [99, 0, 0]);
        assert_eq!(p.outcome, Outcome::Timeout); assert_eq!(p.callbacks, 1);
    }
    #[test] fn late_callback_before_alarm_delivery_is_timeout() {
        let mut p = Pending::new(100, 20); p.callback(121, [99, 0, 0]);
        assert_eq!(p.outcome, Outcome::Timeout);
    }
    #[test] fn missing_callback_and_wrapping_counter() {
        let mut p = Pending::new(u32::MAX - 9, 20); p.tick(9);
        assert_eq!(p.outcome, Outcome::Waiting); p.tick(10);
        assert_eq!(p.outcome, Outcome::Timeout);
    }
    #[test] fn duplicates_fail() {
        let mut p = Pending::new(0, 20); p.callback(1, [1, 2, 3]); p.callback(2, [4, 5, 6]);
        assert_eq!(p.outcome, Outcome::Duplicate);
    }
    #[test] fn conversion_uses_wide_arithmetic_and_bounded_deadlines() {
        assert_eq!(ticks(10000, 32768), Some(327680));
        assert_eq!(ticks(1, 32768), Some(33));
        assert_eq!(ticks(u32::MAX, u32::MAX), None); assert_eq!(ticks(1, 0), None);
    }
}
