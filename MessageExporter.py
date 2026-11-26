"""Project-managed imessage-exporter; no Cargo/Homebrew installation needed."""

import hashlib
import os
from pathlib import Path
import platform
import sqlite3
import subprocess
import tempfile


EXPORTER_VERSION = "5.0.1"
PROJECT_ROOT = Path(__file__).resolve().parent
# SHA-256 digests published on the upstream GitHub release assets.
ASSETS = {
    "arm64": (
        "imessage-exporter-aarch64-apple-darwin",
        "8c81117e1cb6d6e724d438e58a7db0a02995834268483a872362fbbdfc011067",
    ),
    "x86_64": (
        "imessage-exporter-x86_64-apple-darwin",
        "091b52e7deac607b2a7b51bbc0d08353ff93d0e8b88114f3057ac6ca0b84a6ad",
    ),
}


class ExportError(RuntimeError):
    """An actionable export/setup failure suitable for CLI output."""


def default_database():
    return Path.home() / "Library" / "Messages" / "chat.db"


def _digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def ensure_exporter(cache_dir=None):
    """Fetch a pinned, verified native binary once, then reuse it offline."""
    if platform.system() != "Darwin" or platform.machine() not in ASSETS:
        raise ExportError(
            "Automatic message export requires an Apple Silicon or Intel Mac. "
            "Use --html-dir to analyze existing HTML exports on another platform."
        )
    asset, expected = ASSETS[platform.machine()]
    root = Path(cache_dir) if cache_dir else PROJECT_ROOT / ".tools"
    destination = root / "imessage-exporter" / EXPORTER_VERSION / asset
    if destination.is_file() and _digest(destination) == expected:
        destination.chmod(0o700)
        return destination.resolve()

    destination.parent.mkdir(parents=True, exist_ok=True)
    url = (
        "https://github.com/ReagentX/imessage-exporter/releases/download/"
        f"{EXPORTER_VERSION}/{asset}"
    )
    print(f"Preparing imessage-exporter {EXPORTER_VERSION} (one-time download)...", flush=True)
    # macOS's system certificate store also works on Python.org installs whose
    # separate certificate bundle has not been set up.
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temp:
        temporary = Path(temp.name)
    try:
        result = subprocess.run(
            [
                "/usr/bin/curl", "--fail", "--location", "--silent", "--show-error",
                "--proto", "=https", "--proto-redir", "=https", "--tlsv1.2",
                "--connect-timeout", "30", "--max-time", "300",
                "--output", str(temporary), url,
            ],
            capture_output=True, text=True,
        )
        if result.returncode:
            raise ExportError(
                "Could not download imessage-exporter from GitHub. Check your internet "
                f"connection and retry. {result.stderr.strip()}"
            )
        if _digest(temporary) != expected:
            raise ExportError("Exporter checksum mismatch; the download was not installed. Retry later.")
        temporary.chmod(0o700)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination.resolve()


def validate_database(db_path):
    """Check availability read-only, without creating or modifying a database."""
    path = Path(db_path).expanduser().resolve()
    try:
        with path.open("rb") as source:
            header = source.read(16)
    except PermissionError as error:
        raise ExportError(
            "macOS blocked access to chat.db. Grant Full Disk Access to the app running "
            "Py-Message (Terminal, your IDE, or Codex) in System Settings > Privacy & "
            "Security > Full Disk Access, restart that app, and retry."
        ) from error
    except FileNotFoundError as error:
        raise ExportError(
            f"No Messages database at {path}. Run this on your Mac with Messages set up, "
            "or supply --db-path /path/to/chat.db."
        ) from error
    if header != b"SQLite format 3\x00":
        raise ExportError(f"{path} is not a SQLite Messages database.")
    try:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            connection.close()
    except sqlite3.Error as error:
        raise ExportError(f"Could not read chat.db: {error}") from error
    if not {"message", "chat", "handle"}.issubset(tables):
        raise ExportError(f"{path} does not contain the required Messages tables.")
    return path


def export_messages(db_path, output_dir, *, conversation=None, start_date=None, end_date=None, cache_dir=None):
    source = validate_database(db_path)
    binary = ensure_exporter(cache_dir)
    destination = Path(output_dir).resolve()
    command = [
        str(binary), "--format", "html", "--copy-method", "disabled",
        "--db-path", str(source), "--export-path", str(destination),
    ]
    for option, value in (
        ("--conversation-filter", conversation),
        ("--start-date", start_date),
        ("--end-date", end_date),
    ):
        if value:
            command.extend([option, value])
    print("Exporting messages from the local database...", flush=True)
    result = subprocess.run(command)
    if result.returncode:
        raise ExportError(
            f"Message export failed (exit {result.returncode}); analysis was not run. "
            "See the exporter error above. For permission errors, grant Full Disk Access "
            "to the app running Py-Message and restart it."
        )
    files = sorted(destination.rglob("*.html"))
    if not files:
        raise ExportError("No conversations were exported. Check the database and date/contact filters.")
    return files
