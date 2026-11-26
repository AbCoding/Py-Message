# Py-Message

Explore trends in your iMessage history on your Mac. The app runs locally and opens in your browser.

## Download for macOS

**macOS Sonoma 14 or later.** Choose the download for your Mac. **Apple menu → About This Mac** shows a **Chip** (Apple Silicon) or **Processor** (Intel).

| Mac | App download |
| --- | --- |
| Apple Silicon (M-series) | [Download Py-Message for Apple Silicon](https://github.com/AbCoding/Py-Message/releases/latest/download/Py-Message-Apple-Silicon.zip) |
| Intel | [Download Py-Message for Intel](https://github.com/AbCoding/Py-Message/releases/latest/download/Py-Message-Intel.zip) |

1. Unzip the download, drag **Py-Message.app** to **Applications**, and open it. Python, the message decoder, and chart libraries are included.
2. The app is ad-hoc signed and **not Apple-notarized**. If macOS blocks the first open and you trust the download, use **System Settings → Privacy & Security → Open Anyway** after attempting to open it. [Apple’s first-open instructions](https://support.apple.com/en-us/102445)
3. To read your Messages history, add **Py-Message.app** under **System Settings → Privacy & Security → Full Disk Access**. Quit and reopen Py-Message after granting access.
4. Choose **Load conversations** in the browser. Use the small Py-Message window to reopen the workspace or quit. The desktop app keeps its archive in `~/Library/Application Support/Py-Message`. **Soft Paper** is the default theme.

[Release notes and SHA-256 checksums](https://github.com/AbCoding/Py-Message/releases/latest)

## Run from source

Requires Python 3.10 or later. In the repository directory, run

```sh
./run.sh
```

The launcher prepares a local Python environment on first use and opens the app
with saved data. **Load new data** reads `~/Library/Messages/chat.db` without
modifying it. Use `./run.sh run` for sync, analysis, and an offline report at
**`GeneratedFiles/index.html`**.
Messages are processed locally. First-time Python dependency setup requires
internet access; normal structured imports and offline reports do not.

## Interactive app

```sh
./run.sh app
```

The local app opens on **Overview**. On first use, choose **Load conversations**
to import your Mac’s Messages history. On later visits, **Manage data → Load new
data** checks for changes. Opening the app uses the saved library and does not
start a message scan. On small screens, **Library** opens the page’s picker.

| Page | Explore |
| --- | --- |
| **Overview** | Compare activity and your language in direct texts and groups. Choose a period on the chart and open the corresponding page with that date range. |
| **Direct texts** | Compare correspondents, then select a person to combine their linked direct threads. **Reply timing** measures the median elapsed time between alternating senders within each thread. Adjust the maximum gap in **Date range and options**. |
| **Group chats** | Compare groups, select one to compare member activity or language, or open **Reactions** to switch between totals, heart history, the leaderboard and the member matrix. |

Choose **Activity** or **Language**, set the direction and time grouping, and
select **Run analysis**. Language defaults to messages you sent. Compare words
per text, emoji per 1,000 tokens, or usage of a specific word or emoji. Selecting
a frequent word or emoji plots its rate. In a group, selecting a member limits
the analysis to that author's messages. The **Through** date includes the entire
day in the displayed timezone.

The archive trends count each dated, current message once across memberships
and imported copies with the same GUID. When several threads contain the same
message, the original membership takes precedence over a recovered one, then
the newest row and stable IDs break ties. Conversations without enough
participant information appear under **Unclassified chats** in Overview.
The chart and two-period comparison show up to ten series ranked by number of
qualifying messages. The table includes all matching people or groups.

Use the period slider to inspect exact chart values and open underlying messages.
Activity and reply timing also offer **Compare two periods**.
**Saved views** stores named filters next to the local archive, so they can be
reopened after restarting the app. Reopening a view uses the current archive.
**View messages** opens the selected person or group's history. **Load earlier
messages** pages backward. The original per-thread report is available under
**Conversation statistics** for a group, or **Analysis** when browsing an
individual source thread. It includes sender activity, word clouds, reaction
counts, and downloadable statistics.

**Reactions received** totals reactions on each person's messages, including in
group conversations. Columns show hearts, likes, dislikes, laughs, emphasis (`!!`),
questions, and any custom emoji or sticker tapbacks present. Rows use contact
names when matched. Date filters select the original messages; counts reflect
their current reaction state across the archived history, including later changes.
Removed/replaced reactions and duplicate source rows do not inflate the totals.
Unresolvable events and legacy sticker overlays are reported separately; HTML
imports lack the structured reaction details needed for these totals.
This works from the existing archive without importing Messages again.

Group-chat analysis also includes **Hearts over time**, **Messages by heart count**,
and **Most-hearted messages**. These use standard Loved/heart tapbacks only and
count each reacting person once per whole message, including your own reactions.
Multiple hearted parts by the same person do not inflate a message's score;
the original reaction totals above still count individual message-part tapbacks.
Custom red-heart emoji reactions remain separate from standard Loved tapbacks.

The timeline groups current heart counts by the original message date, with a
total view or separate author lines. It follows the analysis grouping/timezone;
very long day/week ranges use months for this chart. Empty periods show zero.
The exact-count selector compares how many messages each author has with exactly
1, 2, 3, etc. hearts; the table shows all observed counts separately. A message
with three hearts contributes once to the three-heart column. The ranked list
shows up to 50 messages, sorted by heart count then newest message, with author,
date, and up to 1,000 characters of current text. Attachment-only and unsent
messages are labeled; unsent text is not displayed. All three views reflect
removals and replacements across the archived reaction history.

**Who hearts whose messages** is an asymmetric heatmap. Rows give hearts and
columns receive them: a cell is the number of an author's distinct messages
hearted by that person divided by all of that author's messages in the selected
date range. Unhearted messages count in the denominator, repeated hearts on parts
of one message count once, and removed/replaced hearts are excluded. Self-pairs
and authors with no messages show a dash; hover or focus a cell for exact counts.
The scale runs from 0% to 100%, and includes your reactions.

The most-hearted messages list also shows local attachments. Images load as you
scroll to them; video and audio have a **Load** button, and files have a
**Download original** button. Images use cached previews; HEIC conversion on macOS
uses the built-in `sips` tool. Missing files and attachments stored only in iCloud
show an unavailable label. This reads existing local or archived files without
refreshing the Messages database. Attachments on messages containing unsent parts
are hidden.

Import and analysis run in a background job while the interface stays available.
The app reads your archive; it does not send messages. Outside the most-hearted
messages list, attachments appear as indicators. Edited, unsent, unsupported,
and non-message events are labeled. Composing messages is not implemented.

Open **Manage data → Match contacts** to label phone numbers and email addresses
using your Mac's Contacts. Allow the macOS prompt to access Contacts; if denied,
enable Contacts under **System Settings > Privacy & Security > Automation** for
the app launching Py-Message. This permission is separate from Full Disk Access.
See [Apple's Automation permission guide](https://support.apple.com/guide/mac-help/mchl108e1718/mac).

Names appear in the conversation list, group sender labels, and analysis legends.
Search also matches contact names. Custom conversation/group titles and original
addresses remain intact. Matching ignores phone punctuation and email case;
North American ten-digit numbers also match their `+1` form. Other international
numbers need the same country code. Ambiguous shared numbers remain unlabeled.

Only names, phone numbers, email addresses, and contact IDs are read. Labels are saved locally
in `ARCHIVE_DIRECTORY/contact-labels.json` and reused on restart. **Refresh contacts**
updates renamed/deleted contacts without importing messages or rerunning analysis.
No contact data is uploaded or changed. Demo mode disables contact matching.

Open **Direct texts** to combine a person's messages across linked direct threads.
Equivalent phone-number formats group automatically. **Refresh contacts** also
groups phone numbers and email addresses on the same contact record; equal
display names alone never merge people. Older name-only contact caches still
work for labels but need a refresh to enable contact-based grouping.

Select a person and choose **View messages** to view **Direct conversations** (your messages and theirs across
their direct threads) or **Their group messages** (only messages they authored
in group threads). The addresses and source conversations are listed above the
messages. Each message links back to its original conversation. Duplicate GUIDs
across threads or imported sources are shown once, with all matching source
threads retained. Reactions and system events are omitted from these person views.

Use **Link another address** for entries that belong to the same person but are
not connected by Contacts. **Undo manual links** separates the manually combined
entries while preserving automatic contact matches. Links persist in
`ARCHIVE_DIRECTORY/people-links.json`; original messages, conversation membership,
and contact cards are unchanged. These views use the existing archive and do not
require a message re-import. The Direct texts page uses these identities for its rankings and person-level analysis.

**Direct texts → Activity → People rankings** plots who you message most over time across their combined
direct conversations. Choose **Sent + received**, **Messages I sent**, or
**Messages I received**, an optional date range, day/week/month buckets, and the
top 1, 5, 10, or 15 people. Select **Plot** to calculate counts in the background.
**Top each period** selects the leaders independently for each day, week, or month,
including people outside the overall top group. Dots show each leader's count;
lines connect consecutive qualifying periods, with gaps elsewhere. These are
per-period counts, not smoothed averages. Only people with messages qualify;
ties are resolved by name, then identity. The legend and ranked counts follow
the hovered period or slider. **Top overall** keeps a fixed set ranked across
the entire date range and shows zero for empty periods. Weeks start Monday in
the selected timezone, and the end date is exclusive.

Select legend names to show/hide lines. Hover over the chart or move the period
slider to compare exact counts. The ranking table shows sent/received counts and
each person's share of the selected metric; selecting a name opens their combined
message view. **Save counts** downloads JSON with the series and ranking.
Group conversations, reactions, absent records, and duplicate messages are excluded.
The chart reads message metadata only, with no message re-import, body decoding,
or word-cloud rendering. Changing the ranking mode or top count reuses cached
counts without recounting messages. Syncing or changing contact identities
or manual links invalidates that cache. Choose a broader grouping or shorter range
if a chart would exceed 1,500 periods.

**Direct texts → Language → Vocabulary details** analyzes your sent text in direct conversations across linked
addresses. Open the view, optionally select up to six people, choose a date range
and day/week/month grouping, then select **Analyze vocabulary**. Opening the view
does not scan message text or refresh Messages. Everything runs locally without
an API key, model download, or external service.

- **Vocabulary over time:** word counts, words per text message, and average
  distinct words in complete, non-overlapping 100-word samples. Insufficient
  samples are blank rather than zero.
- **Frequent words** ranks words by count across the full selected range. Select a word to explore its context.
- **Words by person:** frequent words and words used more with each recipient
  than with the other selected recipients. Selecting people also gives separate
  vocabulary and tone timelines. Overall shifts can reflect changing recipients.
- **Tone signals:** overlapping, per-message rates of positive/negative wording,
  laughter, politeness, uncertainty, profanity, and question marks. The small
  English word lists are displayed in the app. Simple nearby negations suppress
  positive/negative cues; this is not a sentiment model and cannot reliably infer
  emotion, sarcasm, quoted speech, or tone in other languages.
- **Name mentions:** literal token matches against saved contact names and
  optional comma-separated names. First-name and ambiguous matches are labeled;
  full names take priority over overlapping first names. A name counts once per
  message. A match can be an ordinary word or direct address and does not prove
  whom the message refers to. Unlisted names require manual entry.
- **Word and emoji context** opens from the same Language page. Enter one to six words or emoji separated by
  commas or spaces, independent date/grouping filters, and a person picker for
  up to six people (empty means everyone). Compare usage on one chart, then
  switch selection tabs to see nearby words and emoji, per-person timelines, and recipient
  comparisons. Selecting a frequent word in Your vocabulary opens this view
  with that word and carries over the date and person filters.
  The **Emoji** button beside the input opens a searchable, categorized picker.
  Select multiple emoji, select one again to remove it, then choose **Done**.
  The six-selection limit includes typed words. The catalog is bundled locally;
  opening the picker does not scan messages or start analysis.
  Context uses a five-token window on each side; a token is a word or a complete
  Unicode Emoji 17 sequence. Rates use all word and emoji tokens as the denominator
  (per 1,000), including emoji-only messages. Joined families, flags, keycaps,
  and skin-tone combinations stay intact. Skin-tone variants remain distinct;
  text/emoji presentation variants such as ❤ and ❤️ match together.
  Context stays
  within message boundaries. Context change is one minus cosine similarity of
  adjacent periods' neighboring-word counts, with at least five uses and nonempty
  context in each period. It measures distributional associations, not proven
  changes in meaning. No score is produced across an empty period.

Words are Unicode-normalized and case-insensitive; URLs, email addresses, and
numeric tokens are excluded. A small English function-word list is hidden only
from word lists and context, not diversity or vocabulary rate denominators.
Existing character segmentation for CJK text still applies. Attachment-only,
unsupported, fully unsent, invalid-date, and ambiguous-recipient records are
reported as exclusions; received messages, group messages, reactions, and duplicate
GUIDs do not contribute. Current archived text is used for edits.

Vocabulary/tone/name results and individual word-context results have separate
private caches under `GeneratedFiles/app-semantic`. Word context runs independently
of vocabulary/tone/name analysis. All uncached selected words share one scan of
the selected sent text; compatible cached selections are reused. Emoji-aware
context has a separate tokenizer version, so older word-only context results
are recalculated once; vocabulary/tone/name caches remain valid.
Switching detail tabs uses the loaded results without a
new scan. Opening the view does not run analysis; select **Analyze words** to run.
Syncing, contact or identity changes, different filters, or additional names
invalidate the relevant cache. Progress appears during scans. **Save analysis**
exports aggregate JSON, without full message text. Lists show up to 30 people
and the top 30 name matches, plus all names entered manually; narrow the selection
to inspect others.

```sh
./run.sh app --db-path /path/to/chat.db       # import a copied Messages database
./run.sh app --html-dir /path/to/export      # use the existing HTML importer
./run.sh app --archive /path/to/archive.sqlite
./run.sh app --no-browser --port 0           # print a URL using an available port
./run.sh app --demo                         # optional, isolated fictional data
```

Demo mode uses `OUTPUT/demo/` and never reads your real Messages database.
Starting the app does not import anything automatically; use **Load new data**.
Stop the server with Ctrl+C. An active archive operation finishes before exit.

The server binds only to `127.0.0.1`. Its printed URL contains a per-launch session
token; use that full URL when opening a new browser session. APIs require the
token and reject other web origins. No remote fonts, scripts, or analytics load.
The app reuses the Python environment and needs no Node build or frontend setup.

## What changed

The normal workflow no longer exports and reparses HTML. It uses a pinned,
checksum-verified companion built from `imessage-database`, the library behind
[imessage-exporter](https://github.com/ReagentX/imessage-exporter). Original source
rows and payload bytes are retained alongside decoded text, components, and edits.
The existing HTML importer and original Python helpers remain available.

Unchanged runs reuse decoded bodies, text statistics, analysis, and charts.
Changes to older messages, read receipts, reactions, chat membership, and source
schema are reconciled too. A committed SQLite WAL is included in acquisition.
The archive is updated transactionally; interrupted imports can be rerun and
reuse completed decoder checkpoints.

## Commands

```sh
./run.sh                                  # open app with saved data
./run.sh app                              # interactive conversation browser
./run.sh run                              # explicit sync + analysis + report
./run.sh sync                             # update archive without charts
./run.sh report                           # regenerate/reuse report offline
./run.sh analyze --charts none             # statistics without chart rendering
./run.sh export --format jsonl             # full structured message export
./run.sh export --format csv               # enriched CSV export
./run.sh doctor                           # runtime, decoder, archive integrity, run status
./run.sh --help
```

CSV/JSONL exports include physical and logical IDs, GUIDs, source provenance,
UTC timestamps with nanosecond formatting, sender/direction, service, event type,
thread/reaction references, current text, decoded data, raw fields, and attachments.
Blob values use a reversible `{"$bytes":"BASE64"}` representation. Parsed plist
payloads, exact receipt fields, revisions, and original schema are also available
in the SQLite archive. `--export-csv` adds full message CSVs to a normal report;
large text exports are optional so routine sync/report runs stay fast.

```sh
# A copied database, retaining the same identity when its path changes.
./run.sh --db-path /path/to/chat.db --source-id my-macbook

# Filters select archive views; they do not discard the other imported history.
./run.sh --conversation "someone@example.com" --start-date 2024-01-01 \
  --end-date 2025-01-01 --timezone America/Indiana/Indianapolis

./run.sh --group-by month --separate-by-sender --filter-common-words
./run.sh --group-by all --charts all --workers 2
./run.sh --charts none

# Original media is optional, content-addressed and deduplicated.
./run.sh --archive-media
./run.sh --db-path /path/to/chat.db --attachment-root /path/to/Attachments --archive-media

# Existing HTML, with an explicit assumption for timestamps that have no zone.
./run.sh --html-dir /path/to/exported-html --timezone America/Indiana/Indianapolis

# Additional upstream HTML export, using a managed pinned exporter download.
./run.sh --legacy-html
```

`--end-date` is exclusive. Report dates and hour/day/week buckets use the selected
IANA timezone, including daylight-saving changes. The default is the system IANA
zone when discoverable, otherwise UTC. Raw Apple timestamp integers remain intact;
older seconds and newer nanoseconds are interpreted by magnitude, and unknown or
invalid timestamps remain flagged rather than invented. HTML cannot recover the
original source timestamp precision or unavailable database fields.

`--charts top` (default) charts the 20 largest matching conversations; all selected
conversations still receive statistics. Use `--top N`, `--max-words N`, or
`--charts all` to adjust rendering. Worker count is bounded to 1–4 independent
processes. Artifacts are cached by content and settings, and published atomically.

## Data retained and analysis definitions

- Full source rows, including unknown columns and blobs, plus observed revisions.
- Stable source identity, physical row IDs, GUID-based logical IDs, chats,
  participants, thread references, and message/chat membership.
- Original and decoded text, attributed parts/ranges, edit/unsent histories,
  reactions, separate sent/delivered/read/edited/retracted values, and parsed plists
  where the source provides them. Supplemental effect descriptions preserve the
  pinned decoder's representation; source payloads remain authoritative.
- Attachment metadata and local file availability observed during import; optional
  copies of available originals. Missing assets remain explicit. The tool does not
  retrieve missing iCloud history itself, and cannot recover data never observed.
- Original HTML snapshots and provenance for compatibility imports.

Message counts exclude reaction and announcement rows, which are counted
separately. Duplicate GUID rows remain archived but contribute once per
conversation. Records absent from a later complete source snapshot are retained,
marked absent, and excluded from the default current view. Use `--include-absent`
to inspect/export historical observations. Absence is not labeled as a proven
user deletion.

Text metrics share Unicode-aware rules: accents and apostrophes are preserved,
hyphen-separated words remain distinct, and CJK text is counted by character
rather than pretending to provide language-specific word segmentation. Unicode
symbol counts are separate from word counts; they are not emoji grapheme counts.
Original text is never replaced by normalized analysis tokens. Weighted word
length is total token characters divided by total tokens.

Response time means a change of sender direction in a two-person conversation,
up to the configured `--response-gap-hours` (default 24). It measures adjacent
conversational turns, not read receipts, and is omitted for ambiguous group chats.
App payloads are preserved, but not every rich app integration is rendered or
interpreted as prose.

## Storage, recovery, and access

Default layout:

```text
GeneratedFiles/
  .state/archive.sqlite        # canonical records, revisions, analysis caches
  .state/snapshots/            # latest successful snapshot per database source
  .state/decode-cache.sqlite   # resumable body decoding checkpoints
  .state/media/                # optional copied originals, named by hash
  .state/html-snapshots/       # immutable compatibility-import sources
  reports/<settings>/         # local index, statistics, selected PNGs, optional CSVs
  runs/<run-id>/summary.json   # status, timings, configuration, coverage, provenance
  latest-run.json
  index.html
```

Choose a report location with `--output`, or a persistent store with `--archive`.
The latest successful database snapshot is kept; `--retain-snapshots` also retains
older acquisitions. Observed raw row revisions remain in the canonical archive.
A full source reconciliation may still scan the database after it changes;
unchanged sources skip decoding and analysis, and changed conversations are
reaggregated from cached per-message statistics. An explicit full message export
must still write the requested records.

Exit codes: **0** complete, **1** failed, **2** partial coverage with source data
retained, **130** interrupted. Unsupported records and invalid timestamps remain
visible in reports/manifests. `--strict` makes those import failures atomic instead.
`--rebuild` recomputes from retained/acquired data without deleting observed
history. Rerun the same command after interruption; successful decoder checkpoints
are reused. Concurrent writers to one archive are rejected.

If macOS denies access, enable **System Settings > Privacy & Security > Full Disk
Access** for Terminal, your IDE, or Codex, restart that application, and retry.
No disabling of system protections is needed. Database imports run on the Mac
with that history; offline HTML/plain-text archive workflows also work on Linux.
The tool does not connect to another Mac automatically.

Generated data, downloaded tools, build outputs, and Python environments are
ignored by Git. Back up the persistent state directory to retain your archive.

## Development and verification

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m benchmarks.run --sizes 10000 100000 --output benchmarks/results.json
.venv/bin/python -m benchmarks.run --sizes 1000000 --quick --output benchmarks/million.json
```

Install the Python console entry point with `python -m pip install .`, then use
`pymessage`. `main.GenMessages()` and `main.MakeWordCloudFromScratch()` retain the
original interactive HTML helper behavior. `TextingPatterns` is now safe to import.
The native source, Cargo lockfile, both Mac binaries, integrity manifest, and
build scripts are included. See [native/README.md](native/README.md),
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), and
[PERFORMANCE.md](PERFORMANCE.md). CI is configured for Linux and both Mac
architectures, including actual native decoder fixtures and package smoke checks.
