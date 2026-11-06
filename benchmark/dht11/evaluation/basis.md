# DHT11 software v2 scoring basis

This is a host-side audit, never agent input. DHT11/YL-47 is a new heldout candidate. No AutoDriver mechanism development or model call is included.

## Original authorities

The unchanged, undated DHT11 manual distributed in the user-supplied DHT11.rar is the frozen document. Pages are PDF pages, one based. Page 3 defines 40-bit MSB-first humidity integer/fraction, temperature integer/fraction and checksum; fractional bytes are zero for this DHT11. Pages 2–4 specify power stabilization of 1 s, the checksum and a start low greater than 18 ms. Page 4 shows response low/high 80 us, bit low 50 us, zero high 26–28 us and one high 70 us. Page 5 specifies sampling intervals no shorter than 1 s. These are the selected original document's limits, not empirical module calibration.

The original frozen Zephyr dht.c is unchanged. Its GPIO active-low selection and CONFIG_DHT_LOCK_IRQS are verified by the full native_sim build. The source proof uses a deterministic link-time clock wrapper and actual GPIO emulator; it is a source fixture, not real-time hardware validation.

The selected Tock sensor HIL and temperature/humidity system-call wrappers define asynchronous output channels and centi-units. The user selected temperature + humidity, DATA=P1.06, 3.3 V, a 4.7 kOhm module pull-up and single-device operation. Output granularity does not imply 0.01 degree or RH physical accuracy.

## Core assertions

DHT11-RAW-ABI: numerical vectors compare integer bytes times 100 through an independent oracle. Normal complete wire frames must yield the requested channel once per accepted request, without unsolicited/duplicate results. Busy/off exact error codes and a particular internal state machine are not required. An early request may reject or defer. Repeated/rapid requests must respect the physical sampling interval; temporary rejection, deferred acquisition and valid caching are allowed. Stable changes must become observable within the bounded test window. GPIO contention, starts before sensor power stabilization and too-short start pulses violate the selected wire contract. Wrap tests use the actual Tock wrapping tick semantics and one tested schedule only.

DHT11-CHECKSUM-DISCARD: derived from the documented checksum equality for correct transmission, corrupt checksum frames must not yield numeric success. Silence, rejection or a supported error callback are legal. Humidity's HIL has no error argument; no invented sentinel is required.

## Diagnostics and model limits

Nominal one pulses of 68/72 us and a 5 us callback delay are robustness diagnostics because the original manual does not define that tolerance. No-response, truncation, stuck-low and fault recovery are observations, not mandatory recovery scores. Internal wire/clock checks are not candidate achievements. The revised model tests asynchronous sampled input and cooperative Alarm use. It generates packed samples independently from the wire event schedule; cycle-counter busy polling, actual DMA registers, arbitrary ISR latency and all interleavings are not established. A successful ARM build with host incompatibility is model_unsupported, not proof of driver invalidity. Real hardware timing, electrical levels, physical accuracy and simultaneous peripheral contention remain unverified.

The public microsecond alarm is generic target infrastructure, not a sensor decoder: TIMER1 is exclusively configured at 1 MHz, freely running, wrapping 32-bit, with separate capture/compare registers. TIMER0 and other timer consumers retain their original implementation. Its source-level register tests check configuration, minimum delay, wrap, expiry and non-resetting disarm/interrupt. They do not prove silicon behavior.

## Waveform platform revision r1

The task target now provides the generic waveform Capture HIL and a board-bound
nRF52840 backend. Candidates and the reference see the same immutable implementation,
component binding, buffer allocation and compiler workaround. The board reserves
P1.06 output/P1.07 input on one data net, P1.08 clock, SPIM2, TIMER2, GPIOTE7 and
PPI14/15. This is a platform capability change, not a hidden reference privilege.
The task still requires sensor protocol decoding and output conversion in the
editable capsule. Source/document prompts and original material bytes are unchanged.

DHT11-CAPTURE-ABI: submission returns without waiting for the wire, a captured
buffer is returned asynchronously, invalid transport never yields invented data,
and completed cancellation permits later requests. These follow the public HIL;
transport fault tests are independent of waveform timing tolerances. Capture-specific cases are diagnostics: a legal receiver that does not consume this optional service must not be rejected solely for not exercising it. A platform
that cannot acknowledge STOP quarantines the DMA buffer; its tests do not demand
unsafe reuse. The model's failure/recovery schedules are bounded, not exhaustive.

Register-level backend tests are separate platform validation, not driver scores.
The independent normal-wire, numeric, checksum and legal-alternative controls
remain applicable. The 50 us decision threshold is a reference choice, not a
required candidate implementation. GPIO-only candidate reception remains modeled;
the public binding requires accepting the sampler, not using a prescribed decoder.

This final revision still requires physical revalidation after removing diagnostic
code. Earlier prototype observations do not become final-reference hardware scores.
