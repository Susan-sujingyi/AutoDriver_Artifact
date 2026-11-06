//! Finite native model of frozen SpiMasterDevice: one outstanding transaction.
//! No mux arbitration, electrical SPI errors or all interrupt schedules proved.
use kernel::hil::spi::{ClockPhase, ClockPolarity, SpiMasterClient, SpiMasterDevice};
use kernel::ErrorCode;
use std::cell::{Cell, RefCell};
use crate::support::{Fault, FaultAt};

pub(crate) trait SpiDeviceModel {
    fn exchange(&mut self, now: u64, tx: &[u8], rx: &mut [u8]);
    fn fault_matches(&self, at: FaultAt, tx: &[u8]) -> bool;
    fn validate_bus(&self, polarity: ClockPolarity, phase: ClockPhase, rate: u32);
}
pub(crate) struct PendingSpi {
    pub(crate) tx: &'static mut [u8], pub(crate) rx: Option<&'static mut [u8]>,
    pub(crate) length: usize, pub(crate) due: u64, fail: bool,
}
pub(crate) struct FakeSpi<M: SpiDeviceModel + 'static> {
    pub(crate) clock: &'static Cell<u64>, pub(crate) model: &'static RefCell<M>,
    pub(crate) pending: RefCell<Option<PendingSpi>>, pub(crate) client: Cell<Option<&'static dyn SpiMasterClient>>,
    pub(crate) fault: Cell<Option<Fault>>, pub(crate) fault_hit: Cell<bool>,
    polarity: Cell<ClockPolarity>, phase: Cell<ClockPhase>, rate: Cell<u32>,
}
impl<M: SpiDeviceModel + 'static> FakeSpi<M> {
    pub(crate) fn new(clock: &'static Cell<u64>, model: &'static RefCell<M>) -> Self {
        Self {clock, model, pending:RefCell::new(None), client:Cell::new(None),
            fault:Cell::new(None), fault_hit:Cell::new(false),
            polarity:Cell::new(ClockPolarity::IdleLow), phase:Cell::new(ClockPhase::SampleLeading), rate:Cell::new(100_000)}
    }
    fn configurable(&self) -> Result<(),ErrorCode> {
        if self.pending.borrow().is_some() {Err(ErrorCode::BUSY)} else {Ok(())}
    }
    pub(crate) fn complete(&self) {
        let mut p=self.pending.borrow_mut().take().expect("missing pending SPI");
        self.clock.set(self.clock.get().max(p.due));
        if !p.fail {
            let mut rx=vec![0;p.length];
            self.model.borrow_mut().exchange(self.clock.get(),&p.tx[..p.length],&mut rx);
            if let Some(ref mut b)=p.rx {b[..p.length].copy_from_slice(&rx);}
        }
        println!("OBS\tspi_complete\ttime_us={} failed={} length={} tx={:p}",
                 self.clock.get(), p.fail, p.length, p.tx.as_ptr());
        self.client.get().expect("SPI client missing").read_write_done(p.tx,p.rx,p.length,
            if p.fail {Err(ErrorCode::FAIL)} else {Ok(())});
    }
}
impl<M: SpiDeviceModel + 'static> SpiMasterDevice<'static> for FakeSpi<M> {
    fn set_client(&self,c:&'static dyn SpiMasterClient){self.client.set(Some(c));}
    fn configure(&self,p:ClockPolarity,h:ClockPhase,r:u32)->Result<(),ErrorCode>{
        self.configurable()?;if r==0{return Err(ErrorCode::INVAL)}
        self.polarity.set(p);self.phase.set(h);self.rate.set(r);Ok(())
    }
    fn read_write_bytes(&self,tx:&'static mut[u8],rx:Option<&'static mut[u8]>,len:usize)
        ->Result<(),(ErrorCode,&'static mut[u8],Option<&'static mut[u8]>)>{
        if self.pending.borrow().is_some(){return Err((ErrorCode::BUSY,tx,rx))}
        let len=len.min(tx.len()).min(rx.as_ref().map_or(usize::MAX,|b|b.len()));
        if len==0{return Err((ErrorCode::INVAL,tx,rx))}
        self.model.borrow().validate_bus(self.polarity.get(),self.phase.get(),self.rate.get());
        println!("OBS\tspi_submit\ttime_us={} tx={:02x?} length={} rate={} buffer={:p}",
                 self.clock.get(), &tx[..len], len, self.rate.get(), tx.as_ptr());
        let hit=self.fault.get().filter(|f|self.model.borrow().fault_matches(f.at,&tx[..len]));
        if let Some(f)=hit {
            self.fault.set(None);self.fault_hit.set(true);
            if !f.asynchronous{return Err((ErrorCode::FAIL,tx,rx))}
        }
        let delay=(len as u64*8*1_000_000+self.rate.get() as u64-1)/self.rate.get() as u64+10;
        self.pending.replace(Some(PendingSpi{tx,rx,length:len,due:self.clock.get()+delay,fail:hit.is_some()}));Ok(())
    }
    fn set_rate(&self,r:u32)->Result<(),ErrorCode>{self.configure(self.polarity.get(),self.phase.get(),r)}
    fn get_rate(&self)->u32{self.rate.get()}
    fn set_polarity(&self,p:ClockPolarity)->Result<(),ErrorCode>{self.configure(p,self.phase.get(),self.rate.get())}
    fn get_polarity(&self)->ClockPolarity{self.polarity.get()}
    fn set_phase(&self,p:ClockPhase)->Result<(),ErrorCode>{self.configure(self.polarity.get(),p,self.rate.get())}
    fn get_phase(&self)->ClockPhase{self.phase.get()}
}
