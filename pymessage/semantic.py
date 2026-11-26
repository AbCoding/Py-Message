"""Local vocabulary, context, tone cues, and literal name matches in sent text.

No model downloads or remote services. Context vectors describe nearby words;
they do not establish a word's meaning or a sender's emotional state.
"""
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
import json
import math
import re
from zoneinfo import ZoneInfo

from .activity import MAX_PERIODS, period_start, periods, validate as validate_periods
from .text import tokens
from .emoji import context_tokens, context_term

VERSION = 1
WINDOW = 100
STOP = frozenset("a an the i me my mine we us our you u ur your yours he him his she her hers they them their it its this that these those am is are was were be been being do does did have has had and or but if then so as at by for from in into of on to with about just really very also too not no yes yeah yep im i'm it's that's don't dont can't cant can could would should will shall may might must what which who when where why how all any some more most much many up out there here now than like".split())
LINK = re.compile(r'https?://\S+|www\.\S+|\b[^\s@]+@[^\s@]+\.[^\s@]+', re.I)
TONE = {
    'positive wording': frozenset('love loved happy glad great awesome amazing wonderful excited lovely yay nice excellent grateful'.split()),
    'negative wording': frozenset('hate hated sad angry upset awful terrible horrible miserable annoyed frustrating disappointed worried'.split()),
    'laughter': frozenset('lol lmao lmfao rofl haha hahaha hehe hehehe'.split()),
    'politeness': frozenset('please thanks thank sorry appreciate appreciated'.split()),
    'uncertainty': frozenset('maybe perhaps probably possibly unsure uncertain'.split()),
    'profanity': frozenset('fuck fucking fucked shit shitty bullshit damn asshole bitch'.split()),
}
NEGATORS = frozenset("not no never don't dont didn't didnt isn't isnt wasn't wasnt can't cant".split())


def words(text):
    return [word for word in tokens(LINK.sub(' ',text or '')) if any(char.isalpha() for char in word)]


def validate(config, person_ids, focus, names):
    validate_periods(config,'sent',1)
    if not isinstance(person_ids,list) or len(person_ids)>6 or any(not isinstance(p,str) for p in person_ids):
        raise ValueError('Select up to six people, or leave the selection empty for everyone.')
    if not isinstance(focus,str) or len(focus)>80 or (focus.strip() and len(words(focus))!=1):
        raise ValueError('Choose one word for context analysis.')
    if not isinstance(names,list) or len(names)>30 or any(not isinstance(n,str) or len(n)>80 or not words(n) for n in names):
        raise ValueError('Enter up to 30 names, each at most 80 characters.')


def rows(db, people, config, person_ids):
    selected = set(person_ids)
    for person_id in selected:
        people.get(person_id)
    db.execute('DROP TABLE IF EXISTS temp.semantic_threads')
    db.execute('CREATE TEMP TABLE semantic_threads(chat TEXT PRIMARY KEY,person TEXT)')
    db.executemany('INSERT INTO semantic_threads VALUES(?,?)',
        ((thread['id'],p['id']) for p in people.people.values() if not selected or p['id'] in selected
         for thread in p['threads'] if thread['scope']=='direct'))
    lower,upper = config.bounds()
    filters,params = [],[]
    for op,bound in (('>=',lower),('<',upper)):
        if bound is not None:
            filters.append(f'(timestamp_ns IS NULL OR timestamp_ns {op} ?)')
            params.append(bound)
    # Collapse duplicate memberships and GUIDs across sources before filtering.
    # A GUID assigned to different direct recipients is excluded as ambiguous.
    query = '''WITH candidates AS (
        SELECT DISTINCT m.id,m.guid,m.source_rowid,m.timestamp_ns,m.text,m.decoded,m.decode_status,t.person
        FROM semantic_threads t CROSS JOIN memberships j ON j.chat_id=t.chat
        JOIN messages m ON m.id=j.message_id
        WHERE m.source_present=1 AND m.kind='message' AND m.is_from_me=1),
        ranked AS (SELECT *,
          ROW_NUMBER() OVER(PARTITION BY COALESCE(NULLIF(guid,''),id) ORDER BY timestamp_ns DESC,source_rowid DESC,id DESC) AS rank,
          MIN(person) OVER(PARTITION BY COALESCE(NULLIF(guid,''),id)) AS first_person,
          MAX(person) OVER(PARTITION BY COALESCE(NULLIF(guid,''),id)) AS last_person
          FROM candidates)
        SELECT * FROM ranked WHERE rank=1 AND ''' + (' AND '.join(filters) or '1') + ' ORDER BY timestamp_ns,id'
    return db.execute(query,params)


def usable(row, coverage):
    if row['first_person']!=row['last_person']:
        coverage['ambiguous_recipient'] += 1
        return False
    if row['timestamp_ns'] is None:
        coverage['invalid_dates'] += 1
        return False
    edits = json.loads(row['decoded'] or '{}').get('edits') or []
    if edits and all(part.get('status')=='Unsent' for part in edits):
        coverage['unsent'] += 1
        return False
    if row['decode_status']=='unsupported':
        coverage['unsupported'] += 1
        return False
    return True


def tone_cues(sequence, text):
    found = set()
    for index,word in enumerate(sequence):
        for label,lexicon in TONE.items():
            if word in lexicon and not (label in ('positive wording','negative wording') and NEGATORS.intersection(sequence[max(0,index-3):index])):
                found.add(label)
    if any(char in text for char in '😂🤣'): found.add('laughter')
    if '?' in text or '？' in text: found.add('questions')
    return found


class NameMatcher:
    def __init__(self, contacts, extra):
        self.patterns = defaultdict(dict)
        self.metadata = {}
        grouped = defaultdict(set)
        for address,name in contacts.labels.items():
            grouped[name].add(contacts.identities.get(address,address))
        for name,identities in grouped.items():
            sequence = words(name)
            if not sequence: continue
            self.add(sequence,name,identities,False)
            if len(sequence)>1 and len(sequence[0])>=3 and sequence[0] not in STOP:
                self.add(sequence[:1],name,identities,True)
        for name in extra:
            self.add(words(name),name,set(),False,custom=True)

    def add(self, sequence, name, identities, partial, custom=False):
        key = ' '.join(sequence)
        entry = self.metadata.setdefault(key,{'name':key,'contacts':set(),'identities':set(),'first_name_only':False,'custom':False})
        entry['contacts'].add(name)
        entry['identities'].update(identities)
        entry['first_name_only'] |= partial
        entry['custom'] |= custom
        self.patterns[sequence[0]][tuple(sequence)] = key

    def matches(self, sequence):
        found = set()
        index = 0
        while index < len(sequence):
            match = None
            for pattern,key in self.patterns.get(sequence[index],{}).items():
                if tuple(sequence[index:index+len(pattern)])==pattern and (match is None or len(pattern)>match[0]):
                    match = (len(pattern),key)
            if match:
                found.add(match[1]); index += match[0]
            else: index += 1
        return found

    def public(self, key):
        entry = self.metadata[key]
        return {k:v for k,v in entry.items() if k not in ('contacts','identities')} | {
            'contacts':sorted(entry['contacts']), 'ambiguous':len(entry['identities'])>1}


class Aggregate:
    def __init__(self):
        self.messages = 0
        self.words = Counter()
        self.tones = Counter()
        self.names = Counter()
        self.window = []
        self.window_sum = self.windows = 0

    def add(self, sequence, tones, names):
        self.messages += 1
        self.words.update(sequence)
        self.tones.update(tones)
        self.names.update(names)
        for word in sequence:
            self.window.append(word)
            if len(self.window)==WINDOW:
                self.window_sum += len(set(self.window))
                self.windows += 1
                self.window.clear()

    def summary(self):
        count = self.words.total()
        return {'messages':self.messages,'words':count,'unique_words':len(self.words),
                'words_per_message':round(count/self.messages,2) if self.messages else None,
                'diversity':round(self.window_sum/self.windows,2) if self.windows else None,
                'diversity_windows':self.windows,
                'tone':{label:{'count':self.tones[label],'rate':round(100*self.tones[label]/self.messages,2) if self.messages else None}
                        for label in (*TONE,'questions')}}


def frequencies(counts, total, limit=20):
    return [{'word':word,'count':count,'rate':round(1000*count/total,3)}
            for word,count in sorted(counts.items(),key=lambda item:(-item[1],item[0])) if word not in STOP][:limit] if total else []


def distinctive_words(others, selected):
    other_total, selected_total = others.total(), selected.total()
    if min(other_total, selected_total) < 20:
        return []
    rows = []
    for word, count in selected.items():
        if word in STOP or count + others[word] < 3:
            continue
        other_rate = 1000 * others[word] / other_total
        selected_rate = 1000 * count / selected_total
        delta = round(selected_rate - other_rate, 3)
        if delta > 0:
            rows.append({'word':word, 'before':others[word], 'after':count,
                         'before_rate':round(other_rate,3), 'after_rate':round(selected_rate,3), 'delta':delta})
    return sorted(rows, key=lambda row: (-row['delta'], row['word']))[:10]


def compute(db, people, contacts, config, person_ids, extra_names, progress=lambda _:None):
    overall = Aggregate()
    by_period,by_person,person_period = defaultdict(Aggregate),defaultdict(Aggregate),defaultdict(Aggregate)
    matcher = NameMatcher(contacts,extra_names)
    coverage = Counter()
    zone = ZoneInfo(config.timezone)
    first_bucket = None
    for index,row in enumerate(rows(db,people,config,person_ids)):
        if index%5000==0: progress(f'Reading sent messages: {index:,} checked')
        if not usable(row,coverage): continue
        sequence = words(row['text'])
        if not sequence:
            coverage['no_words'] += 1
            continue
        bucket_date = period_start(datetime.fromtimestamp(row['timestamp_ns']//10**9,zone).date(),config.group_by)
        first_bucket = first_bucket or (period_start(date.fromisoformat(config.start_date),config.group_by) if config.start_date else bucket_date)
        span = ((bucket_date.year-first_bucket.year)*12+bucket_date.month-first_bucket.month+1 if config.group_by=='month'
                else (bucket_date-first_bucket).days//(7 if config.group_by=='week' else 1)+1)
        if span>MAX_PERIODS:
            raise ValueError('This range contains too many periods. Choose a shorter range or group by week/month.')
        bucket = bucket_date.isoformat()
        tones,names = tone_cues(sequence,row['text']),matcher.matches(sequence)
        for aggregate in (overall,by_period[bucket],by_person[row['person']]):
            aggregate.add(sequence,tones,names)
        if person_ids: person_period[(row['person'],bucket)].add(sequence,tones,names)
    progress('Comparing vocabulary, tone cues, and name matches')
    buckets = []
    if by_period:
        first = period_start(date.fromisoformat(config.start_date),config.group_by) if config.start_date else date.fromisoformat(min(by_period))
        last = period_start(date.fromisoformat(config.end_date)-timedelta(days=1),config.group_by) if config.end_date else date.fromisoformat(max(by_period))
        buckets = periods(first,last,config.group_by)
    name_keys = list(dict.fromkeys([*(key for key,entry in matcher.metadata.items() if entry['custom']),
                                   *(key for key,_ in overall.names.most_common(30))]))
    names = [dict(matcher.public(key),count=overall.names[key],
                  rate=round(100*overall.names[key]/overall.messages,2) if overall.messages else None,
                  values=[by_period[b].names[key] for b in buckets]) for key in name_keys]
    persons = []
    for person_id,aggregate in sorted(by_person.items(),key=lambda item:(-item[1].words.total(),item[0]))[:30]:
        person = people.get(person_id)
        rest = overall.words-aggregate.words
        distinctive = distinctive_words(rest,aggregate.words)
        persons.append({'id':person_id,'name':person['name'],'addresses':person['addresses'],**aggregate.summary(),
                        'top_words':frequencies(aggregate.words,aggregate.words.total(),10),'distinctive':distinctive,
                        'names':[dict(matcher.public(key),count=count) for key,count in aggregate.names.most_common(10)],
                        'timeline':[{'period':b,**person_period[(person_id,b)].summary()} for b in buckets] if person_ids else []})
    return {'group_by':config.group_by,'timezone':config.timezone,'start_date':config.start_date,'end_date':config.end_date,
            'person_ids':person_ids,'extra_names':extra_names,'summary':overall.summary(),'coverage':dict(coverage),
            'timeline':[{'period':b,**by_period[b].summary()} for b in buckets],
            'top_words':frequencies(overall.words,overall.words.total(),40),'people':persons,
            'people_count':len(by_person),'names':names,
            'tone_lexicon':{label:sorted(lexicon) for label,lexicon in TONE.items()}}


def validate_context(config, person_ids, selected_words):
    validate(config,person_ids,'',[])
    if not isinstance(selected_words,list) or not 1<=len(selected_words)<=6:
        raise ValueError('Choose one to six words or emoji for context analysis.')
    return list(dict.fromkeys(context_term(word) for word in selected_words))


def context(db, people, config, person_ids, focus, buckets, progress=lambda _:None):
    # Vocabulary periods can omit emoji-only messages, so derive context's own.
    return contexts(db,people,config,person_ids,[focus],progress=progress)[0]


def contexts(db, people, config, person_ids, selected_words, buckets=None, progress=lambda _:None):
    """Scan once for all words; ±5 tokens, never across message boundaries."""
    neighbors,occurrences = defaultdict(lambda:defaultdict(Counter)),defaultdict(Counter)
    person_neighbors,person_hits = defaultdict(lambda:defaultdict(Counter)),defaultdict(Counter)
    person_period_hits = defaultdict(Counter)
    totals,person_totals,person_period_totals = Counter(),Counter(),Counter()
    targets = set(selected_words)
    zone = ZoneInfo(config.timezone)
    coverage = Counter()
    for index,row in enumerate(rows(db,people,config,person_ids)):
        if index%5000==0: progress(f'Reading word context: {index:,} checked')
        if not usable(row,coverage): continue
        sequence = list(context_tokens(LINK.sub(' ',row['text'] or '')))
        if not sequence:
            coverage['no_words_or_emoji'] += 1
            continue
        bucket = period_start(datetime.fromtimestamp(row['timestamp_ns']//10**9,zone).date(),config.group_by).isoformat()
        if bucket not in totals and buckets is None:
            first = period_start(date.fromisoformat(config.start_date),config.group_by) if config.start_date else date.fromisoformat(min(totals,default=bucket))
            # Bound the scan before retaining an excessively wide day-level range.
            periods(first,date.fromisoformat(bucket),config.group_by)
        totals[bucket] += len(sequence)
        person_totals[row['person']] += len(sequence)
        if person_ids: person_period_totals[(row['person'],bucket)] += len(sequence)
        for i,word in enumerate(sequence):
            if word not in targets: continue
            occurrences[word][bucket] += 1
            person_hits[word][row['person']] += 1
            if person_ids: person_period_hits[word][(row['person'],bucket)] += 1
            window = [w for w in sequence[max(0,i-5):i]+sequence[i+1:i+6] if w not in STOP and w!=word]
            neighbors[word][bucket].update(window)
            person_neighbors[word][row['person']].update(window)
    if buckets is None:
        buckets = []
        if totals:
            first = period_start(date.fromisoformat(config.start_date),config.group_by) if config.start_date else date.fromisoformat(min(totals))
            last = period_start(date.fromisoformat(config.end_date)-timedelta(days=1),config.group_by) if config.end_date else date.fromisoformat(max(totals))
            buckets = periods(first,last,config.group_by)
    result = []
    for focus in selected_words:
        timeline = []
        for i,bucket in enumerate(buckets):
            previous = buckets[i-1] if i else None
            a,b = neighbors[focus][previous],neighbors[focus][bucket]
            norm = math.sqrt(sum(v*v for v in a.values())*sum(v*v for v in b.values()))
            similarity = sum(v*b[w] for w,v in a.items())/norm if norm and occurrences[focus][previous]>=5 and occurrences[focus][bucket]>=5 else None
            timeline.append({'period':bucket,'count':occurrences[focus][bucket],
                             'rate':round(1000*occurrences[focus][bucket]/totals[bucket],3) if totals[bucket] else None,
                             'context_change':round(1-min(1,similarity),3) if similarity is not None else None,
                             'neighbors':frequencies(b,b.total(),12)})
        recipients = person_ids or [p for p,_ in person_hits[focus].most_common(30)]
        result.append({'word':focus,'rate_unit':'tokens','timeline':timeline,'coverage':dict(coverage),'people':[{'id':p,'name':people.get(p)['name'],
                'addresses':people.get(p)['addresses'],'count':person_hits[focus][p],
                'rate':round(1000*person_hits[focus][p]/person_totals[p],3) if person_totals[p] else None,
                'neighbors':frequencies(person_neighbors[focus][p],person_neighbors[focus][p].total(),12),
                'timeline':[{'period':b,'count':person_period_hits[focus][(p,b)],
                             'rate':round(1000*person_period_hits[focus][(p,b)]/person_period_totals[(p,b)],3) if person_period_totals[(p,b)] else None}
                            for b in buckets] if person_ids else []} for p in recipients]})
    return result
