# Third-party materials

Bundled materials retain their original notices and licenses. This extraction
does not assign a new license to the original research code or to manufacturer
documents. See each component's license for its terms.

- `third_party/riot`, `zephyr`, `tock`, `nuttx`, `nuttx-apps`, `libtock-rs`:
  fixed OS/library source snapshots with upstream license and copyright files.
  File identities and source metadata are under `third_party/metadata/`;
  `third_party/snapshots.yaml` identifies their manifests.
- `third_party/hal_st`: the selected LSM6DSO dependency from upstream commit
  `7a792882847223c72944791e0c48eed6101e6569`, including the original source and
  BSD-3-Clause/Apache-2.0 notices. Identity: `third_party/metadata/hal_st_lsm6dso.json`.
- `third_party/libraries`: source-validator dependencies, including CMSIS,
  nrfx and mpaland printf, with their original license files and release metadata.
- `third_party/rust_vendor`: original Cargo packages and retained license files;
  checksums and origins are in `benchmark/common/target/tock/dependencies/dependencies.json`.
- `third_party/hardware_tools`: elf2tab 0.12.0, MIT license, source archive,
  locked dependencies and Python wheels. This tool packages firmware files;
  verification does not connect to hardware.
- `third_party/tools/kconfig-frontends*`: bundled Kconfig tooling with its package
  copyright and license notices.
- `third_party/wheels`: Python distribution wheels retain metadata and license
  files. Locks are in `dependencies/` and the source-validator fixture.
  AutoDriver analysis license copies are under `third_party/licenses/autodriver/`;
  PyMuPDF includes its upstream AGPL terms. See `dependencies/autodriver-analysis.json`.
- `benchmark/*/documents`: manufacturer datasheets and their original notices.
  Adjacent provenance identifies document revision, retrieval source and hashes.
  Public availability alone is not treated as a new redistribution license.
- `benchmark/*/evaluation`: retained manufacturer evidence for independent
  models carries its own notices and provenance. Examples include Bosch BMM150
  SensorAPI (BSD-3-Clause), Infineon DPS3xx (Apache-2.0), and Aosong DHT20
  supplementary samples. These materials remain host-only.
- Selected or adapted driver sources under `benchmark/` retain their source-file
  notices. The approved processed Zephyr inputs are disclosed in
  `docs/source-authenticity.md`.

`third_party/sources.lock` inventories the distributed resource files. No
upstream source or document is relicensed by this notice.
