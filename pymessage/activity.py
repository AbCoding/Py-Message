"""Message-count timelines across people, without decoding message bodies."""
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

VERSION = 2
MAX_PERIODS = 1500


def validate(config, metric, top, ranking_mode='overall'):
    ZoneInfo(config.timezone)
    config.bounds()
    if config.start_date and config.end_date and config.start_date>=config.end_date:
        raise ValueError('End date must be after start date (the end date is exclusive).')
    if config.group_by not in ('day','week','month'):
        raise ValueError('Choose day, week, or month for people activity.')
    if metric not in ('total','sent','received'):
        raise ValueError('Choose total, sent, or received messages.')
    if isinstance(top,bool) or not isinstance(top,int) or not 1 <= top <= 15:
        raise ValueError('Choose between 1 and 15 people.')
    if ranking_mode not in ('overall','period'):
        raise ValueError('Choose top overall or top each period.')
    if config.start_date and config.end_date:
        periods(period_start(date.fromisoformat(config.start_date),config.group_by),
                period_start(date.fromisoformat(config.end_date)-timedelta(days=1),config.group_by),config.group_by)


def period_start(value, group):
    if group=='month':
        return value.replace(day=1)
    if group=='week':
        return value-timedelta(days=value.weekday())
    return value


def periods(first, last, group):
    result = []
    while first <= last:
        if len(result)>=MAX_PERIODS:
            raise ValueError('This range contains too many periods. Choose a shorter date range or group by week/month.')
        result.append(first.isoformat())
        if group=='month':
            first = date(first.year+first.month//12,first.month%12+1,1)
        else:
            first += timedelta(days=7 if group=='week' else 1)
    return result


def compute(db, people, config, metric):
    """One SQL aggregation across direct threads, deduplicated per person/GUID.

    Both directions are counted together for summaries; only the chosen metric
    determines ranking and the plotted series. Group chats are never assigned
    wholesale to their members. No text, blobs, or decoded payloads are read.
    """
    zone = ZoneInfo(config.timezone)
    db.create_function('activity_bucket',1,lambda stamp: period_start(
        datetime.fromtimestamp(stamp//10**9,zone).date(),config.group_by).isoformat() if stamp is not None else None)
    db.execute('CREATE TEMP TABLE activity_threads(chat TEXT PRIMARY KEY,person TEXT)')
    db.executemany('INSERT INTO activity_threads VALUES(?,?)',
        ((thread['id'],person['id']) for person in people.people.values() for thread in person['threads'] if thread['scope']=='direct'))
    db.execute('CREATE TEMP TABLE activity_handles(source TEXT,handle TEXT,person TEXT,PRIMARY KEY(source,handle))')
    db.executemany('INSERT INTO activity_handles VALUES(?,?,?)',
        ((source,handle,person_id) for (source,handle),person_id in people.by_handle.items()))
    lower,upper = config.bounds()
    clauses,params = [],[]
    for op,bound in (('>=',lower),('<',upper)):
        if bound is not None:
            clauses.append(f'(timestamp_ns IS NULL OR timestamp_ns {op} ?)')
            params.append(bound)
    where = ' AND '.join(clauses) or '1'
    query = f'''WITH candidates AS (
        SELECT t.person,m.id,m.timestamp_ns,m.is_from_me,
            ROW_NUMBER() OVER(PARTITION BY t.person,COALESCE(NULLIF(m.guid,''),m.id)
                ORDER BY m.timestamp_ns DESC,m.source_rowid DESC,m.id DESC) AS rank
        FROM activity_threads t CROSS JOIN memberships j ON j.chat_id=t.chat
        JOIN messages m ON m.id=j.message_id
        LEFT JOIN activity_handles h ON h.source=m.source AND h.handle=m.handle_id
        WHERE m.source_present=1 AND m.kind='message' AND (m.is_from_me=1 OR h.person=t.person))
        SELECT person,activity_bucket(timestamp_ns) AS bucket,is_from_me,COUNT(*) AS count
        FROM candidates WHERE rank=1 AND {where} GROUP BY person,bucket,is_from_me'''
    totals = defaultdict(Counter)
    timelines = defaultdict(Counter)
    period_totals = Counter()
    invalid_dates = unknown_direction = 0
    for row in db.execute(query,params):
        count = row['count']
        if row['bucket'] is None:
            invalid_dates += count
            continue
        if row['is_from_me'] not in (0,1):
            unknown_direction += count
            continue
        direction = 'sent' if row['is_from_me'] else 'received'
        totals[row['person']][direction] += count
        if metric in ('total',direction):
            timelines[row['person']][row['bucket']] += count
            period_totals[row['bucket']] += count
    ranking = []
    for person_id,counts in totals.items():
        person = people.get(person_id)
        total = counts['sent']+counts['received']
        selected = total if metric=='total' else counts[metric]
        if selected:
            ranking.append({'id':person_id,'name':person['name'],'addresses':person['addresses'],
                            'count':selected,'sent':counts['sent'],'received':counts['received'],'total':total})
    ranking.sort(key=lambda row:(-row['count'],row['name'].casefold(),row['id']))
    buckets = []
    if period_totals:
        first = period_start(date.fromisoformat(config.start_date),config.group_by) if config.start_date else date.fromisoformat(min(period_totals))
        last = period_start(date.fromisoformat(config.end_date)-timedelta(days=1),config.group_by) if config.end_date else date.fromisoformat(max(period_totals))
        buckets = periods(first,last,config.group_by)
    # Retain each bucket's leaders, including people outside the overall top 15.
    # Sparse rows keep the cache bounded by 15 entries per period.
    by_period = defaultdict(list)
    names = {row['id']:row['name'].casefold() for row in ranking}
    for person_id,counts in timelines.items():
        for bucket,count in counts.items():
            by_period[bucket].append({'id':person_id,'count':count})
    leaders = [sorted(by_period[bucket],key=lambda row:(-row['count'],names[row['id']],row['id']))[:15]
               for bucket in buckets]
    return {
        'metric':metric,'group_by':config.group_by,'timezone':config.timezone,
        'start_date':config.start_date,'end_date':config.end_date,
        'periods':buckets,'period_totals':[period_totals[bucket] for bucket in buckets],
        'ranking':ranking,
        'period_leaders':leaders,
        'series':[{**row,'values':[timelines[row['id']][bucket] for bucket in buckets]} for row in ranking[:15]],
        'total':sum(row['count'] for row in ranking),'people_count':len(ranking),
        'invalid_dates':invalid_dates,'unknown_direction':unknown_direction,
        'definition':'Direct conversations only. Linked addresses are combined; duplicate messages, reactions, and group chats are excluded.',
    }


def select(result, top, ranking_mode):
    """Project cached counts without another scan of the message archive."""
    if ranking_mode == 'overall':
        return {**result,'series':result['series'][:top],'period_leaders':[],
                'top':top,'ranking_mode':ranking_mode}
    leaders = [rows[:top] for rows in result['period_leaders']]
    metadata = {row['id']:row for row in result['ranking']}
    series = {}
    for index,rows in enumerate(leaders):
        for row in rows:
            person = series.setdefault(row['id'],{**metadata[row['id']],'points':[]})
            person['points'].append({'index':index,'count':row['count']})
    return {**result,'series':list(series.values()),'period_leaders':leaders,
            'top':top,'ranking_mode':ranking_mode}
