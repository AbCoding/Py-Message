# Structured decoder companion

`pymessage-decoder` is a narrow JSONL adapter around upstream
`imessage-database`, pinned to tag `5.0.1` and its exact Git revision in
`Cargo.lock`. It reads a SQLite snapshot and a newline-separated file of source
row IDs. Output protocol 1 contains the row ID, recovered text, ordered body
components/ranges, effects, attachment hints, edit/unsent history, and per-record
errors. Original source blobs are preserved independently by the Python archive.

Normal users use the verified prebuilt binaries under `pymessage/bin/` and do not
need Rust. The versioned protocol is deliberately smaller than the upstream API;
unknown/raw values are retained rather than silently discarded. Rich app payloads
are retained as raw and parsed plist data when applicable; this companion does not
render every app integration. Effect/attachment debug descriptions are supplemental;
the raw payloads and upstream version remain the authoritative preserved source.

To rebuild on macOS with Rust/rustup installed:

```sh
rustup toolchain install 1.99.1 --profile minimal
rustup target add aarch64-apple-darwin x86_64-apple-darwin --toolchain 1.99.1
python3 native/build.py
python3 native/collect_licenses.py
```

`build.py` runs Cargo inside this directory so `rust-toolchain.toml` applies.
Both Mac targets use the Xcode command-line SDK. The script refreshes the binary
manifest only after successful builds. CI tests the actual native executable on
Apple Silicon and Intel runners. Before shipping a decoder update, rerun parity,
raw-preservation, malformed-record, and incremental-cache tests and regenerate
notices using the same Cargo source cache used to build the binaries.

The companion is GPL-3.0-or-later. See `LICENSE` and the bundled notices. The source
and lockfile are included in source distributions so recipients can rebuild it.
