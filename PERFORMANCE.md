# Performance and correctness notes

The structured pipeline replaces the normal `chat.db → HTML → DOM → CSV` path
with a consistent snapshot, batched source reconciliation, a persistent archive,
and cached analysis. HTML remains a compatibility import/export option.

## Reproducible measurements

Measured locally on Apple Silicon / Python 3.10.11, using the synthetic generator
in `benchmarks/`. PNG rendering and optional full message CSV exports are disabled
in these measurements. They include CLI startup, acquisition, persistence, and
statistics/report generation. They are not timings for a rich-media personal
archive and are not a universal speed guarantee.

The machine-readable results are in:

- `benchmarks/results-one-chat.json`: 10k/100k messages, full/no-op/append/old-update.
- `benchmarks/results-many-chats.json`: 10k messages distributed across 100 chats.
- `benchmarks/results-million.json`: one million messages, first import/no-op.

A million-message run completed in about **192 seconds initially** and
**1.9 seconds unchanged**, with about **38 MiB peak child-process RSS**. The
unchanged run did no decoding, tokenization, or report rendering. The retained
output was approximately **2.6 GB** for a 138 MB synthetic source: original rows,
canonical fields, receipts, snapshots, indexes, and per-message analysis caches
are deliberately retained. Raw rows are not compressed. Media copies, historical
snapshots, revisions, extra report views, and full exports add storage.

These large runs are scale/memory checks. Earlier development measurements
included concurrent build/benchmark activity; prefer sequential runs on an idle
machine for precise comparisons. First-import cost varies with binary payload
complexity, number of conversations, and filesystem caching.

The previous review measured roughly 1.9 seconds just to parse/write CSV for
5,000 simple HTML messages, with about 173 MB whole-process peak RSS including
analysis imports. That excluded exporter work and retained less information;
it is not a like-for-like end-to-end baseline. The strongest measured improvement
is skipping repeated work, rather than claiming a fixed first-run speedup.

## Work avoided

- Unchanged database snapshots skip raw reconciliation and body decoding.
- Body cache keys include decoder identity and inputs; completed entries survive
  failed canonical imports and can be reused on retry.
- Stable view metadata avoids scanning all memberships on unchanged reports.
- Per-conversation content fingerprints invalidate affected conversations while
  other conversations reuse their statistics and charts.
- Per-message token/character/symbol caches avoid retokenizing unchanged content
  when report filters/timezone/aggregation change.
- Reconciliation, decoder input, and SQLite sort/staging work are disk-backed;
  Python processes bounded batches rather than a full message DOM/token list.
- Full CSV/JSONL materialization is explicit; routine reports do not serialize
  the whole raw archive again. Word-cloud vocabularies and chart workers are capped.
- Original media is streamed and hashed in one pass, deduplicated, and reused
  when observed file signatures are unchanged.

## Remaining costs and deliberate boundaries

A changed database is reconciled with a full row scan because Messages does not
provide a verified stable change feed here. Appends therefore still have a
linear source-read cost, even though only changed records are decoded. Changed
conversations are reaggregated from cached per-message contributions; individual
period counters are not yet updated by subtracting/adding deltas. This avoids
incorrect first/last times, reply-turn statistics, and sender changes.

Snapshot creation and hashing remain proportional to database size. Explicit
exports are proportional to the requested data. A high-entropy vocabulary grows
Counters with the number of unique tokens; memory does not grow with an
accumulated list of every token. Cached report metadata grows with conversation
count. Large media can dominate total disk and I/O costs.

The decoder companion preserves body ranges and edit histories, while source
rows/blobs and parsed plist payloads preserve data outside its narrow protocol.
It does not interpret every rich app integration. CJK token statistics use
character segmentation, and symbol statistics are not full emoji grapheme counts.

## Correctness gates

The regression suite covers raw/unknown blob preservation, distinct receipts,
source immutability (including mistaken archive paths), WAL acquisition, old
message mutations, reactions, absence/history, duplicate GUIDs, UTC precision,
DST boundaries, Unicode, actual native typedstream/edit fixtures, resumable decode
checkpoints, strict atomic rollback, malformed HTML retention, partial/no-op
status, selective invalidation, media deduplication/integrity checks, offline
exports, migrations, locks, and bounded process-based chart rendering.

CI checks behavior rather than fragile wall-time limits. It is configured for
Linux plus Apple Silicon and Intel macOS runners, tests the actual native binary
on each Mac architecture, and builds the Python package/native companion.
Remote CI has not been executed from this local workspace.
