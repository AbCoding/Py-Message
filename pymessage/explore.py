"""Scoped archive trends for Overview, Direct texts and Group chats."""
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import date, datetime, timedelta
import json
from statistics import median
from zoneinfo import ZoneInfo

from .activity import period_start, periods, validate as validate_periods
from .emoji import context_term, context_tokens
from .reports import timestamp_iso
from .semantic import LINK, STOP


PAGES = {'overview', 'direct', 'groups'}
LABELS = {'direct': 'Direct texts', 'groups': 'Group chats', 'unknown': 'Unclassified chats',
          'me': 'You', 'received': 'Received', 'sent': 'Sent', 'all': 'All messages'}
VERSION = 2


def options(payload, config):
    page = payload.get('page', 'overview')
    measure = payload.get('measure', 'activity')
    target = payload.get('target', '')
    direction = payload.get('direction', 'sent' if measure == 'language' else 'all')
    gap = payload.get('gap', 24)
    author = payload.get('author', '')
    language_stat = payload.get('language_stat', 'length')
    term = payload.get('term', '')
    if language_stat not in ('length', 'emoji', 'term'):
        raise ValueError('Choose a language measure.')
    if measure == 'language' and language_stat == 'term':
        term = context_term(term)
    else:
        term = ''
    validate_periods(config, 'total', 1)
    if page not in PAGES or measure not in ('activity', 'language', 'reply'):
        raise ValueError('Choose an available page and measure.')
    if not isinstance(target, str) or not isinstance(author, str) or (target and page == 'overview'):
        raise ValueError('Choose a valid conversation or person.')
    if measure == 'reply' and (page != 'direct' or not target):
        raise ValueError('Choose a direct correspondent for reply timing.')
    if direction not in ('all', 'sent', 'received') or gap not in (1, 6, 12, 24, 48):
        raise ValueError('Choose a supported direction and reply gap.')
    if author and (page != 'groups' or not target):
        raise ValueError('Choose a group before filtering by member.')
    return dict(page=page, measure=measure, target=target, direction=direction,
                gap=gap, author=author, language_stat=language_stat, term=term, group_by=config.group_by, timezone=config.timezone,
                start_date=config.start_date or '', end_date=config.end_date or '')


def prepare(db, people, query):
    recipients = {thread['id']: person['id'] for person in people.people.values()
                  for thread in person['threads'] if thread['scope'] == 'direct'}
    if query['page'] == 'direct' and query['target']:
        people.get(query['target'])
    if query['page'] == 'groups' and query['target']:
        if people.threads.get(query['target'], {}).get('scope') != 'groups':
            raise ValueError('This group is no longer in the archive.')
    db.execute('CREATE TEMP TABLE explore_chats(chat TEXT PRIMARY KEY, scope TEXT, recipient TEXT)')
    db.executemany('INSERT INTO explore_chats VALUES(?,?,?)',
                   ((key, t['scope'], recipients.get(key)) for key, t in people.threads.items()))
    db.execute('CREATE TEMP TABLE explore_handles(source TEXT, handle TEXT, person TEXT, PRIMARY KEY(source,handle))')
    db.executemany('INSERT INTO explore_handles VALUES(?,?,?)',
                   ((source, handle, person) for (source, handle), person in people.by_handle.items()))


def rows(db, query, config, detail=False):
    # Pick one destination before filtering. This keeps Overview and its drill-downs
    # additive, including when a source row has an extra recovered membership.
    filters = ['m.timestamp_ns IS NOT NULL']
    params = []
    if query['page'] != 'overview':
        filters.append('scope=?')
        params.append(query['page'])
    if query['target']:
        filters.append(('recipient' if query['page'] == 'direct' else 'chat') + '=?')
        params.append(query['target'])
    for operator, bound in zip(('>=', '<'), config.bounds()):
        if bound is not None:
            filters.append('m.timestamp_ns ' + operator + ' ?')
            params.append(bound)
    if query['direction'] != 'all' and query['measure'] != 'reply':
        filters.append('m.is_from_me=?')
        params.append(int(query['direction'] == 'sent'))
    if query['author']:
        filters.append("CASE WHEN m.is_from_me=1 THEN 'me' ELSE COALESCE(h.person,'unknown-author') END=?")
        params.append(query['author'])
    extra = ',m.text,m.decoded,m.decode_status' if detail or query['measure'] == 'language' else ''
    order = ' ORDER BY m.timestamp_ns,m.source_rowid,m.id' if query['measure'] == 'reply' or detail else ''
    return db.execute(f"""WITH ranked AS (
        SELECT m.id,j.chat_id AS chat,COALESCE(t.scope,'unknown') AS scope,t.recipient,
        ROW_NUMBER() OVER (PARTITION BY COALESCE(NULLIF(m.guid,''),m.id)
          ORDER BY j.recovered ASC,m.timestamp_ns DESC,m.source_rowid DESC,m.id,j.chat_id) AS rn
        FROM memberships j JOIN messages m ON m.id=j.message_id
        LEFT JOIN explore_chats t ON t.chat=j.chat_id
        WHERE m.source_present=1 AND m.kind='message')
        SELECT chat,scope,recipient,m.id,m.timestamp_ns,m.is_from_me,
          CASE WHEN m.is_from_me=1 THEN 'me' ELSE COALESCE(h.person,'unknown-author') END AS author
          {extra}
        FROM ranked JOIN messages m ON m.id=ranked.id
        LEFT JOIN explore_handles h ON h.source=m.source AND h.handle=m.handle_id
        WHERE rn=1 AND {' AND '.join(filters)}{order}""", params)


def series_key(row, query):
    if query['page'] == 'overview':
        return row['scope']
    if query['target']:
        return row['author']
    return row['recipient'] if query['page'] == 'direct' else row['chat']


def label(key, people):
    if key in people.people:
        return people.get(key)['name']
    if key in people.threads:
        return people.threads[key]['name']
    return LABELS.get(key, 'Unknown author')


def text_value(row):
    if row['decode_status'] == 'unsupported':
        return ''
    edits = json.loads(row['decoded'] or '{}').get('edits') or []
    if edits and all(edit.get('status') == 'Unsent' for edit in edits):
        return ''
    return (row['text'] or '').strip()


def compute(db, people, query, config, inspect_period=None, offset=0):
    prepare(db, people, query)
    zone = ZoneInfo(config.timezone)
    measure = query['measure']
    counts, numerators, denominators = defaultdict(Counter), defaultdict(Counter), defaultdict(Counter)
    vocab, emoji = Counter(), Counter()
    gaps = defaultdict(lambda: defaultdict(list))
    previous = {}
    items = []
    matches = 0
    scan_config = config
    if inspect_period is not None and measure != 'reply':
        start = date.fromisoformat(inspect_period)
        if query['group_by'] == 'month':
            end = date(start.year + start.month // 12, start.month % 12 + 1, 1)
        else:
            end = start + timedelta(days=7 if query['group_by'] == 'week' else 1)
        scan_config = replace(config, start_date=max(config.start_date or inspect_period, inspect_period),
                              end_date=min(config.end_date or end.isoformat(), end.isoformat()))
    for row in rows(db, query, scan_config, detail=inspect_period is not None):
        stamp = row['timestamp_ns']
        period = period_start(datetime.fromtimestamp(stamp // 10**9, zone).date(), config.group_by).isoformat()
        key = series_key(row, query)
        if inspect_period is not None and period != inspect_period and measure != 'reply':
            continue
        if measure == 'reply':
            last = previous.get(row['chat'])
            previous[row['chat']] = (stamp, row['is_from_me'])
            if (not last or last[1] is None or row['is_from_me'] is None or last[1] == row['is_from_me']
                    or (stamp - last[0]) > query['gap'] * 3600 * 10**9):
                continue
            if inspect_period is not None and period != inspect_period:
                continue
            if inspect_period is None:
                gaps[key][period].append((stamp - last[0]) / 1e9)
        if measure == 'language':
            body = text_value(row)
            if not body:
                continue
            stat = query['language_stat']
            if inspect_period is not None and stat != 'length':
                tokens = context_tokens(LINK.sub(' ', body))
                if stat == 'term' and query['term'] not in tokens:
                    continue
                if stat == 'emoji' and not any(not any(c.isalpha() for c in token) for token in tokens):
                    continue
            if inspect_period is None:
                if stat == 'length':
                    denominators[key][period] += 1
                for token in context_tokens(LINK.sub(' ', body)):
                    is_word = any(char.isalpha() for char in token)
                    if is_word and token not in STOP:
                        vocab[token] += 1
                    elif not is_word:
                        emoji[token] += 1
                    if stat != 'length':
                        denominators[key][period] += 1
                    if ((stat == 'length' and is_word) or (stat == 'emoji' and not is_word)
                            or (stat == 'term' and token == query['term'])):
                        numerators[key][period] += 1
        if inspect_period is not None:
            matches += 1
            if matches > offset:
                items.append(dict(id=row['id'], chat=row['chat'], name=label(row['chat'], people),
                                  sender=label(row['author'], people), time=timestamp_iso(stamp),
                                  text=text_value(row)[:1500]))
            if len(items) == 51:
                break
        else:
            counts[key][period] += 1
    if inspect_period is not None:
        return {'messages': items[:50], 'has_more': len(items) > 50}

    observed = {p for timeline in counts.values() for p in timeline}
    buckets = []
    if observed:
        first = date.fromisoformat(config.start_date or min(observed))
        last = date.fromisoformat(config.end_date) - timedelta(days=1) if config.end_date else date.fromisoformat(max(observed))
        buckets = periods(period_start(first, config.group_by), period_start(last, config.group_by), config.group_by)

    names = {key: label(key, people) for key in counts}
    repeated = Counter(names.values())
    for key, name in names.items():
        if repeated[name] < 2:
            continue
        if key in people.people:
            addresses = people.get(key)['addresses']
            names[key] = name + ' · ' + (addresses[0] if addresses else key[-8:])
        elif key in people.threads:
            names[key] = name + ' · ' + (', '.join(people.threads[key]['addresses']) or key[-8:])
    ranking = sorted(counts, key=lambda k: (-sum(counts[k].values()), names[k].casefold(), k))
    def language_value(numerator, denominator):
        scale = 1 if query['language_stat'] == 'length' else 1000
        return round(scale * numerator / denominator, 2) if denominator else None

    def value(key, period):
        count = counts[key][period]
        if measure == 'reply':
            return median(gaps[key][period]) if count else None
        if measure == 'language':
            return language_value(numerators[key][period], denominators[key][period])
        return count
    stats = []
    for key in ranking:
        count = sum(counts[key].values())
        overall = count
        if measure == 'reply':
            overall = median(g for values in gaps[key].values() for g in values)
        elif measure == 'language':
            overall = language_value(sum(numerators[key].values()), sum(denominators[key].values()))
        stats.append(dict(id=key, name=names[key], count=count, value=overall))
    return dict(query=query, periods=buckets, total=sum(s['count'] for s in stats),
                ranking=stats, series=[dict(id=key, name=names[key],
                values=[value(key, p) for p in buckets]) for key in ranking[:10]],
                words=vocab.most_common(24), emoji=emoji.most_common(24))
