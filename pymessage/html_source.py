"""Compatibility ingestion. Original HTML is retained alongside parsed records."""
from datetime import timezone
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from .source import APPLE_EPOCH_SECONDS, _normalize_message
from .util import atomic_write, digest, dumps


class PlainCache:
    def get(self, raw):
        return None


def sync_html(archive, directory, run_id, timings, *, zone='UTC', strict=False, rebuild=False, exclude=None):
    import MessageFileToDatabase as converter
    source = Path(directory).expanduser().resolve()
    if not source.is_dir():
        raise RuntimeError(f'HTML export directory does not exist: {source}')
    files = sorted(path for path in source.rglob('*.html') if not exclude or not path.is_relative_to(Path(exclude).resolve()))
    if not files:
        raise RuntimeError(f'No HTML files found in {source}')
    source_id = digest({'kind':'html','path':str(source)})[:24]
    counts = {'source_id':source_id,'added':0,'changed':0,'unchanged':0,'unsupported':0,'errors':0,'files':len(files)}
    db = archive.db
    previous = db.execute('SELECT snapshot_hash FROM sources WHERE id=?',(source_id,)).fetchone()
    try:
        db.execute('BEGIN')
        db.execute('DELETE FROM work.seen')
        db.execute("INSERT INTO sources(id,path,kind,decoder,coverage) VALUES(?,?,?,'html-2','HTML-derived; unavailable source fields remain unknown') ON CONFLICT(id) DO NOTHING", (source_id,str(source),'html'))
        file_hashes = []
        with timings.stage('html_import'):
            for file in files:
                relative = file.relative_to(source).as_posix()
                content = file.read_bytes()
                stamp = digest(content)
                file_hashes.append([relative,stamp])
                snapshot = archive.path.parent/'html-snapshots'/source_id/(stamp+'.html')
                if not snapshot.exists():
                    atomic_write(snapshot,content)
                payload = dumps({'relative_path':relative,'snapshot':str(snapshot),'sha256':stamp,'assumed_timezone':zone})
                file_changed = archive.put_raw(source_id,'html_files',relative,digest(payload.encode()),payload,run_id)
                chat_id = source_id+':html:'+relative
                db.execute('INSERT OR REPLACE INTO chats VALUES(?,?,?,?,?,?)',(chat_id,source_id,relative,None,file.stem,dumps({'html_file':relative})))
                if not file_changed and not rebuild:
                    keys = list(db.execute("SELECT record_key,payload FROM raw_records WHERE source=? AND table_name='message' AND absent=0 AND json_extract(payload,'$._html_file')=?",(source_id,relative)))
                    for row in keys:
                        db.execute('INSERT OR IGNORE INTO work.seen VALUES(?,?)',('message',row['record_key']))
                    counts['unchanged'] += len(keys)
                    continue
                messages = converter.ExtractAllMessages(file)
                prefix = relative+':'
                db.execute("UPDATE import_errors SET resolved=1 WHERE source=? AND stage='html' AND substr(record_key,1,?)=?",(source_id,len(prefix),prefix))
                for index,message in enumerate(messages):
                    identity = message.get('id')
                    timestamp = converter._own_elements(message,'span.timestamp')
                    link = timestamp[0].find('a',href=True) if timestamp else None
                    if link:
                        identity = parse_qs(urlparse(link['href']).query).get('message-guid',[identity])[0]
                    guid = identity.removeprefix('r-') if identity else None
                    key = str(int(digest([relative,guid or index])[:15],16))
                    try:
                        value = converter.MessageToDataEntry(message)
                        sent = any('sent' in tag.get('class',[]) for tag in message.find_all('div',recursive=False))
                        # HTML cannot recover Apple's original integer precision.
                        stamp_ns = int(value['Timestamp'].replace(tzinfo=ZoneInfo(zone)).timestamp())*10**9
                        sender = value['Sender']
                        handle_id = digest(sender)[:20]
                        raw = {'guid':guid,'text':value['Content'],'date':stamp_ns-APPLE_EPOCH_SECONDS*10**9,
                               'is_from_me':int(sent),'handle_id':handle_id,'_html_sender':sender,
                               '_html_file':relative,'_html_index':index,'_html_fragment':str(message),
                               '_assumed_timezone':zone,'_rendered_read_duration':str(value['ReadTime']) if value['ReadTime'] is not None else None}
                        raw_payload = dumps(raw)
                        raw_hash = digest(raw_payload.encode())
                        existing = db.execute('SELECT 1 FROM messages WHERE source=? AND source_rowid=?',(source_id,int(key))).fetchone()
                        changed = archive.put_raw(source_id,'message',key,raw_hash,raw_payload,run_id)
                        if changed or rebuild:
                            _normalize_message(archive,source_id,key,raw_hash,raw,PlainCache(),run_id,strict)
                            counts['changed' if existing else 'added'] += 1
                        else:
                            counts['unchanged'] += 1
                        db.execute('INSERT OR REPLACE INTO participants VALUES(?,?,?,?)',(source_id,handle_id,sender,'{}'))
                        if not sent:
                            db.execute('INSERT OR IGNORE INTO chat_participants VALUES(?,?,?)',(chat_id,source_id,handle_id))
                        db.execute('INSERT OR IGNORE INTO memberships VALUES(?,?,0)',(chat_id,source_id+':'+key))
                    except (ValueError,TypeError) as error:
                        counts['errors'] += 1
                        archive.error(run_id,source_id,relative+':'+str(index),'html',error,stamp)
                        if strict:
                            raise
            db.execute("UPDATE raw_records SET absent=1 WHERE source=? AND NOT EXISTS(SELECT 1 FROM work.seen s WHERE s.table_name=raw_records.table_name AND s.record_key=raw_records.record_key)",(source_id,))
            db.execute("UPDATE messages SET source_present=0 WHERE source=? AND CAST(source_rowid AS TEXT) IN (SELECT record_key FROM raw_records WHERE source=? AND table_name='message' AND absent=1)",(source_id,source_id))
            # Old directory-import file names can be retained as history, while
            # current presence controls the default view.
            stamp = digest([file_hashes,zone])
            counts['snapshot_unchanged'] = bool(previous and previous['snapshot_hash']==stamp and not rebuild)
            if not counts['snapshot_unchanged']:
                db.execute('UPDATE sources SET snapshot_hash=?,decoder=?,last_success=? WHERE id=?',
                           (stamp,'html-2',run_id,source_id))
        db.commit()
        return counts
    except BaseException:
        db.rollback()
        raise
