# Generic nRF52840 I2C transport

The board supplies logical I2C0 on TWIM0, P0.26 SDA/P0.27 SCL. SPIM0 is disabled.
The native NuttX I2C interface supports 7-bit addresses at 100/250/400 kHz,
STOP-separated messages, same-direction NOSTART continuations, and a repeated
START from a write group to a read group. Each contiguous group is at most 256
bytes. Other repeated-START shapes and 10-bit addresses return ENOTSUP before
starting any transaction. This resource contains no sensor protocol.

The transport uses private static DMA buffers. Read data is copied to the caller
only after successful completion and matching byte counts. A failed transfer
returns negative errno. Mutex acquisition has a 10 ms budget; a transaction
has a 50 ms tick-based wait budget and abort has a 10 ms budget. Tick rounding
and OS scheduling add latency; these budgets do not establish a physical
end-to-end sensor response deadline.

After a failure the transport requests STOP. If STOPPED is absent after abort,
the controller and its private DMA storage remain reserved and subsequent calls
return EBUSY. It is unsafe to disable or reuse an active DMA buffer. A later
STOPPED permits the next request to clear stale events and resume operation.
No automatic GPIO recovery pulses, board reset or power cycling is performed.
Preserve these errors in the acceptance log. Hardware recovery/readback remains
a user operation, and repeated software errors must not be labeled valid samples.
