// SPDX-License-Identifier: Apache-2.0
//! Finite asynchronous sampled digital input provided by the board.
use crate::ErrorCode;
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Status { Complete, Cancelled, Short, Timing, Hardware }
#[derive(Clone, Copy, Debug)]
pub struct Report {
    pub status: Status, pub bytes: usize, pub sample_period_ns: u32,
    pub elapsed_us: u32, pub generation: u32,
}
pub trait Client {
    fn captured(&self, buffer: &'static mut [u8], report: Report);
}
/// The platform owns a fixed input and a separate open-drain release output.
/// start() prepares finite MSB-first acquisition and releases that output at
/// hardware STARTED. No protocol parsing. No synchronous success callback.
/// Caller holds output low before start; the board fixes exclusive resources.
/// Buffer remains exclusively owned until captured(). cancel() is asynchronous:
/// if hardware never acknowledges STOP, buffer stays quarantined, never reused.
pub trait Capture {
    fn set_client(&self, client: &'static dyn Client);
    fn start(&self, buffer: &'static mut [u8]) -> Result<(), (ErrorCode, &'static mut [u8])>;
    fn cancel(&self) -> Result<(), ErrorCode>;
    fn busy(&self) -> bool;
}

// Samples are packed MSB first. The fixed board backend produces 1 us samples.
// Completion reports transport validity, not application protocol correctness.
// A successful start transfers the buffer until exactly one completion; on a
// synchronous error the original buffer is returned and no callback occurs.
// Clients must not busy-wait for completion or assume callbacks run inline.
