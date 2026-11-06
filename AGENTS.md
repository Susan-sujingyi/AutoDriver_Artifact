# Standalone AutoDriver artifact

- This repository contains the benchmark and the current AutoDriver method only.
- Resolve all runtime inputs inside this checkout; do not use the original checkout, development results, generated knowledge or external symlinks.
- The production method is `method/autodriver/implementation/system/`. Keep algorithm changes separate from packaging changes.
- Canonical tasks are `benchmark/<sensor>/tasks/*/task.yaml`; live AutoDriver runs use SD.
- Agents see only prepared public materials and approved method tools. Hidden references, independent evaluators, manufacturer supplements and scoring feedback remain host-only.
- Preserve upstream licenses, frozen source selections and the source-authenticity disclosure. Do not silently regenerate source validation receipts to accept drift.
- Use `.venv`; install with `python3.13 tests/common/setup.py`. Run offline tests with `bash scripts/test.sh`.
- `manifest.json` proves integrity of this extraction. It is not an inherited experiment acceptance certificate or a correctness claim.
- Never call a paid model or operate hardware as part of offline verification. Keep `.env` private and mode 0600.
