"""Versioned local store. Source rows and observed revisions remain recoverable."""
import json
from pathlib import Path
import sqlite3
from .util import dumps

SCHEMA_VERSION = 2
SCHEMA = '''
CREATE TABLE sources (
 id TEXT PRIMARY KEY, path TEXT NOT NULL, kind TEXT NOT NULL, schema_json TEXT,
 snapshot TEXT, snapshot_hash TEXT, decoder TEXT, last_success TEXT, coverage TEXT NOT NULL DEFAULT 'complete'
);
CREATE TABLE raw_records (
 source TEXT NOT NULL, table_name TEXT NOT NULL, record_key TEXT NOT NULL,
 hash TEXT NOT NULL, payload TEXT NOT NULL, absent INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(source, table_name, record_key)
);
CREATE TABLE revisions (
 id INTEGER PRIMARY KEY, source TEXT NOT NULL, table_name TEXT NOT NULL, record_key TEXT NOT NULL,
 hash TEXT NOT NULL, payload TEXT NOT NULL, observed_run TEXT NOT NULL,
 UNIQUE(source,table_name,record_key,hash)
);
CREATE TABLE messages (
 id TEXT PRIMARY KEY, source TEXT NOT NULL, source_rowid INTEGER NOT NULL, guid TEXT,
 timestamp_ns INTEGER, text TEXT, sender TEXT NOT NULL, handle_id TEXT, is_from_me INTEGER,
 kind TEXT NOT NULL, thread_guid TEXT, associated_guid TEXT, service TEXT, raw_hash TEXT NOT NULL,
 decoded TEXT NOT NULL, decode_status TEXT NOT NULL, source_present INTEGER NOT NULL DEFAULT 1,
 UNIQUE(source,source_rowid)
);
CREATE INDEX messages_time ON messages(timestamp_ns);
CREATE INDEX messages_source_guid ON messages(source,guid);
CREATE TABLE chats (
 id TEXT PRIMARY KEY, source TEXT NOT NULL, source_rowid TEXT NOT NULL, guid TEXT, name TEXT, metadata TEXT NOT NULL
);
CREATE TABLE participants (
 source TEXT NOT NULL, handle_id TEXT NOT NULL, address TEXT, metadata TEXT NOT NULL,
 PRIMARY KEY(source,handle_id)
);
CREATE TABLE chat_participants (
 chat_id TEXT NOT NULL, source TEXT NOT NULL, handle_id TEXT NOT NULL, PRIMARY KEY(chat_id,source,handle_id)
);
CREATE TABLE memberships (
 chat_id TEXT NOT NULL, message_id TEXT NOT NULL, recovered INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(chat_id,message_id)
);
CREATE INDEX memberships_message ON memberships(message_id);
CREATE TABLE parts (message_id TEXT NOT NULL, part_index INTEGER NOT NULL, data TEXT NOT NULL, PRIMARY KEY(message_id,part_index));
CREATE TABLE edits (message_id TEXT NOT NULL, part_index INTEGER NOT NULL, data TEXT NOT NULL, PRIMARY KEY(message_id,part_index));
CREATE TABLE receipts (message_id TEXT PRIMARY KEY, sent_raw, delivered_raw, read_raw, edited_raw, retracted_raw, data TEXT NOT NULL);
CREATE TABLE reactions (message_id TEXT PRIMARY KEY, target_guid TEXT, action TEXT, type_code INTEGER, emoji TEXT);
CREATE TABLE attachments (
 id TEXT PRIMARY KEY, source TEXT NOT NULL, source_rowid TEXT NOT NULL, path TEXT, available INTEGER NOT NULL,
 archived_path TEXT, sha256 TEXT, metadata TEXT NOT NULL
);
CREATE TABLE message_attachments (message_id TEXT NOT NULL, attachment_id TEXT NOT NULL, PRIMARY KEY(message_id,attachment_id));
CREATE TABLE import_errors (
 id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, source TEXT, record_key TEXT, stage TEXT NOT NULL, error TEXT NOT NULL, raw_hash TEXT
 ,resolved INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE message_statistics (message_id TEXT PRIMARY KEY,version TEXT NOT NULL,data TEXT NOT NULL);
CREATE TABLE structured_payloads (message_id TEXT NOT NULL,field TEXT NOT NULL,format TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(message_id,field));
CREATE TABLE runs (
 id TEXT PRIMARY KEY, started TEXT NOT NULL, finished TEXT, status TEXT NOT NULL, config TEXT NOT NULL, summary TEXT
);
CREATE TABLE reports (cache_key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, context_stamp TEXT NOT NULL, path TEXT NOT NULL, summary TEXT NOT NULL);
'''


class Archive:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        tables = {row[0] for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if tables and not {'sources','raw_records','messages','runs'}.issubset(tables):
            self.db.close()
            raise RuntimeError('The archive path contains another database. Choose a new --archive path; source data was not modified.')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA busy_timeout=10000')
        self.db.execute('PRAGMA temp_store=FILE')
        version = self.db.execute('PRAGMA user_version').fetchone()[0]
        if version > SCHEMA_VERSION:
            self.db.close()
            raise RuntimeError(f'Archive schema {version} is newer than this program supports.')
        if version == 0:
            self.db.executescript('BEGIN;\n' + SCHEMA + f'PRAGMA user_version={SCHEMA_VERSION};\nCOMMIT;')
        elif version == 1:
            self.db.executescript('''BEGIN;
                ALTER TABLE import_errors ADD COLUMN resolved INTEGER NOT NULL DEFAULT 0;
                CREATE TABLE message_statistics (message_id TEXT PRIMARY KEY,version TEXT NOT NULL,data TEXT NOT NULL);
                CREATE TABLE structured_payloads (message_id TEXT NOT NULL,field TEXT NOT NULL,format TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(message_id,field));
                UPDATE sources SET decoder=NULL;
                PRAGMA user_version=2;
                COMMIT;''')
        self.db.execute('CREATE INDEX IF NOT EXISTS import_errors_record ON import_errors(source,record_key,stage)')
        self.db.execute('CREATE INDEX IF NOT EXISTS messages_coverage ON messages(decode_status)')
        self.db.execute('CREATE INDEX IF NOT EXISTS messages_source_presence ON messages(source,source_present)')
        self.db.execute('CREATE TABLE IF NOT EXISTS view_cache (key TEXT PRIMARY KEY,context_stamp TEXT NOT NULL,selection TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS media_observations (id TEXT PRIMARY KEY,signature TEXT NOT NULL)')
        # Empty filename creates an automatically removed disk-backed staging
        # database. Large imports must not duplicate every raw row in RAM.
        self.db.execute("ATTACH DATABASE '' AS work")
        self.db.execute('PRAGMA work.journal_mode=OFF')
        self.db.execute('PRAGMA work.synchronous=OFF')
        self.db.execute('CREATE TABLE work.seen (table_name TEXT, record_key TEXT, PRIMARY KEY(table_name,record_key)) WITHOUT ROWID')
        self.db.execute('CREATE TABLE work.pending (rowid INTEGER PRIMARY KEY, raw_hash TEXT, payload TEXT)')
        self.db.commit()

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def raw(self, source, table, *, present=True):
        sql = 'SELECT record_key,hash,payload FROM raw_records WHERE source=? AND table_name=?'
        if present:
            sql += ' AND absent=0'
        for row in self.db.execute(sql, (source, table)):
            yield row['record_key'], row['hash'], json.loads(row['payload'])

    def error(self, run_id, source, key, stage, error, raw_hash=None):
        self.db.execute('INSERT INTO import_errors(run_id,source,record_key,stage,error,raw_hash) VALUES(?,?,?,?,?,?)',
                        (run_id, source, str(key), stage, str(error), raw_hash))

    def put_raw(self, source, table, key, raw_hash, payload, run_id):
        old = self.db.execute('SELECT hash,payload,absent FROM raw_records WHERE source=? AND table_name=? AND record_key=?', (source, table, key)).fetchone()
        self.db.execute('INSERT INTO work.seen VALUES(?,?)', (table, key))
        if old and old['hash'] == raw_hash and not old['absent']:
            return False
        if old:
            self.db.execute('INSERT OR IGNORE INTO revisions(source,table_name,record_key,hash,payload,observed_run) VALUES(?,?,?,?,?,?)',
                            (source, table, key, old['hash'], old['payload'], run_id))
        self.db.execute('INSERT INTO raw_records(source,table_name,record_key,hash,payload,absent) VALUES(?,?,?,?,?,0) '
                        'ON CONFLICT(source,table_name,record_key) DO UPDATE SET hash=excluded.hash,payload=excluded.payload,absent=0',
                        (source, table, key, raw_hash, payload))
        return True

    def manifest(self, run_id, summary, status, finished):
        self.db.execute('UPDATE runs SET summary=?,status=?,finished=? WHERE id=?', (dumps(summary), status, finished, run_id))
        self.db.commit()
