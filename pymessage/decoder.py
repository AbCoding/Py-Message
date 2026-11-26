"""Verified bundled native decoder and durable, content-addressed decode cache."""
from functools import lru_cache
from importlib.resources import files
import json
from pathlib import Path
import platform
import sqlite3
import subprocess
import tempfile

from .util import digest, dumps, file_digest


@lru_cache(maxsize=1)
def native_binary():
    if platform.system() != 'Darwin':
        return None
    root = Path(str(files('pymessage').joinpath('bin')))
    manifest = root / 'manifest.json'
    if not manifest.exists():
        return None
    data = json.loads(manifest.read_text())
    asset = data.get(platform.machine())
    if not asset:
        return None
    path = root / asset['file']
    if not path.is_file() or file_digest(path) != asset['sha256']:
        raise RuntimeError('Bundled decoder is missing or failed its checksum. Reinstall Py-Message.')
    return path


def identity():
    binary = native_binary()
    return 'native-protocol-1:' + file_digest(binary) if binary else 'raw-only-1'


def body_hash(raw):
    return digest({key: raw.get(key) for key in ('text', 'attributedBody', 'message_summary_info', 'payload_data',
                  'date_edited','balloon_bundle_id','associated_message_type','item_type','guid','date','is_from_me','handle_id')})


def needs_native(raw):
    return bool(raw.get('attributedBody') or raw.get('message_summary_info'))


class DecodeCache:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS decoded (key TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        self.db.commit()
        self.version = identity()

    def key(self, raw):
        return self.version + ':' + body_hash(raw)

    def get(self, raw):
        row = self.db.execute('SELECT payload FROM decoded WHERE key=?', (self.key(raw),)).fetchone()
        return json.loads(row[0]) if row else None

    def close(self):
        self.db.close()

    def decode_pending(self, archive, snapshot, directory):
        """Stream only uncached changed bodies; checkpoints survive archive rollback."""
        binary = native_binary()
        if not binary:
            return 0
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=directory, prefix='decode-') as temp:
            ids_path = Path(temp) / 'ids.txt'
            count = 0
            with ids_path.open('w') as ids:
                for row in archive.db.execute('SELECT rowid,payload FROM work.pending ORDER BY rowid'):
                    raw = json.loads(row['payload'])
                    if needs_native(raw) and self.get(raw) is None:
                        ids.write(str(row['rowid']) + '\n')
                        count += 1
            if not count:
                return 0
            # stderr goes to a file to avoid pipe deadlocks and unbounded buffers.
            with (Path(temp) / 'stderr.txt').open('w+') as errors:
                with subprocess.Popen([str(binary), str(snapshot), str(ids_path)], stdout=subprocess.PIPE,
                                      stderr=errors, text=True, encoding='utf-8') as process:
                    try:
                        for line in process.stdout:
                            value = json.loads(line)
                            if value.get('protocol') != 1 or not isinstance(value.get('rowid'), int):
                                raise RuntimeError('Invalid native decoder protocol record.')
                            pending = archive.db.execute('SELECT payload FROM work.pending WHERE rowid=?', (value['rowid'],)).fetchone()
                            if pending is None:
                                raise RuntimeError('Decoder returned an unrequested message ID.')
                            raw = json.loads(pending[0])
                            value.pop('rowid', None)
                            value.pop('guid', None)
                            self.db.execute('INSERT OR REPLACE INTO decoded VALUES(?,?)', (self.key(raw), dumps(value)))
                            # Commit bounded batches; a failed import can reuse completed decodes.
                            if self.db.total_changes % 200 == 0:
                                self.db.commit()
                        status = process.wait()
                    except BaseException:
                        process.terminate()
                        process.wait()
                        self.db.commit()
                        raise
                self.db.commit()
                if status:
                    errors.seek(0)
                    # Ingestion quarantines uncached rows while preserving source bytes.
                    return count
            return count
