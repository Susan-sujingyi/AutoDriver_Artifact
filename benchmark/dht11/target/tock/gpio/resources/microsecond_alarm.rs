// Licensed under the Apache License, Version 2.0 or the MIT License.
// SPDX-License-Identifier: Apache-2.0 OR MIT
// This addition is applied inside nrf5x::timer by the task's board resource patch.
// General-purpose, free-running 1 MHz alarm; no sensor framing or decoding.
/// Exclusive timer instance. CC0 captures time and CC1 schedules an alarm.
/// The peripheral remains running across alarms, so GPIO clients can timestamp edges.
pub struct MicrosecondAlarm<'a> {
    registers: StaticRef<TimerRegisters>,
    client: OptionalCell<&'a dyn hil::time::AlarmClient>,
}
impl<'a> MicrosecondAlarm<'a> {
    pub const fn new(instance: usize) -> Self {
        Self { registers: INSTANCES[instance], client: OptionalCell::empty() }
    }
    /// Called once by board wiring before exposing this timer to clients.
    pub fn start(&self) {
        let r = &*self.registers;
        r.tasks_stop.write(Task::ENABLE::SET);
        r.intenclr.set(0xffff_ffff);
        r.shorts.set(0);
        r.mode.set(0); // Timer mode, not event-counter mode.
        r.bitmode.write(Bitmode::BITMODE::Bit32);
        r.prescaler.set(4); // Nordic TIMER base 16 MHz / 2^4 = 1 MHz.
        for event in &r.events_compare { event.write(Event::READY::CLEAR); }
        r.tasks_clear.write(Task::ENABLE::SET);
        r.tasks_start.write(Task::ENABLE::SET);
    }
    pub fn handle_interrupt(&self) {
        let r = &*self.registers;
        if r.events_compare[CC_COMPARE].is_set(Event::READY) {
            let armed = self.is_armed();
            r.intenclr.write(Inte::COMPARE1::SET);
            r.events_compare[CC_COMPARE].write(Event::READY::CLEAR);
            if armed { self.client.map(|c| c.alarm()); }
        }
    }
}
impl Time for MicrosecondAlarm<'_> {
    type Frequency = hil::time::Freq1MHz;
    type Ticks = hil::time::Ticks32;
    fn now(&self) -> Self::Ticks {
        self.registers.tasks_capture[CC_CAPTURE].write(Task::ENABLE::SET);
        Self::Ticks::from(self.registers.cc[CC_CAPTURE].get())
    }
}
impl<'a> Alarm<'a> for MicrosecondAlarm<'a> {
    fn set_alarm_client(&self, c: &'a dyn hil::time::AlarmClient) { self.client.set(c); }
    fn set_alarm(&self, reference: Self::Ticks, dt: Self::Ticks) {
        let r = &*self.registers;
        r.intenclr.write(Inte::COMPARE1::SET);
        r.events_compare[CC_COMPARE].write(Event::READY::CLEAR);
        let now = self.now();
        let mut end = reference.wrapping_add(dt);
        if !now.within_range(reference, end) || end.wrapping_sub(now).into_u32() < 10 {
            end = now.wrapping_add(Self::Ticks::from(10));
        }
        r.cc[CC_COMPARE].set(end.into_u32());
        r.intenset.write(Inte::COMPARE1::SET);
        // Close the race where setup crossed the compare value. Clearing a late
        // compare is safe only while disabled; re-arm ahead of the current time.
        let later = self.now();
        if !later.within_range(now, end) && !r.events_compare[CC_COMPARE].is_set(Event::READY) {
            r.intenclr.write(Inte::COMPARE1::SET);
            r.cc[CC_COMPARE].set(later.wrapping_add(Self::Ticks::from(10)).into_u32());
            r.intenset.write(Inte::COMPARE1::SET);
        }
    }
    fn get_alarm(&self) -> Self::Ticks { Self::Ticks::from(self.registers.cc[CC_COMPARE].get()) }
    fn disarm(&self) -> Result<(), ErrorCode> {
        self.registers.intenclr.write(Inte::COMPARE1::SET);
        self.registers.events_compare[CC_COMPARE].write(Event::READY::CLEAR);
        Ok(())
    }
    fn is_armed(&self) -> bool { self.registers.intenset.is_set(Inte::COMPARE1) }
    fn minimum_dt(&self) -> Self::Ticks { Self::Ticks::from(10) }
}
