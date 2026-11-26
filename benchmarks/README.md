# Performance fixtures

All datasets are deterministic synthetic conversations. The generator supports
one huge conversation or many independent conversations, with Unicode text,
distinct receipt fields, unknown source columns/blobs, and standard message/chat
joins. It never reads the user's Messages directory.

```sh
.venv/bin/python -m benchmarks.run --sizes 10000 100000 --output benchmarks/results-one-chat.json
.venv/bin/python -m benchmarks.run --sizes 10000 --chats 100 --output benchmarks/results-many-chats.json
.venv/bin/python -m benchmarks.run --sizes 1000000 --quick --output benchmarks/results-million.json
```

The harness launches separate CLI processes, captures wall and stage timings,
and checks the first import, unchanged run, append, and mutation of an old
message. `--quick` measures only first import and no-op. It disables PNG rendering
and optional full CSV materialization to isolate acquisition/storage/analysis.
Peak child RSS is a high-water mark across a case's child processes; on macOS
bytes are converted to MiB, and on Linux KiB are converted to MiB.

Run benchmarks sequentially on an otherwise idle machine when comparing changes.
Absolute results depend on hardware, data shape, filesystem caching, and other
workloads. CI checks correctness/cache behavior at a small scale rather than
using brittle wall-time limits. Larger output sizes are expected: the archive
preserves source rows, raw bodies, revisions, snapshots, and derived statistics.
