# ADT7310 temperature scoring basis v2

Host-side audit; never copied into agent materials.

## Product choices and original evidence

ADT7310 temperature in Tock TemperatureClient centi°C, signed i32. Basic selected configuration: 16-bit resolution, continuous conversion, SPI mode declared by task.yaml (RIOT mode 0; Zephyr mode 3), MSB first,8-bit words, maximum1MHz. No connected interrupt, runtime configuration or threshold behavior is required. These are necessary product/interface selections, not algorithm hints. The public board allocates a dedicated SPIM2 with MOSI P1.13, MISO P1.14, SCK P1.15 and CS P1.04. Existing SPIM0/flash stays unchanged; owned GPIOs are removed from userspace GPIO exposure.

Manufacturer ADT7310 RevA24-page PDF, retrieved from the recorded mirror, is frozen with its verbatim text and page map. Source is the unchanged pinned Zephyr implementation and its effective configuration/build evidence. Mirror bytes are not claimed identical to a newer official revision.

## Scoring basis

* NUMERIC: RevA temperature data format (pp12–15): signed16 representation, 1/128°C per count in16-bit mode. The independent Fraction oracle evaluates raw*100/128 in centi°C and permits exact value±1 output unit for rounding. Sixteen core vectors span −55..150°C, byte edges, fractional signs and zero. All65536 encodings are separately checked against Decimal for arithmetic only, not physical range. No reference or source conversion code is imported by the oracle.
* CONFIG: configuration register p14, bits7 and6:5 select16-bit continuous. CT/INT configuration and thresholds are not constrained with these pins unused. Writing0x80 or0x90 is equivalent for the selected functions. Final numeric callbacks require selected settings. The chosen SPI mode/frequency limit is public configuration. Identity probing is optional.
* DATA: normal operation p9, status register p13, temperature value pp14–15 and serial communication pp17–19. SPI transfers send a command byte; temperature value is high-byte then low-byte. Data represent the most recent converted sample; the model allows cached acquired values and does not force every request to read RDY or acquire a new sample. RDY bit7 is0 after a conversion and returns1 after reading the temperature register. Stable changed input must propagate in the observation window. A700ms waiting control without status reads is accepted.
* ABI: frozen sensors.rs TemperatureDriver/TemperatureClient. Healthy accepted requests produce a corresponding result; no unsolicited or duplicate callbacks. Concurrent requests may be rejected/queued. No exact BUSY/OFF error code, initialization idempotence or callback reentrancy requirement is imposed. Missing completion is not_completed.
* DIAGNOSTIC: generic SPI immediate/asynchronous FAIL and stalled device observations. These are HIL failure injections, not I²C NACK and not claims of electrically detectable faults. Failure/recovery policy is not core-scored. Unreached fault is not_exercised.

## Finite model and support boundary

Power is stable, documented defaults at start, fast first conversion6000µs, subsequent periods240000µs; one case delays first conversion to600000µs. Reset via32 ones is modeled with500µs access exclusion. These schedules exercise readiness; they do not prove bounds over real silicon. No fast-conversion physical accuracy, threshold electrical behavior, precision calibration, all CS timing, mux arbitration or alarm wrap is verified. Continuous SPI read mode, reserved command encodings and transient non-continuous conversion modes are model_unsupported, not automatically driver defects. Initialization using such modes needs a model coverage review.

Shared FakeSpi implements the frozen SpiMasterDevice interface with separate TX/RX buffers, minimum transfer length, optional write-only RX, one pending transfer, BUSY returns with original buffers, delayed callback and configuration blocking while busy. Default settings match VirtualSpiMasterDevice. It does not simulate multiple queued virtual clients. Four model self-checks cover signed data, RDY clearing, reset and buffer/length semantics.

Startup supervision250ms; requests1s; stable-input progression2s;50000 events/advance;5s/process;300s/build. These are observation resources, not physical device deadlines. Nominal numeric rounding allowance is not sensor accuracy.

## Controls and status

Reference plus legal nearest rounding, reduced fraction, comparator mode,500kHz SPI, omitted ID probe and waiting without status polling. Compilable defects cover units, scale, sign, byte order, resolution, SPI mode/frequency, short receive, fabricated/duplicate/missing callbacks. All are evaluator audits, not methods or heldout model experiments. Actual ARM board compilation, S/D/SD validation and hardware bundle evidence are recorded separately. No real model calls or flashing.

Scoring revision: the 600 ms first-conversion delay is diagnostic; no task requirement makes this extra delay mandatory. Initial BUSY/OFF requests use the shared bounded admission observer, advancing events and retrying only unaccepted requests. Admission deadline, event budget, and host deadline are distinct evidence; rejection alone does not establish a driver defect.
