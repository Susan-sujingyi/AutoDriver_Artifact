# Offline native Rust build dependencies

This bundle closes the native benchmark's dependency on an already populated host Cargo registry. It does not bundle or clear the Rust compiler, rust-src, linker or GNU make. Install the public `nightly-2024-01-01` toolchain with `rust-src`, `llvm-tools-preview` and target `thumbv7em-none-eabi` first. Python installation follows the artifact's normal setup instructions.

`dependencies.json` pins 33 original crates downloaded from the official `https://static.crates.io/crates/` distribution service. Each archive SHA-256 equals its package checksum in either the frozen Tock `Cargo.lock` or the fixed nightly's rust-src `Cargo.lock`. The manifest records the authority lock-file hash, URL, byte size, extracted-file hashes and license locations. Original crate bytes and source files are retained in `third_party/rust_vendor/archives` and `sources`; only Cargo's `.cargo-checksum.json` metadata is added to each extracted directory.

Eleven packages cover the frozen Tock workspace's registry dependencies. Twenty-two cover the fixed toolchain's sysroot dependency resolution, including `compiler_builtins 0.1.103`. Tock's `-Z build-std=core,compiler_builtins` still resolves dependencies of the sysroot, including `std` and target-specific packages, even though this board does not compile/link all of them. For example, Tock's lock selects `cfg-if 1.0.4`, but the nightly sysroot also needs `cfg-if 1.0.0`. Providing only compiler_builtins, or only the Tock lock's versions, fails in a new Cargo home. This is a tested sufficient pinned set, not a claim of a globally minimal dependency set or support for every Rust target.

## Reproduce the fresh-Cargo check

```sh
.venv/bin/python scripts/validate_native_offline.py --out runs/native-offline-review
```

The output directory must be new. The script verifies the frozen Tock snapshot, makes a private copy, validates all bundled crate archives and sources, creates an absent-before-use `CARGO_HOME`, and points only that home's `config.toml` to the bundled directory source. Cargo network access is disabled. It runs the actual capsule check and nRF52840DK board make, saves logs and the resulting firmware hash, and checks dependency traces for old Cargo registry paths. It refuses external ancestor Cargo configurations that could affect the proof. No source snapshot, shared Cargo configuration, model or hardware is modified.

The successful final proof is `runs/native_offline/fresh-cargo-v2/report.json`; selected exact-byte evidence is preserved in `docs/validation/native_build/fresh-cargo-v2`. The fresh Cargo home contains only configuration and Cargo lock files, with no registry/index or downloaded source cache. The existing Rust toolchain remains available; this is not a fresh operating-system image or a network sandbox. Full sensor task patch replay is a separate validation step. libtock-rs userspace has another dependency graph and is not covered by this bundle.

## Reuse from evaluators or a shell

The Python API is `driverbench.native_dependencies.create_cargo_environment(root, new_cargo_home) -> (env, evidence)`. It validates vendor integrity and creates a new home, strips inherited Cargo overrides and Rust compiler wrappers/flags, selects the pinned toolchain and returns an environment for the caller's capsule/board commands. Reuse the returned environment for both commands; do not call it again on an existing directory. The evidence dictionary records the pinned dependency manifest and configured Cargo home.

For a caller-owned target worktree:

```sh
.venv/bin/python scripts/setup_native_cargo.py \
  --cargo-home runs/reviewer-cargo-home \
  --evidence runs/reviewer-cargo-environment.json \
  --cwd runs/your-prepared-task/target/boards/nordic/nrf52840dk -- make
```

`--evidence` saves the complete original environment identity JSON to a new file; terminal output contains only a short configuration status before the optional command's own output. Omitting the command only creates the private configuration; later commands must use that `CARGO_HOME`. This helper never edits the artifact root's `.cargo` directory or an installed toolchain.

## Licenses and provenance

All original license and copyright files are included unchanged. `r-efi` and `r-efi-alloc` carry their complete MIT grant and copyright notices in `AUTHORS`. `fortanix-sgx-abi` carries the original MPL-2.0 notice in `src/lib.rs`; the complete official Mozilla license page is additionally bundled under `third_party/rust_vendor/licenses`. These are third-party packages under their respective licenses, not relicensed AutoDriver code. `dependencies.json` lists each package's original license expression and source locations.

Cargo directory source replacement and checksum metadata follow the [official Cargo source replacement documentation](https://doc.rust-lang.org/cargo/reference/source-replacement.html). Initial incomplete dependency discovery attempts are retained under `runs/native_offline/initial-v1`; only the final fresh-home build is the complete success proof.
