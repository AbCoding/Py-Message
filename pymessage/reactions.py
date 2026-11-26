"""Reconstruct current tapbacks and attribute them to the target's author."""
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
import json
import unicodedata
from zoneinfo import ZoneInfo

from .source import associated_target

STANDARD = ('heart','like','dislike','laugh','emphasis','question')


def reaction_type(code, emoji):
    if 2000 <= code <= 2005 or 3000 <= code <= 3005:
        return STANDARD[code % 1000]
    if code in (2006,3006):
        value = unicodedata.normalize('NFC',emoji or '').replace('\ufe0f','')
        return 'emoji:'+value if value else 'unknown_emoji'
    if code in (2007,3007):
        return 'sticker'
    return None


def received(archive, chat, config, senders, bounds=(None,None)):
    """Date bounds select target messages; events use the full archived history.

    Each reactor has one tapback per message part. Adds replace that slot;
    removals clear only the matching type, so late removals of an old type
    cannot erase its replacement. Legacy sticker overlays (1000) are excluded;
    sticker tapbacks (2007/3007) occupy the same slot as other tapbacks.
    """
    presence = '1' if config.include_absent else 'm.source_present=1'
    target_presence = '1' if config.include_absent else 'tm.source_present=1'
    query = f'''WITH events AS (
        SELECT m.id,m.source,m.source_rowid,m.guid,m.timestamp_ns,m.is_from_me,m.handle_id,m.associated_guid,
            r.target_guid,r.type_code,r.emoji,
            ROW_NUMBER() OVER(PARTITION BY m.source,COALESCE(NULLIF(m.guid,''),m.id)
                ORDER BY m.source_present DESC,m.source_rowid DESC) AS duplicate_rank
        FROM memberships j JOIN messages m ON m.id=j.message_id
        LEFT JOIN reactions r ON r.message_id=m.id
        WHERE j.chat_id=? AND m.kind='reaction' AND {presence})
        SELECT e.*,target.id AS target_id,target.timestamp_ns AS target_time,
            COALESCE(CASE WHEN target.is_from_me THEN 'Me' ELSE p.address END,target.sender) AS recipient
        FROM events e LEFT JOIN messages target ON target.id=(
            SELECT tm.id FROM messages tm WHERE tm.source=e.source AND tm.guid=e.target_guid
                AND tm.kind='message' AND {target_presence}
                AND EXISTS(SELECT 1 FROM memberships tj WHERE tj.chat_id=? AND tj.message_id=tm.id)
            ORDER BY tm.source_present DESC,tm.source_rowid DESC LIMIT 1)
        LEFT JOIN participants p ON p.source=target.source AND p.handle_id=target.handle_id
        WHERE e.duplicate_rank=1 ORDER BY e.timestamp_ns,e.source_rowid,e.id'''
    lower,upper = config.bounds()
    active = {}
    targets = {}
    unresolved = 0
    stickers = 0
    for event in archive.db.execute(query,(chat['id'],chat['id'])):
        if not event['target_id']:
            unresolved += 1
            continue
        stamp = event['target_time']
        if ((lower is not None and (stamp is None or stamp < lower)) or
                (upper is not None and (stamp is None or stamp >= upper))):
            continue
        code = event['type_code']
        if code == 1000:
            stickers += 1
            continue
        kind = reaction_type(code,event['emoji']) if isinstance(code,int) else None
        _,part = associated_target(event['associated_guid'])
        if event['is_from_me']:
            actor = 'me'
        elif event['handle_id'] not in (None,'','0'):
            actor = 'handle:'+event['handle_id']
        else:
            actor = None
        if not kind or part is None or actor is None or event['timestamp_ns'] is None:
            unresolved += 1
            continue
        slot = (event['source'],event['target_guid'],part,actor)
        if 3000 <= code <= 3007:
            if slot in active and active[slot][1]==kind:
                del active[slot]
        else:
            active[slot] = (event['recipient'],kind,event['target_id'])
            targets[event['target_id']] = {'id':event['target_id'],'sender':event['recipient'],'stamp':stamp}
    counts = defaultdict(Counter)
    for sender in senders:
        counts[sender]  # Include participants whose messages received no reactions.
    for sender,kind,_ in active.values():
        counts[sender][kind] += 1
    types = list(STANDARD) + sorted({kind for values in counts.values() for kind in values if kind not in STANDARD})
    source = archive.db.execute('SELECT kind FROM sources WHERE id=?',(chat['source'],)).fetchone()
    from .people import chat_scope
    participants = archive.db.execute('SELECT COUNT(DISTINCT handle_id) FROM chat_participants WHERE chat_id=?',(chat['id'],)).fetchone()[0]
    is_group = chat_scope(chat['guid'], chat['metadata'], participants) == 'groups'
    available = source is None or source[0]!='html'
    return {
        'available': available,
        'total':len(active), 'types':types,
        'by_sender':{sender:{'total':sum(values.values()),'counts':dict(values)} for sender,values in counts.items()},
        'unresolved_events':unresolved,'excluded_sticker_events':stickers,
        'definition':'Current reactions on messages sent in the selected date range. Removed or replaced reactions are excluded.',
        'hearts':heart_details(archive,config,senders,active,targets,bounds,chat['source']) if is_group and available else None,
    }


def heart_details(archive, config, senders, active, targets, bounds, source):
    """Count distinct heart reactors per whole message; fetch previews only for leaders."""
    from .activity import period_start, periods
    from .analysis import time_key
    from .reports import timestamp_iso
    reactors = defaultdict(set)
    for slot,(_,kind,target_id) in active.items():
        if kind=='heart': reactors[target_id].add((slot[0],slot[3]))
    # Reuse the analysis's full, deduplicated message counts as denominators,
    # including messages without hearts. Collapse repeated parts and handles
    # with the same address before counting each reactor -> author pair.
    addresses = {row['handle_id']:row['address'] for row in archive.db.execute(
        'SELECT handle_id,address FROM participants WHERE source=?',(source,))}
    pairs = defaultdict(Counter)
    people = set(senders)
    for target_id, actors in reactors.items():
        author = targets[target_id]['sender']
        names = {'Me' if actor=='me' else addresses.get(actor.removeprefix('handle:')) or actor
                 for _,actor in actors}
        people.update(names)
        for actor in names:
            if actor != author: pairs[actor][author] += 1
    matrix = {'people':sorted(people), 'message_counts':dict(senders),
              'counts':{actor:dict(counts) for actor,counts in pairs.items()}}
    messages = [{**targets[key],'hearts':len(actors)} for key,actors in reactors.items()]
    messages.sort(key=lambda row:(-row['hearts'],-(row['stamp'] or 0),row['id']))
    by_sender = {sender:{'hearts':0,'messages':0,'buckets':{}} for sender in senders}
    for message in messages:
        author = by_sender.setdefault(message['sender'],{'hearts':0,'messages':0,'buckets':{}})
        author['hearts'] += message['hearts']; author['messages'] += 1
        key = str(message['hearts'])
        author['buckets'][key] = author['buckets'].get(key,0)+1
    group = config.group_by if config.group_by!='all' else 'month'
    zone = ZoneInfo(config.timezone)
    dated = [row['stamp'] for row in messages if row['stamp'] is not None]
    first,last = bounds
    first = first if first is not None else min(dated,default=None)
    last = last if last is not None else max(dated,default=None)
    keys = []
    coarsened = False
    if first is not None and last is not None:
        if group=='time_of_day': keys = [f'{hour:02d}:00' for hour in range(24)]
        elif group=='day_of_week': keys = [f'{i} {name}' for i,name in enumerate(('Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'))]
        else:
            start = date.fromisoformat(config.start_date) if config.start_date else datetime.fromtimestamp(first//10**9,zone).date()
            end = date.fromisoformat(config.end_date)-timedelta(days=1) if config.end_date else datetime.fromtimestamp(last//10**9,zone).date()
            try:
                keys = periods(period_start(start,group),period_start(end,group),group)
            except ValueError:
                group,coarsened = 'month',True
                keys = periods(period_start(start,group),period_start(end,group),group)
            if group=='month': keys = [key[:7] for key in keys]
    history = defaultdict(Counter)
    for message in messages:
        if message['stamp'] is not None:
            history[message['sender']][time_key(message['stamp'],group,zone)] += message['hearts']
    top = []
    for message in messages[:50]:
        row = archive.db.execute('''SELECT text,decoded,decode_status,
            (SELECT COUNT(*) FROM message_attachments WHERE message_id=m.id) AS attachments
            FROM messages m WHERE id=?''',(message['id'],)).fetchone()
        edits = json.loads(row['decoded']).get('edits') or []
        unsent = bool(edits) and all(part.get('status')=='Unsent' for part in edits)
        text = '' if unsent else (row['text'] or '')
        top.append({'id':message['id'],'sender':message['sender'],'hearts':message['hearts'],
                    'timestamp':timestamp_iso(message['stamp']),'text':text[:1000],
                    'truncated':len(text)>1000,'unsent':unsent,'attachments':row['attachments'],
                    'unsupported':row['decode_status']=='unsupported'})
    return {'total':sum(row['hearts'] for row in messages),'message_count':len(messages),
            'by_sender':by_sender,'buckets':sorted({row['hearts'] for row in messages}),
            'matrix':matrix,
            'periods':keys,'group_by':group,'coarsened':coarsened,'timezone':config.timezone,
            'series':[{'sender':sender,'values':[history[sender][key] for key in keys]}
                      for sender in sorted(by_sender,key=lambda s:(-by_sender[s]['hearts'],s))],
            'period_totals':[sum(counts[key] for counts in history.values()) for key in keys],
            'undated_messages':sum(row['stamp'] is None for row in messages),
            'top_messages':top,'top_limit':50,
            'definition':'Standard Loved/heart tapbacks only, counted once per reacting person on each whole message. Includes your reactions. Removed or replaced hearts are excluded. Custom emoji reactions are separate. Time is the original message date, not the reaction date.'}
