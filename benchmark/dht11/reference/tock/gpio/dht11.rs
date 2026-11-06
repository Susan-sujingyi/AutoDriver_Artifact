// SPDX-License-Identifier: Apache-2.0
//! Hidden reference control. Not an agent input or numeric oracle.
use core::cell::Cell;
use kernel::hil::gpio::{self, InterruptPin};
use kernel::hil::sensors::{TemperatureClient, TemperatureDriver, HumidityClient, HumidityDriver};
use kernel::hil::time::{Alarm, AlarmClient, Frequency, Ticks};
use kernel::ErrorCode;
use kernel::hil::waveform::{Capture, Client, Report, Status};
use kernel::utilities::cells::{OptionalCell, TakeCell};

const START_US: u32 = 19000;
const THRESHOLD_US: u32 = 50;
const GAP_US: u32 = 1100000;
#[derive(Clone, Copy, PartialEq)]
enum State { Off, PowerWait, Ready, Cooldown, Start, Receiving }
pub struct Dht11<'a, P: InterruptPin<'a>, A: Alarm<'a>> {
    sampler: OptionalCell<&'static dyn Capture>, rx: TakeCell<'static, [u8]>,
    pin: &'a P, alarm: &'a A, state: Cell<State>, pending: Cell<Option<bool>>,
    bits: Cell<u8>, frame: Cell<[u8;5]>, last: Cell<Option<A::Ticks>>,
    temp: Cell<Option<&'a dyn TemperatureClient>>, humidity: Cell<Option<&'a dyn HumidityClient>>,
}
impl<'a,P:InterruptPin<'a>,A:Alarm<'a>> Dht11<'a,P,A> {
    pub fn new(pin:&'a P, alarm:&'a A)->Self {
        Self {sampler:OptionalCell::empty(),rx:TakeCell::empty(),pin,alarm,state:Cell::new(State::Off),pending:Cell::new(None),bits:Cell::new(0),frame:Cell::new([0;5]),
            last:Cell::new(None),temp:Cell::new(None),humidity:Cell::new(None)}
    }
    /// Board binding: set before startup; buffer is owned by the capture service while active.
    pub fn set_sampler(&'static self, sampler:&'static dyn Capture, buffer:&'static mut [u8]) {
        assert!(self.state.get()==State::Off && self.rx.is_none());
        self.rx.replace(buffer);self.sampler.set(sampler);sampler.set_client(self);
    }
    fn receive_frame(&self) {
        self.pin.disable_interrupts();let _=self.alarm.disarm();
        self.state.set(State::Receiving);
        if let Some(buffer)=self.rx.take() {
            if let Some(sampler)=self.sampler.get() {
                match sampler.start(buffer) {
                    Ok(())=>{self.wait(30000);return;},
                    Err((_,buffer))=>{self.rx.replace(buffer);},
                }
            } else {self.rx.replace(buffer);}
        }
        self.finish(false);
    }
    pub fn startup(&self)->Result<(),ErrorCode> {
        if self.state.get()!=State::Off {return Err(ErrorCode::BUSY);}
        if self.sampler.is_none() || self.rx.is_none() {return Err(ErrorCode::OFF);}
        if A::Frequency::frequency()<1000000 {return Err(ErrorCode::NOSUPPORT);}
        self.pin.disable_interrupts();self.pin.make_input();self.pin.set_floating_state(gpio::FloatingState::PullNone);
        self.state.set(State::PowerWait);self.wait(1100000);Ok(())
    }
    fn ticks(us:u32)->A::Ticks {A::Ticks::from_or_max((us as u64*A::Frequency::frequency()as u64+999999)/1000000)}
    fn us(&self,t:A::Ticks)->u32 {(self.alarm.now().wrapping_sub(t).into_u32()as u64*1000000/A::Frequency::frequency()as u64)as u32}
    fn wait(&self,us:u32){self.alarm.set_alarm(self.alarm.now(),Self::ticks(us).max(self.alarm.minimum_dt()));}
    fn begin(&self){
        self.pin.disable_interrupts();self.frame.set([0;5]);self.bits.set(0);
        self.pin.clear();self.pin.make_output();self.pin.clear();self.state.set(State::Start);self.wait(START_US);
    }
    fn request(&self,humidity:bool)->Result<(),ErrorCode>{
        match self.state.get(){State::Off|State::PowerWait=>return Err(ErrorCode::OFF),State::Ready=>(),_=>return Err(ErrorCode::BUSY)}
        self.pending.set(Some(humidity));
        if let Some(last)=self.last.get(){let elapsed=self.us(last);if elapsed<GAP_US{self.state.set(State::Cooldown);self.wait(GAP_US-elapsed);return Ok(());}}
        self.begin();Ok(())
    }
    fn finish(&self,valid:bool){
        self.pin.disable_interrupts();self.pin.make_input();let _=self.alarm.disarm();
        self.last.set(Some(self.alarm.now()));self.state.set(State::Ready);
        let pending=self.pending.take();let b=self.frame.get();
        match pending {
            Some(false)=>if let Some(c)=self.temp.get(){c.callback(if valid{Ok(b[2]as i32*100)}else{Err(ErrorCode::FAIL)});},
            Some(true)=>if valid {if let Some(c)=self.humidity.get(){c.callback(b[0]as usize*100);}},
            None=>(),
        }
        // This frozen HumidityClient has no error callback; do not invent a valid
        // humidity value on transport failure. Fault reporting is diagnostic-only.
    }
}
impl<'a,P:InterruptPin<'a>,A:Alarm<'a>> AlarmClient for Dht11<'a,P,A>{
    fn alarm(&self){match self.state.get(){
        State::PowerWait=>self.state.set(State::Ready),
        State::Cooldown=>self.begin(),
        State::Start=>self.receive_frame(),
        State::Receiving=>{self.sampler.map(|s|{let _=s.cancel();});},
        _=>(),
    }}
}
impl<'a,P:InterruptPin<'a>,A:Alarm<'a>> gpio::Client for Dht11<'a,P,A>{fn fired(&self){}}
impl<'a,P:InterruptPin<'a>,A:Alarm<'a>> Client for Dht11<'a,P,A>{
    fn captured(&self, buffer:&'static mut [u8], report:Report) {
        if self.state.get()!=State::Receiving {return;}
        let frame=if report.status==Status::Complete && report.bytes==buffer.len()
            && report.sample_period_ns==1000 {decode(buffer)}else{None};
        self.rx.replace(buffer);
        if let Some(bytes)=frame {self.frame.set(bytes);self.bits.set(40);}
        self.finish(frame.is_some());
    }
}
impl<'a,P:InterruptPin<'a>,A:Alarm<'a>> TemperatureDriver<'a> for Dht11<'a,P,A>{
    fn set_client(&self,c:&'a dyn TemperatureClient){self.temp.set(Some(c));}
    fn read_temperature(&self)->Result<(),ErrorCode>{self.request(false)}
}
impl<'a,P:InterruptPin<'a>,A:Alarm<'a>> HumidityDriver<'a> for Dht11<'a,P,A>{
    fn set_client(&self,c:&'a dyn HumidityClient){self.humidity.set(Some(c));}
    fn read_humidity(&self)->Result<(),ErrorCode>{self.request(true)}
}

// Pure bounded protocol decoding after asynchronous capture. No GPIO polling.
fn decode(data:&[u8])->Option<[u8;5]> {
    if data.is_empty() || data.len()>2048 {return None;}
    let n=data.len()*8;
    let bit=|i:usize|data[i/8]&(0x80>>(i%8))!=0;
    let next=|pos:&mut usize, level:bool|->Option<()> {
        let start=*pos;
        while *pos<n {
            if *pos-start>=150 {return None;}
            if bit(*pos)==level{return Some(());}
            *pos+=1;
        }
        None
    };
    let mut pos=0;let mut b=[0u8;5];
    for level in [true,false,true,false]{next(&mut pos,level)?;}
    for i in 0..40 {
        next(&mut pos,true)?;let rise=pos;next(&mut pos,false)?;
        b[i/8]=(b[i/8]<<1)|u8::from(pos-rise>=THRESHOLD_US as usize);
    }
    next(&mut pos,true)?;
    if (pos..n).any(|i|!bit(i)){return None;}
    if b[..4].iter().fold(0u8,|a,x|a.wrapping_add(*x))==b[4]{Some(b)}else{None}
}
