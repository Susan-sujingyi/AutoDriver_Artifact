//! Host transport for the real kernel DebugWriter. No candidate macro rewriting.
use kernel::debug::{DebugWriter, DebugWriterWrapper};
use kernel::hil::uart::{Transmit, TransmitClient};
use kernel::collections::ring_buffer::RingBuffer;
use kernel::ErrorCode;
struct HostOutput;
impl<'a> Transmit<'a> for HostOutput {
    fn set_transmit_client(&self, _: &'a dyn TransmitClient) {}
    fn transmit_buffer(&self, buffer: &'static mut [u8], len: usize)
        -> Result<(), (ErrorCode, &'static mut [u8])> {
        use std::io::Write;
        let _ = std::io::stderr().write_all(&buffer[..len]);
        // The host transport is synchronous. Return ownership immediately via
        // the kernel writer's error-return path; no asynchronous buffer remains.
        Err((ErrorCode::NOSUPPORT, buffer))
    }
    fn transmit_word(&self, _: u32) -> Result<(), ErrorCode> { Err(ErrorCode::NOSUPPORT) }
    fn transmit_abort(&self) -> Result<(), ErrorCode> { Ok(()) }
}
pub fn initialize() {
    let uart = Box::leak(Box::new(HostOutput));
    let output = Box::leak(vec![0;1024].into_boxed_slice());
    let ring = Box::leak(Box::new(RingBuffer::new(Box::leak(vec![0;4096].into_boxed_slice()))));
    let writer = Box::leak(Box::new(DebugWriter::new(uart, output, ring)));
    let wrapper = Box::leak(Box::new(DebugWriterWrapper::new(writer)));
    unsafe { kernel::debug::set_debug_writer_wrapper(wrapper); }
}
