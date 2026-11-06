// Licensed under the Apache License, Version 2.0 or the MIT License.
// SPDX-License-Identifier: Apache-2.0 OR MIT
//! Fixed resource wiring, without sensor protocol or conversion logic.
use core::mem::MaybeUninit;
use kernel::component::Component;
use kernel::hil::{gpio,time};
use capsules_extra::dht11::Dht11;
#[macro_export]
macro_rules! dht11_component_static {
    ($P:ty,$A:ty $(,)?) => {kernel::static_buf!(capsules_extra::dht11::Dht11<'static,$P,$A>)};
}
pub struct Dht11Component<P:'static+gpio::InterruptPin<'static>,A:'static+time::Alarm<'static>> {
    pin:&'static P,alarm:&'static A,sampler:&'static dyn kernel::hil::waveform::Capture,buffer:&'static mut [u8],
}
impl<P:'static+gpio::InterruptPin<'static>,A:'static+time::Alarm<'static>> Dht11Component<P,A>{
    pub fn new(pin:&'static P,alarm:&'static A,sampler:&'static dyn kernel::hil::waveform::Capture,buffer:&'static mut [u8])->Self{Self{pin,alarm,sampler,buffer}}
}
impl<P:'static+gpio::InterruptPin<'static>,A:'static+time::Alarm<'static>> Component for Dht11Component<P,A>{
    type StaticInput=&'static mut MaybeUninit<Dht11<'static,P,A>>;
    type Output=&'static Dht11<'static,P,A>;
    fn finalize(self,s:Self::StaticInput)->Self::Output{
        let d=s.write(Dht11::new(self.pin,self.alarm));
        d.set_sampler(self.sampler,self.buffer);self.pin.set_client(d);self.alarm.set_alarm_client(d);let _=d.startup();d
    }
}
