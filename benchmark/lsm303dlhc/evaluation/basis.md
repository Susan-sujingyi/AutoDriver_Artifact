# LSM303DLHC magnetic scoring basis v2

Host-side audit only. This document, oracle, reference and models are never agent inputs.

## Required product and interfaces

The user confirmed GY-511, magnetic XYZ in signed integer nT, sensor axes, SDA P0.26/SCL P0.27. Fixed initial configuration: ±1.3 gauss, 15 Hz continuous magnetic sampling, temperature disabled, no interrupt connection. The magnetometer's 7-bit I²C address is 0x1e (ST DocID018771 Rev2 §5.1.3). No accelerometer implementation is required. Frozen Tock NineDof transports low32-bit signed values through usize; userspace converts to i32. Board/component wiring is public, driver algorithms are hidden. Both zero and sign extension on a 64-bit host are accepted.

## Independent sources and assertions

* **NUMERIC:** ST Rev2 pp38–39, Table75 and output registers03–08. Wire sequence X,Z,Y, each high-byte first signed two's complement. Nominal sensitivities at ±1.3 gauss are XY1100 and Z980 counts/gauss. One gauss=100000nT. Fraction oracle is independent of Zephyr and reference code. Exact ideal ±1nT accepts integer truncation and nearest rounding. This is arithmetic tolerance, not instrument precision. Sixteen physically plausible vectors exercise axis isolation, negative signs, byte boundaries and unequal axes. The oracle also checks all4096 documented count encodings against Decimal; this exhaustive arithmetic check is not4096 physical tests.
* **CONFIG:** pp37–38 CRA 15Hz and temperature disabled; CRB gain001; MR continuous00. Power-on defaults already select rate/range, so rewriting them is not mandatory. Configuration order and transaction grouping are unrestricted. Reserved-bit writes are masked, invalid writes ignored per §5.1.3. A callback under wrong required configuration fails.
* **DATA:** pp39–40 LOCK/RDY and §5.1.3 pointer wrapping. The model locks a sample upon the first data read and releases after all six bytes are acquired. Both burst and split reads are possible. Successful output must correspond to a completely acquired sample; valid cached values are allowed. A stable changed input must eventually be observed within a bounded supervisor window. No exact trace or mandatory status-read instruction is required.
* **ABI:** Existing Tock NineDof accepts/rejects requests and asynchronously reports XYZ. No unsolicited, duplicate or rejected-request callbacks. Concurrent requests may be rejected or queued; no exact BUSY/OFF code is mandated. Healthy completion supervised for500ms; missing completion is not_completed rather than wrong numeric output. Startup250ms, stable-change1s, event ceiling20000, process5s are evaluation resources, not datasheet hard deadlines.
* **DIAGNOSTIC:** Injected immediate/asynchronous transport failures and stalled conversion record observations only. NineDof has no result-error callback here. No unique recovery strategy or exact error-code requirement is imposed. A fault not reached is not_exercised. Diagnostic execution does not prove recovery.

## Model scope and limits

Power supply is stable and the device starts at documented reset defaults. The finite model uses66667µs conversion periods and a delayed310ms first conversion case. This is selected digital stimulation, not proven timing bounds for all silicon. It does not cover physical accuracy, saturation/overflow, self-test, temperature, magnetic offsets, sensor orientation calibration, all legal schedules or timer wrap. Invalid reads yield zero according to §5.1.3. Optional temperature reads are model_unsupported. The shared FakeI2C permits one outstanding transaction and does not simulate the virtual bus arbiter. Output locking and pointer behavior are the supported finite interpretation; legal implementations outside it must be audited instead of automatically claimed incorrect.

## Validation controls

Reference, reordered configuration, power-on defaults, nearest rounding, reduced rational arithmetic and signed-extension controls. Deliberate wrong axes, Z sensitivity, units, endian, sign, range/rate, fabricated values and sleeping sensor must compile and be detected. Each run retains individual cases; tests and controls are benchmark maintenance, never AutoDriver method results.

Manufacturer-authored PDF retrieved from Pololu mirror, original revision and SHA recorded. Full Zephyr source configuration was built/linked and smoke executed in the independent source-build fixture; this proves material integration only. No model calls or physical experiments are part of this audit. Board and userspace build evidence is recorded separately.
