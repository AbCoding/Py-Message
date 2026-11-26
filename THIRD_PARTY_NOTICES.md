# Third-party components

The bundled `pymessage-decoder` companion links the GPL-3.0-or-later
[imessage-database library](https://github.com/ReagentX/imessage-exporter/tree/5.0.1/imessage-database)
by Christopher Sardegna. The exact upstream revision and all Rust dependency
versions are recorded in `native/Cargo.lock`. The companion's complete source
is in `native/src/main.rs`, with reproducible build instructions in
[native/README.md](native/README.md). This notice does not assign a new license
to the pre-existing Python project.

The upstream GPL license and notices for linked Cargo packages are included in
`pymessage/data/native-licenses/`. The public upstream decoder test fixtures in
`tests/fixtures/native_cases.json`, `edited.plist`, and `edited_and_unsent.plist`
come from that same pinned repository and are covered by its GPL license. They
contain public test data, not messages obtained from the user's Mac.

Optional legacy HTML export downloads the unmodified upstream executable with
its release SHA-256 digest. Upstream source and license are available at the
[pinned release](https://github.com/ReagentX/imessage-exporter/releases/tag/5.0.1).

Python dependencies retain their respective package licenses and notices.

`pymessage/data/emoji-17.txt` contains the fully-qualified emoji sequences derived
from Unicode's [Emoji 17.0 test data](https://www.unicode.org/Public/17.0.0/emoji/emoji-test.txt).
The source URL and SHA-256 digest are recorded in the generated data header.
Unicode License V3 is reproduced in `pymessage/data/unicode-license.txt` and
included in the package. The data is bundled for offline use.
`pymessage/web/emoji-picker.json` derives its emoji, names, and categories from
the same source and is covered by that same license.
