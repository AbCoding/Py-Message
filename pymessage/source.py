"""Read-only consistent source acquisition and full-row reconciliation."""
from datetime import datetime, timezone
import base64
import hashlib
import json
import os
import plistlib
from pathlib import Path
import sqlite3
import time
import tempfile

from .decoder import DecodeCache, identity as decoder_identity, needs_native
from .util import atomic_output, digest, dumps, file_digest, quote

APPLE_EPOCH_SECONDS = 978307200
IMPORT_VERSION = 'structured-2'


def utc_ns(value, *, receipt=False):
    if value is None or isinstance(value, bool) or (receipt and value == 0):
        return None
    try:
        if not isinstance(value, (int, float)):
            return None
        # Apple schemas use seconds in older databases and nanoseconds in newer ones.
        result = int(value) if abs(value) >= 10**12 else int(value * 10**9)
        result += APPLE_EPOCH_SECONDS * 10**9
        datetime.fromtimestamp(result // 10**9, timezone.utc)
        return result if -(2**63) <= result < 2**63 else None
    except (ValueError, OverflowError, OSError):
        return None


def snapshot_database(path, destination, timeout=60):
    source = Path(path).expanduser().resolve()
    stage = 'source'
    try:
        # Surface macOS EPERM directly; SQLite otherwise hides it behind the
        # ambiguous "unable to open database file". Do not read message data.
        try:
            with source.open('rb'):
                pass
        except FileNotFoundError as error:
            raise RuntimeError(f'No Messages database at {source}. Supply --db-path or set up Messages on this Mac.') from error
        # Opening read-only also ensures a typo never creates a source database.
        connection = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=5)
        try:
            tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'message' not in tables:
                raise RuntimeError('Source is not a Messages database: message table is missing.')
            deadline = time.monotonic() + timeout
            def progress(status, remaining, total):
                if time.monotonic() > deadline:
                    raise RuntimeError('Snapshot timed out while the source was busy. Close other database tools and retry.')
            stage = 'snapshot'
            with atomic_output(destination) as temporary:
                target = sqlite3.connect(temporary)
                try:
                    connection.backup(target, pages=2048, progress=progress, sleep=0.05)
                    if target.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                        raise RuntimeError('Source snapshot failed SQLite integrity validation.')
                finally:
                    target.close()
        finally:
            connection.close()
    except (PermissionError, sqlite3.OperationalError) as error:
        if stage == 'snapshot':
            raise RuntimeError(f'Could not create the Messages snapshot at {destination}. '
                               'The source opened successfully. Check that the output folder is writable '
                               'and has free space, or choose another --output folder. '
                               f'Details: {error}') from error
        blocked = isinstance(error, PermissionError)
        reason = 'macOS denied access to' if blocked else 'Cannot open'
        raise RuntimeError(
            f'{reason} the Messages database at {source}.\n'
            'Open System Settings > Privacy & Security > Full Disk Access and enable Py-Message.app or the app '
            'that launched the source version (Terminal, your IDE, or Codex). Fully quit and reopen '
            'that app, then run ./run.sh app again and click Load new data. '
            'Giving access to the browser does not grant access to the Python server.\n'
            'For a copied database, check --db-path and file permissions. '
            f'Details: {error}') from error
    return source


def source_schema(connection):
    return [dict(row) for row in connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")]


def record_stream(connection, table):
    info = list(connection.execute(f'PRAGMA table_info({quote(table)})'))
    try:
        cursor = connection.execute(f'SELECT rowid AS __source_rowid__, * FROM {quote(table)}')
        has_rowid = True
    except sqlite3.OperationalError:
        cursor = connection.execute(f'SELECT * FROM {quote(table)}')
        has_rowid = False
    primary = [row['name'] for row in sorted(info, key=lambda r: r['pk']) if row['pk']]
    while True:
        batch = cursor.fetchmany(500)
        if not batch:
            break
        for row in batch:
            raw = dict(row)
            rowid = raw.pop('__source_rowid__', None)
            key = str(rowid) if has_rowid else dumps([raw[c] for c in primary])
            if not has_rowid and not primary:
                key = digest(raw)
            yield key, raw


def message_kind(raw):
    associated = raw.get('associated_message_type') or 0
    if isinstance(associated, int) and (associated == 1000 or 2000 <= associated < 4000):
        return 'reaction'
    if raw.get('item_type') or raw.get('is_system_message') or raw.get('group_action_type'):
        return 'announcement'
    return 'message'


def associated_target(value):
    if not isinstance(value,str) or not value:
        return None,None
    if value.startswith('p:') and '/' in value:
        part,guid = value[2:].split('/',1)
        return guid,int(part) if part.isdigit() else None
    if value.startswith('bp:'):
        return value[3:],0
    return value,0


def message_identity(source, rowid, raw):
    # GUID is retained explicitly; physical source row ID prevents destroying
    # duplicate rows whose GUID happens to be identical.
    return source + ':' + str(rowid)


def _normalize_message(archive, source_id, key, raw_hash, raw, cache, run_id, strict):
    db = archive.db
    db.execute("UPDATE import_errors SET resolved=1 WHERE source=? AND record_key=? AND stage IN ('decode','timestamp')",(source_id,key))
    decoded = cache.get(raw) if needs_native(raw) else None
    status = 'decoded' if decoded and not decoded.get('error') else 'plain'
    if needs_native(raw) and (decoded is None or decoded.get('error')):
        status = 'unsupported'
    if decoded and not decoded.get('text') and raw.get('attributedBody') and not decoded.get('parts') and not decoded.get('edits'):
        status = 'unsupported'
    if decoded and raw.get('date_edited') and raw.get('message_summary_info') and not decoded.get('edits'):
        status = 'unsupported'
    if status == 'unsupported':
        reason = (decoded or {}).get('error') or 'Body could not be decoded; original source fields and blobs retained.'
        archive.error(run_id, source_id, key, 'decode', reason, raw_hash)
        if strict:
            raise RuntimeError(f'Message {key} could not be decoded in strict mode.')
    if decoded is None:
        plain = raw.get('text') if isinstance(raw.get('text'), str) else None
        decoded = {'protocol': 1, 'text': plain,
                   'parts': [{'kind':'text','text':plain}] if plain else [], 'edits': None}
    text = decoded.get('text')
    if text is None and status == 'unsupported' and isinstance(raw.get('text'),str):
        text = raw['text']
    # Edited bodies can exist only in the edit history; compose the latest part
    # text while preserving every historical event separately.
    if decoded.get('edits'):
        current = []
        for part in decoded['edits']:
            if part['status'] == 'Unsent':
                continue
            if part['events']:
                current.append(part['events'][-1]['text'])
            elif part['status'] == 'Original':
                index = part['part']
                components = decoded.get('parts') or []
                if index < len(components):
                    current.append(''.join(r.get('text') or '' for r in components[index].get('ranges',[])))
        text = '\n'.join(value for value in current if value) or None
    handle = str(raw['handle_id']) if raw.get('handle_id') is not None else None
    sender = 'Me' if raw.get('is_from_me') else (handle or 'Unknown')
    stamp = utc_ns(raw.get('date'))
    if stamp is None:
        archive.error(run_id, source_id, key, 'timestamp', 'Missing or invalid timestamp; message retained outside time-bounded views.', raw_hash)
        if strict:
            raise RuntimeError(f'Message {key} has an invalid timestamp.')
    identifier = message_identity(source_id, key, raw)
    previous_text = db.execute('SELECT text FROM messages WHERE id=?',(identifier,)).fetchone()
    if previous_text and previous_text[0] != text:
        db.execute('DELETE FROM message_statistics WHERE message_id=?',(identifier,))
    db.execute('''INSERT INTO messages(id,source,source_rowid,guid,timestamp_ns,text,sender,handle_id,is_from_me,kind,
        thread_guid,associated_guid,service,raw_hash,decoded,decode_status,source_present) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
        ON CONFLICT(id) DO UPDATE SET guid=excluded.guid,timestamp_ns=excluded.timestamp_ns,text=excluded.text,
        sender=excluded.sender,handle_id=excluded.handle_id,is_from_me=excluded.is_from_me,kind=excluded.kind,
        thread_guid=excluded.thread_guid,associated_guid=excluded.associated_guid,service=excluded.service,
        raw_hash=excluded.raw_hash,decoded=excluded.decoded,decode_status=excluded.decode_status,source_present=1''',
        (identifier,source_id,int(key),raw.get('guid'),stamp,text,sender,handle,raw.get('is_from_me'),message_kind(raw),
         raw.get('thread_originator_guid'),raw.get('associated_message_guid'),raw.get('service'),raw_hash,dumps(decoded),status))
    for table in ('parts', 'edits', 'receipts', 'reactions'):
        db.execute(f'DELETE FROM {table} WHERE message_id=?', (identifier,))
    db.execute('DELETE FROM structured_payloads WHERE message_id=?',(identifier,))
    for field in ('message_summary_info','payload_data'):
        value = raw.get(field)
        if isinstance(value,dict) and '$bytes' in value:
            blob = base64.b64decode(value['$bytes'])
            try:
                payload = plistlib.loads(blob)
                db.execute('INSERT INTO structured_payloads VALUES(?,?,?,?)',(identifier,field,'plist',dumps(payload)))
            except (plistlib.InvalidFileException,ValueError,TypeError,OverflowError):
                pass  # Source bytes remain in raw_records, including non-plist payloads.
    for index, part in enumerate(decoded.get('parts') or []):
        db.execute('INSERT INTO parts VALUES(?,?,?)', (identifier,index,dumps(part)))
    for index, part in enumerate(decoded.get('edits') or []):
        db.execute('INSERT INTO edits VALUES(?,?,?)', (identifier,index,dumps(part)))
    receipt = {name: raw.get(name) for name in ('date','date_delivered','date_read','date_edited','date_retracted')}
    db.execute('INSERT INTO receipts VALUES(?,?,?,?,?,?,?)', (identifier,*receipt.values(),dumps({k:utc_ns(v,receipt=k!='date') for k,v in receipt.items()})))
    if message_kind(raw) == 'reaction':
        code = raw.get('associated_message_type')
        target,_ = associated_target(raw.get('associated_message_guid'))
        db.execute('INSERT INTO reactions VALUES(?,?,?,?,?)', (identifier,target,
                    'remove' if code >= 3000 else 'add',code,raw.get('associated_message_emoji')))
    return status


def _relationships(archive, source_id):
    db = archive.db
    # Reconcile metadata without deleting preserved absent message rows/history.
    db.execute('DELETE FROM memberships WHERE message_id IN (SELECT id FROM messages WHERE source=? AND source_present=1)',(source_id,))
    for key, _, raw in archive.raw(source_id, 'handle'):
        db.execute('INSERT OR REPLACE INTO participants VALUES(?,?,?,?)', (source_id,key,raw.get('id'),dumps(raw)))
    for key, _, raw in archive.raw(source_id, 'chat'):
        name = raw.get('display_name') or raw.get('chat_identifier') or raw.get('guid') or 'Conversation ' + key
        chat_id = source_id+':chat:'+key
        db.execute('DELETE FROM chat_participants WHERE chat_id=?',(chat_id,))
        db.execute('INSERT OR REPLACE INTO chats VALUES(?,?,?,?,?,?)', (chat_id,source_id,key,raw.get('guid'),name,dumps(raw)))
    orphan = source_id + ':chat:orphaned'
    db.execute('INSERT OR IGNORE INTO chats VALUES(?,?,?,?,?,?)', (orphan,source_id,'orphaned',None,'Orphaned messages','{}'))
    for _, _, raw in archive.raw(source_id, 'chat_handle_join'):
        db.execute('INSERT OR IGNORE INTO chat_participants VALUES(?,?,?)', (source_id+':chat:'+str(raw.get('chat_id')),source_id,str(raw.get('handle_id'))))
    for table, recovered in (('chat_message_join',0),('chat_recoverable_message_join',1)):
        for _, _, raw in archive.raw(source_id, table):
            db.execute('INSERT OR IGNORE INTO memberships VALUES(?,?,?)', (source_id+':chat:'+str(raw.get('chat_id')),source_id+':'+str(raw.get('message_id')),recovered))
    db.execute('INSERT OR IGNORE INTO memberships SELECT ?,id,0 FROM messages m WHERE source=? AND NOT EXISTS(SELECT 1 FROM memberships j WHERE j.message_id=m.id)', (orphan,source_id))
    # Resolve display addresses in queries; this keeps handle identity independent
    # of labels and prevents renames from rewriting every message.


def _copy_original(path, media_dir):
    media_dir = Path(media_dir)
    media_dir.mkdir(parents=True,exist_ok=True)
    before = path.stat()
    descriptor,name = tempfile.mkstemp(prefix='.copy-',dir=media_dir)
    temporary = Path(name)
    checksum = hashlib.sha256()
    try:
        with os.fdopen(descriptor,'wb') as target, path.open('rb') as source:
            for chunk in iter(lambda:source.read(1024*1024),b''):
                target.write(chunk)
                checksum.update(chunk)
            target.flush()
            os.fsync(target.fileno())
        after = path.stat()
        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
            raise OSError('Attachment changed during acquisition; retry the sync.')
        value = checksum.hexdigest()
        destination = media_dir/value
        if not destination.exists() or destination.stat().st_size != temporary.stat().st_size:
            os.replace(temporary,destination)
        return value,str(destination)
    finally:
        temporary.unlink(missing_ok=True)


def _attachments(archive, source_id, media_dir, archive_media, attachment_root, run_id=None):
    db = archive.db
    changed = False
    for key, _, raw in archive.raw(source_id, 'attachment'):
        identifier = source_id + ':attachment:' + key
        original = raw.get('filename')
        path = Path(original).expanduser() if isinstance(original, str) and original else None
        if path and attachment_root:
            try:
                suffix = path.parts[path.parts.index('Attachments')+1:]
                path = Path(attachment_root).expanduser().joinpath(*suffix)
            except ValueError:
                path = Path(attachment_root).expanduser() / path.name
        available = False
        try:
            available = bool(path and path.is_file())
        except OSError:
            pass
        previous = db.execute('SELECT * FROM attachments WHERE id=?', (identifier,)).fetchone()
        stored = previous['archived_path'] if previous else None
        checksum = previous['sha256'] if previous else None
        if archive_media and available:
            try:
                stat = path.stat()
                signature = dumps([str(path),stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns])
                old_signature = db.execute('SELECT signature FROM media_observations WHERE id=?',(identifier,)).fetchone()
                if not (old_signature and old_signature[0]==signature and stored and Path(stored).is_file()):
                    checksum,stored = _copy_original(path,media_dir)
                    db.execute('INSERT OR REPLACE INTO media_observations VALUES(?,?)',(identifier,signature))
                db.execute("UPDATE import_errors SET resolved=1 WHERE source=? AND record_key=? AND stage='media'",(source_id,'attachment:'+key))
            except OSError as error:
                if run_id:
                    archive.error(run_id,source_id,'attachment:'+key,'media',error)
        current = (identifier,source_id,key,str(path) if path else None,int(available),stored,checksum,dumps(raw))
        if previous is None or tuple(previous)!=current:
            db.execute('INSERT OR REPLACE INTO attachments VALUES(?,?,?,?,?,?,?,?)',current)
            changed = True
    db.execute('DELETE FROM message_attachments WHERE message_id IN (SELECT id FROM messages WHERE source=? AND source_present=1)', (source_id,))
    for _, _, raw in archive.raw(source_id, 'message_attachment_join'):
        db.execute('INSERT OR IGNORE INTO message_attachments VALUES(?,?)', (source_id+':'+str(raw.get('message_id')),source_id+':attachment:'+str(raw.get('attachment_id'))))
    return changed


def sync_database(archive, path, state_dir, run_id, timings, *, strict=False, rebuild=False,
                  archive_media=False, attachment_root=None, retain_snapshots=False, source_name=None):
    state_dir = Path(state_dir)
    source_path = Path(path).expanduser().resolve()
    source_id = digest({'kind':'sqlite','identity':source_name or str(source_path)})[:24]
    snapshots = state_dir / 'snapshots'
    snapshots.mkdir(parents=True, exist_ok=True)
    pending = snapshots / (source_id + '-' + run_id + '.db')
    with timings.stage('snapshot'):
        snapshot_database(source_path, pending)
        snapshot_hash = file_digest(pending)
    version = IMPORT_VERSION+':'+decoder_identity()
    previous = archive.db.execute('SELECT * FROM sources WHERE id=?', (source_id,)).fetchone()
    counts = {'source_id':source_id,'added':0,'changed':0,'unchanged':0,'absent':0,'decoded':0,'unsupported':0,'raw_records_changed':0}
    if previous and previous['snapshot_hash'] == snapshot_hash and previous['decoder'] == version and not rebuild:
        if previous['path'] != str(source_path):
            archive.db.execute('UPDATE sources SET path=? WHERE id=?',(str(source_path),source_id))
            archive.db.commit()
        counts['unchanged'] = archive.db.execute('SELECT COUNT(*) FROM messages WHERE source=? AND source_present=1', (source_id,)).fetchone()[0]
        if archive_media:
            with timings.stage('media'):
                changed = _attachments(archive,source_id,state_dir/'media',True,attachment_root,run_id)
                if changed:
                    archive.db.execute('UPDATE sources SET last_success=? WHERE id=?',(run_id,source_id))
                archive.db.commit()
        pending.unlink()
        counts['snapshot_unchanged'] = True
        return counts
    source = sqlite3.connect(pending.as_uri() + '?mode=ro', uri=True)
    source.row_factory = sqlite3.Row
    cache = DecodeCache(state_dir / 'decode-cache.sqlite')
    try:
        archive.db.execute('BEGIN')
        archive.db.execute('DELETE FROM work.seen')
        archive.db.execute('DELETE FROM work.pending')
        schema = source_schema(source)
        archive.db.execute('INSERT INTO sources(id,path,kind,schema_json) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET path=excluded.path,schema_json=excluded.schema_json',
                           (source_id,str(source_path),'sqlite',dumps(schema)))
        with timings.stage('reconcile'):
            tables = [r['name'] for r in schema if r['type']=='table' and not r['name'].startswith('sqlite_')]
            for table in tables:
                for key, raw in record_stream(source,table):
                    payload = dumps(raw)
                    raw_hash = digest(payload.encode('utf-8'))
                    changed = archive.put_raw(source_id,table,key,raw_hash,payload,run_id)
                    counts['raw_records_changed'] += int(changed)
                    if table == 'message':
                        existing = archive.db.execute('SELECT raw_hash FROM messages WHERE source=? AND source_rowid=?', (source_id,int(key))).fetchone()
                        if changed or rebuild or existing is None or (previous and previous['decoder'] != version):
                            archive.db.execute('INSERT INTO work.pending VALUES(?,?,?)', (int(key),raw_hash,payload))
                            counts['changed' if existing else 'added'] += 1
                        else:
                            counts['unchanged'] += 1
            archive.db.execute('UPDATE raw_records SET absent=1 WHERE source=? AND absent=0 AND NOT EXISTS(SELECT 1 FROM work.seen s WHERE s.table_name=raw_records.table_name AND s.record_key=raw_records.record_key)', (source_id,))
            counts['absent'] = archive.db.execute("UPDATE messages SET source_present=0 WHERE source=? AND source_present=1 AND EXISTS(SELECT 1 FROM raw_records r WHERE r.source=messages.source AND r.table_name='message' AND r.record_key=CAST(messages.source_rowid AS TEXT) AND r.absent=1)", (source_id,)).rowcount
        with timings.stage('decode'):
            counts['decode_requested'] = cache.decode_pending(archive,pending,state_dir)
            for row in archive.db.execute('SELECT rowid,raw_hash,payload FROM work.pending ORDER BY rowid'):
                status = _normalize_message(archive,source_id,str(row['rowid']),row['raw_hash'],json.loads(row['payload']),cache,run_id,strict)
                counts['unsupported' if status=='unsupported' else 'decoded'] += 1
        with timings.stage('relationships'):
            _relationships(archive,source_id)
        with timings.stage('media'):
            _attachments(archive,source_id,state_dir/'media',archive_media,attachment_root,run_id)
        counts['reconciliation'] = {
            'source_message_rows':source.execute('SELECT COUNT(*) FROM message').fetchone()[0],
            'archived_present_rows':archive.db.execute('SELECT COUNT(*) FROM messages WHERE source=? AND source_present=1',(source_id,)).fetchone()[0],
        }
        target = pending
        # Snapshot names are published only after the canonical data transaction
        # succeeds. The source snapshot itself is already complete and validated.
        archive.db.execute('UPDATE sources SET snapshot=?,snapshot_hash=?,decoder=?,last_success=? WHERE id=?',
                           (str(target),snapshot_hash,version,run_id,source_id))
        archive.db.commit()
        if not retain_snapshots and previous and previous['snapshot'] and previous['snapshot'] != str(target):
            Path(previous['snapshot']).unlink(missing_ok=True)
        counts['snapshot_unchanged'] = False
        return counts
    except BaseException:
        archive.db.rollback()
        # Keep the complete acquisition snapshot and decode checkpoints to aid
        # recovery. No partial canonical records have been published.
        raise
    finally:
        cache.close()
        source.close()
