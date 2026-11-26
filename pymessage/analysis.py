"""Streaming analysis over stable archive views, independent of exports."""
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta, timezone
import hashlib
import json
from zoneinfo import ZoneInfo

from .text import TOKENIZER_VERSION, common_words, statistics
from .util import digest, dumps

ANALYSIS_VERSION = '7'


@dataclass
class Config:
    timezone: str = 'UTC'
    conversation: str = None
    start_date: str = None
    end_date: str = None
    group_by: str = 'week'
    separate_by_sender: bool = False
    filter_common_words: bool = False
    include_absent: bool = False
    charts: str = 'top'
    top: int = 20
    max_words: int = 300
    response_gap_hours: float = 24.0
    export_csv: bool = False

    def key(self):
        data = asdict(self)
        data['analysis_version'] = ANALYSIS_VERSION
        data['tokenizer'] = TOKENIZER_VERSION
        if self.filter_common_words:
            data['dictionary'] = digest(sorted(common_words()))
        return digest(data)

    def bounds(self):
        zone = ZoneInfo(self.timezone)
        return tuple(int(datetime.combine(datetime.fromisoformat(value).date(), time(), zone).timestamp()) * 10**9 if value else None
                     for value in (self.start_date, self.end_date))


def where(config, alias='m'):
    clauses, params = [], []
    if not config.include_absent:
        clauses.append(f'{alias}.source_present=1')
    for operator, bound in zip(('>=','<'), config.bounds()):
        if bound is not None:
            clauses.append(f'{alias}.timestamp_ns {operator} ?')
            params.append(bound)
    return (' AND '.join(clauses) or '1'), params


def conversations(archive, config):
    condition, params = where(config)
    query = f'''SELECT c.*,COUNT(DISTINCT m.id) AS records FROM chats c
        JOIN memberships j ON j.chat_id=c.id JOIN messages m ON m.id=j.message_id
        WHERE {condition} GROUP BY c.id ORDER BY records DESC,c.id'''
    for chat in archive.db.execute(query, params):
        if config.conversation:
            addresses = [r[0] or '' for r in archive.db.execute('''SELECT p.address FROM participants p
                JOIN chat_participants cp ON cp.source=p.source AND cp.handle_id=p.handle_id WHERE cp.chat_id=?''', (chat['id'],))]
            terms = [t.strip().casefold() for t in config.conversation.split(',') if t.strip()]
            if not any(term in value.casefold() for term in terms for value in [chat['name'] or '',chat['guid'] or '',*addresses]):
                continue
        yield dict(chat)


def iter_messages(archive, chat_id, config):
    condition, params = where(config)
    # Physical duplicate source rows remain archived; one GUID contributes once
    # per conversation view. Null/empty GUIDs retain independent row identities.
    query = f'''SELECT m.*,s.version AS stats_version,s.data AS stats_data,COALESCE(CASE WHEN m.is_from_me THEN 'Me' ELSE p.address END,m.sender) AS display_sender,
        ROW_NUMBER() OVER(PARTITION BY m.source,COALESCE(NULLIF(m.guid,''),m.id) ORDER BY m.source_present DESC,m.source_rowid DESC) AS duplicate_rank
        FROM messages m JOIN memberships j ON j.message_id=m.id
        LEFT JOIN participants p ON p.source=m.source AND p.handle_id=m.handle_id
        LEFT JOIN message_statistics s ON s.message_id=m.id
        WHERE j.chat_id=? AND {condition}
        ORDER BY m.timestamp_ns,m.source_rowid'''
    for row in archive.db.execute(query, [chat_id,*params]):
        yield row


def fingerprint(archive, chat, config):
    checksum = hashlib.sha256()
    checksum.update(dumps([config.key(),chat['id'],chat['metadata']]).encode())
    for row in iter_messages(archive,chat['id'],config):
        checksum.update(dumps([row['id'],row['raw_hash'],row['decode_status'],row['decoded'],row['source_present'],row['display_sender']]).encode())
    # Reactions outside the date bounds can change the current reaction state
    # of an in-range message. Include that dependency in filtered report caches.
    for row in archive.db.execute('''SELECT m.id,m.raw_hash,m.source_present,m.associated_guid,
        r.target_guid,r.type_code,r.emoji FROM memberships j JOIN messages m ON m.id=j.message_id
        LEFT JOIN reactions r ON r.message_id=m.id
        WHERE j.chat_id=? AND m.kind='reaction' ORDER BY m.id''',(chat['id'],)):
        checksum.update(dumps(list(row)).encode())
    for row in archive.db.execute('''SELECT a.id,a.available,a.archived_path,a.sha256,a.metadata FROM attachments a
        JOIN message_attachments ma ON ma.attachment_id=a.id JOIN memberships j ON j.message_id=ma.message_id
        WHERE j.chat_id=? ORDER BY a.id''', (chat['id'],)):
        checksum.update(dumps(list(row)).encode())
    return checksum.hexdigest()


def time_key(stamp, group, zone):
    if stamp is None:
        return None
    value = datetime.fromtimestamp(stamp // 10**9, zone)
    if group=='time_of_day':
        return f'{value.hour:02d}:00'
    if group=='day_of_week':
        return str(value.weekday()) + ' ' + value.strftime('%A')
    if group=='day':
        return value.date().isoformat()
    if group=='week':
        return (value.date()-timedelta(days=value.weekday())).isoformat()
    return value.strftime('%Y-%m')


def analyze(archive, chat, config):
    zone = ZoneInfo(config.timezone)
    groups = ['time_of_day','day_of_week','day','week','month'] if config.group_by=='all' else [config.group_by]
    timelines = {group:defaultdict(Counter) for group in groups}
    frequencies, symbols, senders = Counter(), Counter(), Counter()
    result = {'message_count':0,'reaction_events':0,'announcements':0,'duplicates':0,'unsupported':0,
              'invalid_timestamps':0,'absent_records':0,'characters':0,'word_count':0,'word_characters':0,
              'edited_messages':0,'unsent_parts':0,'fully_unsent_messages':0,'thread_reply_messages':0,'response_count':0,'response_seconds_total':0.0,
              'response_seconds_max':None,'first_timestamp_ns':None,'last_timestamp_ns':None}
    previous = None
    # Only a two-person chat has unambiguous conversational turns. Multi-party
    # reports retain thread references but do not invent who was replying to whom.
    participant_count = archive.db.execute('SELECT COUNT(DISTINCT handle_id) FROM chat_participants WHERE chat_id=?',(chat['id'],)).fetchone()[0]
    from .people import chat_scope
    response_enabled = chat_scope(chat['guid'], chat['metadata'], participant_count) == 'direct'
    stop = common_words() if config.filter_common_words else frozenset()
    for row in iter_messages(archive,chat['id'],config):
        if row['duplicate_rank'] > 1:
            result['duplicates'] += 1
            continue
        if row['kind']=='reaction':
            result['reaction_events'] += 1
            continue
        if row['kind']=='announcement':
            result['announcements'] += 1
            continue
        result['message_count'] += 1
        result['unsupported'] += row['decode_status']=='unsupported'
        result['absent_records'] += not row['source_present']
        result['thread_reply_messages'] += bool(row['thread_guid'])
        decoded = json.loads(row['decoded'])
        edits = decoded.get('edits') or []
        result['edited_messages'] += any(part['status']=='Edited' for part in edits)
        result['unsent_parts'] += sum(part['status']=='Unsent' for part in edits)
        result['fully_unsent_messages'] += bool(edits) and all(part['status']=='Unsent' for part in edits)
        if row['stats_version']==TOKENIZER_VERSION:
            stats = json.loads(row['stats_data'])
        else:
            stats = statistics(row['text'])
            archive.db.execute('INSERT OR REPLACE INTO message_statistics VALUES(?,?,?)',(row['id'],TOKENIZER_VERSION,dumps(stats)))
        result['word_count'] += stats['word_count']
        result['word_characters'] += stats['word_characters']
        result['characters'] += stats['characters']
        frequencies.update({key:value for key,value in stats['tokens'].items() if key not in stop})
        symbols.update(stats['symbols'])
        sender = row['display_sender']
        senders[sender] += 1
        stamp = row['timestamp_ns']
        if stamp is None:
            result['invalid_timestamps'] += 1
            continue
        if result['first_timestamp_ns'] is None:
            result['first_timestamp_ns'] = stamp
        result['last_timestamp_ns'] = stamp
        for group in groups:
            bucket = time_key(stamp,group,zone)
            timelines[group][bucket][sender if config.separate_by_sender else 'Messages'] += 1
        direction = None if row['is_from_me'] is None else bool(row['is_from_me'])
        if previous and direction is not None and response_enabled and direction != previous[1]:
            seconds = (stamp-previous[0])/1e9
            if 0 <= seconds <= config.response_gap_hours*3600:
                result['response_count'] += 1
                result['response_seconds_total'] += seconds
                result['response_seconds_max'] = max(result['response_seconds_max'] or 0,seconds)
        previous = (stamp,direction) if direction is not None else None
    words = result['word_count']
    count = result['message_count']
    result.update({
        'average_words_per_message':words/count if count else None,
        'average_characters_per_message':result['characters']/count if count else None,
        'average_word_length':result['word_characters']/words if words else None,
        'mean_turn_response_seconds':result['response_seconds_total']/result['response_count'] if result['response_count'] else None,
        'response_metric':f'Alternating turns in two-person chats, gaps <= {config.response_gap_hours:g} hours; not read receipts.',
        'response_metric_available':response_enabled,
        'senders':dict(senders),'word_frequencies':dict(frequencies.most_common()),'symbols':dict(symbols.most_common()),
        'timelines':{group:{bucket:dict(values) for bucket,values in sorted(buckets.items())} for group,buckets in timelines.items()},
    })
    condition,params = where(config)
    result['threads'] = archive.db.execute(f'''SELECT COUNT(DISTINCT m.thread_guid) FROM messages m JOIN memberships j ON j.message_id=m.id
        WHERE j.chat_id=? AND m.kind='message' AND {condition}''',(chat['id'],*params)).fetchone()[0]
    result['attachments'] = archive.db.execute(f'''SELECT COUNT(DISTINCT a.id) FROM attachments a JOIN message_attachments ma ON ma.attachment_id=a.id
        JOIN messages m ON m.id=ma.message_id JOIN memberships j ON j.message_id=ma.message_id
        WHERE j.chat_id=? AND m.kind='message' AND {condition}''',(chat['id'],*params)).fetchone()[0]
    result['missing_attachments'] = archive.db.execute(f'''SELECT COUNT(DISTINCT a.id) FROM attachments a JOIN message_attachments ma ON ma.attachment_id=a.id
        JOIN messages m ON m.id=ma.message_id JOIN memberships j ON j.message_id=ma.message_id
        WHERE j.chat_id=? AND m.kind='message' AND a.available=0 AND a.archived_path IS NULL AND {condition}''',(chat['id'],*params)).fetchone()[0]
    from .reactions import received
    result['reactions_received'] = received(archive,chat,config,senders,
        (result['first_timestamp_ns'],result['last_timestamp_ns']))
    return result
