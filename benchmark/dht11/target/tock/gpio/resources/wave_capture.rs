// SPDX-License-Identifier: Apache-2.0
//! nRF52840 SPIM2 sampled-line acquisition. No DHT knowledge.
//! Exclusive P1.06 release, P1.07 input, P1.08 clock, TIMER2,
//! GPIOTE7, PPI14/15. These resources MUST NOT be shared during acquisition.
use core::cell::Cell;
use core::sync::atomic::{fence, Ordering};
use kernel::hil::waveform::{Capture, Client, Report, Status};
use kernel::utilities::cells::{OptionalCell, TakeCell};
use kernel::ErrorCode;
pub const SPI: usize = 0x40023000;
pub const PPI: usize = 0x4001f000;
pub const GPIOTE: usize = 0x40006000;
pub const GPIO: usize = 0x50000300; // port 1
pub const TIMER: usize = 0x4000a000; // TIMER2
const MASK: usize = (1 << 14) | (1 << 15);
const CONFIG7: usize = GPIOTE + 0x510 + 7 * 4;
pub const STARTED: usize = SPI + 0x14c;
pub const END: usize = SPI + 0x118;
pub const ENDRX: usize = SPI + 0x110;
pub const STOPPED: usize = SPI + 0x104;
/// Abstract register access permits the identical state/ownership implementation
/// to execute against a fault-injecting host register model.
pub trait Registers { fn read(&self, addr: usize) -> usize; fn write(&self, addr: usize, value: usize); }
pub struct Mmio;
impl Registers for Mmio {
    fn read(&self, a: usize) -> usize { unsafe { core::ptr::read_volatile(a as *const u32) as usize } }
    fn write(&self, a: usize, v: usize) { unsafe { core::ptr::write_volatile(a as *mut u32, v as u32) } }
}
#[derive(Clone, Copy, PartialEq, Debug)]
enum State { Idle, Active, Stopping }
pub struct Sampler<I: Registers> {
    pub io: I, state: Cell<State>, buffer: TakeCell<'static, [u8]>,
    client: OptionalCell<&'static dyn Client>, generation: Cell<u32>,
}
impl<I: Registers> Sampler<I> {
    pub fn new(io: I) -> Self { Self { io, state: Cell::new(State::Idle), buffer: TakeCell::empty(), client: OptionalCell::empty(), generation: Cell::new(0) } }
    fn free(&self) -> bool {
        if self.io.read(SPI+0x500)!=0 || self.io.read(CONFIG7)!=0 || self.io.read(PPI+0x500)&MASK!=0 { return false; }
        for ch in [14,15] {
            if self.io.read(PPI+0x510+ch*8)!=0 || self.io.read(PPI+0x514+ch*8)!=0 || self.io.read(PPI+0x910+ch*4)!=0 { return false; }
            for group in 0..6 { if self.io.read(PPI+0x800+group*4)&(1<<ch)!=0 {return false;} }
        }
        true
    }
    fn cleanup(&self) {
        self.io.write(SPI+0x308, usize::MAX);
        self.io.write(PPI+0x508, MASK);
        self.io.write(SPI+0x500, 0); // only after END or STOPPED proves DMA finished
        self.io.write(TIMER+0x004, 1);
        // Set GPIO's fallback latch high before disconnecting GPIOTE.
        self.io.write(GPIO+0x508,1<<6);
        self.io.write(CONFIG7,0);
        self.io.write(GPIO+0x700+6*4,0); // released input, no pull
        for ch in [14,15] {
            self.io.write(PPI+0x510+ch*8,0);self.io.write(PPI+0x514+ch*8,0);self.io.write(PPI+0x910+ch*4,0);
        }
        for event in [STARTED,END,ENDRX,STOPPED,SPI+0x120] {self.io.write(event,0);}
    }
    pub fn handle_interrupt(&self) {
        let state=self.state.get();
        if state==State::Idle {return;}
        let end=self.io.read(END)!=0;
        let stopped=self.io.read(STOPPED)!=0;
        // ENDRX can be raised on STOP even for partial buffers. END alone while
        // cancelling does not permit disable: wait for STOPPED as specified.
        if state==State::Stopping && !stopped {return;}
        if state==State::Active && !end && !stopped {return;}
        let amount=self.io.read(SPI+0x53c);
        let elapsed=(self.io.read(TIMER+0x544) as u32).wrapping_sub(self.io.read(TIMER+0x540) as u32);
        let started=self.io.read(STARTED)!=0;
        let received=self.io.read(ENDRX)!=0;
        let len=self.buffer.map_or(0,|b|b.len());
        let status=if state==State::Stopping {Status::Cancelled}
            else if !end || !started || !received {Status::Hardware}
            else if amount!=len {Status::Short}
            else if elapsed.abs_diff((len*8)as u32)>32 {Status::Timing}
            else {Status::Complete};
        // Stop state is visible before any callback; buffers cannot be reused
        // until the DMA has stopped. All stale events cleared before reentry.
        self.cleanup();fence(Ordering::SeqCst);
        let buffer=self.buffer.take();self.state.set(State::Idle);
        if let Some(buffer)=buffer {self.client.map(move |client|client.captured(buffer,Report {
            status,bytes:amount,sample_period_ns:1000,elapsed_us:elapsed,generation:self.generation.get(),
        }));}
    }
}
impl<I: Registers> Capture for Sampler<I> {
    fn set_client(&self,c:&'static dyn Client){if self.state.get()==State::Idle {self.client.set(c);}}
    fn busy(&self)->bool{self.state.get()!=State::Idle}
    fn start(&self,b:&'static mut [u8])->Result<(),(ErrorCode,&'static mut [u8])>{
        if self.busy(){return Err((ErrorCode::BUSY,b));}
        if self.client.is_none(){return Err((ErrorCode::OFF,b));}
        if b.len()<2 || b.len()>2048 {return Err((ErrorCode::SIZE,b));}
        if !self.free(){return Err((ErrorCode::BUSY,b));}
        // No restart shortcut/list mode: exactly one fixed DMA buffer.
        self.io.write(SPI+0x200,0);self.io.write(SPI+0x308,usize::MAX);
        for event in [STARTED,END,ENDRX,STOPPED,SPI+0x120] {self.io.write(event,0);}
        self.io.write(SPI+0x540,0);self.io.write(SPI+0x550,0);
        self.io.write(GPIO+0x700+6*4,1|(6<<8)); // output, open drain S0D1, input connected
        self.io.write(GPIO+0x700+7*4,0); // MISO input
        self.io.write(GPIO+0x50c,1<<8);self.io.write(GPIO+0x700+8*4,1); // spare SCK output
        // GPIOTE task output starts low, rises only at hardware STARTED.
        self.io.write(GPIOTE+0x308,1<<7);self.io.write(GPIOTE+0x100+7*4,0);
        self.io.write(CONFIG7,3|(38<<8));
        self.io.write(SPI+0x508,40);self.io.write(SPI+0x50c,1<<31);self.io.write(SPI+0x510,39);
        self.io.write(SPI+0x524,0x10000000); // 1 MHz
        self.io.write(SPI+0x554,0); // mode 0, MSB first
        self.io.write(SPI+0x544,0);self.io.write(SPI+0x548,0);self.io.write(SPI+0x5c0,0xff);
        self.io.write(SPI+0x534,b.as_mut_ptr() as usize);self.io.write(SPI+0x538,b.len());
        // Separate free-running timer: no software service needed while sampling.
        self.io.write(TIMER+0x004,1);self.io.write(TIMER+0x308,usize::MAX);self.io.write(TIMER+0x200,0);
        self.io.write(TIMER+0x504,0);self.io.write(TIMER+0x508,3);self.io.write(TIMER+0x510,4);
        self.io.write(TIMER+0x00c,1);self.io.write(TIMER+0x000,1);
        self.io.write(PPI+0x510+15*8,STARTED);self.io.write(PPI+0x514+15*8,GPIOTE+0x04c);
        self.io.write(PPI+0x910+15*4,TIMER+0x040);
        self.io.write(PPI+0x510+14*8,END);self.io.write(PPI+0x514+14*8,TIMER+0x044);
        self.io.write(PPI+0x504,MASK);
        self.buffer.replace(b);self.generation.set(self.generation.get().wrapping_add(1));self.state.set(State::Active);
        fence(Ordering::SeqCst);
        self.io.write(SPI+0x304,(1<<6)|(1<<1)); // END, STOPPED
        self.io.write(SPI+0x500,7);
        self.io.write(SPI+0x010,1);
        Ok(())
    }
    fn cancel(&self)->Result<(),ErrorCode>{
        if self.state.get()==State::Idle{return Err(ErrorCode::OFF);}
        if self.state.get()==State::Stopping{return Err(ErrorCode::BUSY);}
        self.state.set(State::Stopping);self.io.write(SPI+0x014,1);Ok(())
    }
}
