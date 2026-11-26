"""Loopback-only conversation browser, backed by the canonical archive."""
from contextlib import contextmanager, nullcontext
from copy import copy
from dataclasses import asdict
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import secrets
import sqlite3
import threading
from urllib.parse import parse_qs, urlsplit
import webbrowser
from zoneinfo import ZoneInfo

from .analysis import Config, analyze, fingerprint
from .archive import Archive
from .contacts import ContactBook, read_macos_contacts
from .people import People, chat_scope, load_links, save_links
from .reports import render_charts, timestamp_iso
from .util import atomic_write, digest, dumps, exclusive_lock

WEB = Path(__file__).with_name('web')


class App:
    def __init__(self, args):
        self.args = copy(args)
        self.output = args.output.expanduser().resolve()
        self.path = (args.archive or self.output/'.state/archive.sqlite').expanduser().resolve()
        source = (args.db_path or Path.home()/'Library/Messages/chat.db').expanduser().absolute()
        requested_archive = (args.archive or self.output/'.state/archive.sqlite').expanduser().absolute()
        # Source inode/access checks belong to explicit sync, never app startup.
        if requested_archive == source or self.path == source:
            raise ValueError('The archive must be different from the Messages source database.')
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        self.job = {'state': 'idle'}
        self.artifacts = {}
        self.leaderboard_messages = set()
        self.origin = None
        self.worker = None
        self.contacts_path = self.path.parent/'contact-labels.json'
        self.contacts = ContactBook.load(self.contacts_path)
        self.people_path = self.path.parent/'people-links.json'
        self.library_path = self.path.parent/'app-library.json'
        self.library_lock = threading.Lock()
        self._library = None

    @contextmanager
    def read(self):
        if not self.path.is_file():
            raise ValueError('Load your conversations first.')
        db = sqlite3.connect(self.path.as_uri()+'?mode=ro', uri=True, timeout=10)
        db.row_factory = sqlite3.Row
        db.create_function('contact_matches',2,self.contacts.matches)
        try:
            yield db
        finally:
            db.close()

    def status(self):
        with self.lock:
            job = dict(self.job)
        return {'job': job, 'archive_exists': self.path.is_file(),
                'demo': self.args.demo, 'timezone': self.args.timezone,
                'contacts': {'count':self.contacts.count,'updated':self.contacts.updated,'identity_ready':self.contacts.identity_ready}}

    def people(self, search='', offset=0, person_id=None, scope=None, direct_only=False):
        if not self.path.exists():
            if person_id:
                raise ValueError('Load your conversations first.')
            return {'people':[],'has_more':False,'identity_ready':self.contacts.identity_ready}
        with self.read() as db:
            index = People(db,self.contacts,load_links(self.people_path))
            if person_id:
                return index.messages(db,person_id,scope,offset) if scope else index.public(person_id,detail=True)
            return index.listing(search,offset,direct_only)

    def group_members(self, chat_id):
        with self.read() as db:
            people = People(db, self.contacts, load_links(self.people_path))
            if people.threads.get(chat_id, {}).get('scope') != 'groups':
                raise ValueError('Select a group chat.')
            return sorted([{'id': p['id'], 'name': p['name'], 'addresses': p['addresses']}
                           for p in people.people.values() if any(t['id'] == chat_id for t in p['threads'])],
                          key=lambda p: (p['name'].casefold(), p['id']))

    def link_people(self, payload, unlink=False):
        with self.lock, self.read() as db:
            if self.job['state']=='running':
                raise RuntimeError('Wait for the active operation to finish before changing people links.')
            index = People(db,self.contacts,load_links(self.people_path))
            selected = index.get(payload.get('person_id'))
            anchor = min(selected['handles'])
            links = index.unlinked(selected['id']) if unlink else index.merged_links(selected['id'],payload.get('other_id'))
            save_links(self.people_path,links)
            updated = People(db,self.contacts,links)
            return updated.public(updated.by_handle[anchor],detail=True)

    def library(self, refresh=False):
        """Saved sidebar index. Source changes are checked only by explicit sync.

        Existing archives get one local bootstrap if no index has been saved.
        Reopens/searches then use JSON, without archive scans or freshness checks.
        """
        with self.library_lock:
            if not refresh and self._library is not None:
                return self._library
            if not refresh:
                try:
                    saved = json.loads(self.library_path.read_text())
                    fields = {'id','name','metadata','guid','count','preview','from_me','latest','participants'}
                    if (isinstance(saved,dict) and saved.get('version')==1 and
                        saved.get('archive')==str(self.path) and isinstance(saved.get('chats'),list) and
                        all(isinstance(chat,dict) and fields.issubset(chat) and
                            isinstance(chat['participants'],list) for chat in saved['chats'])):
                        self._library = saved
                        return saved
                except (FileNotFoundError,ValueError):
                    pass
            if not self.path.exists():
                return {'version':1,'archive':str(self.path),'chats':[]}
            chats = []
            with self.read() as db:
                db.execute('BEGIN')
                rows = db.execute('''SELECT c.id,c.name,c.metadata,c.guid,MAX(m.timestamp_ns) AS latest,
                    COUNT(DISTINCT CASE WHEN m.kind='message' THEN COALESCE(NULLIF(m.guid,''),m.id) END) AS count
                    FROM chats c JOIN memberships j ON j.chat_id=c.id
                    JOIN messages m ON m.id=j.message_id AND m.source_present=1
                    GROUP BY c.id ORDER BY latest DESC,c.id''').fetchall()
                participants = {}
                for row in db.execute('''SELECT cp.chat_id,p.address FROM chat_participants cp JOIN participants p
                    ON cp.source=p.source AND cp.handle_id=p.handle_id ORDER BY p.address'''):
                    participants.setdefault(row['chat_id'],[]).append(row['address'])
                for row in rows:
                    item = dict(row)
                    latest = db.execute('''SELECT m.text,m.kind,m.is_from_me FROM memberships j
                        JOIN messages m ON m.id=j.message_id WHERE j.chat_id=? AND m.source_present=1
                        ORDER BY m.timestamp_ns DESC,m.source_rowid DESC LIMIT 1''',(row['id'],)).fetchone()
                    item['preview'] = (latest['text'] or ('Reaction' if latest['kind']=='reaction' else 'Attachment or message event'))[:240]
                    item['from_me'] = bool(latest['is_from_me'])
                    item['latest'] = timestamp_iso(item['latest'])
                    item['participants'] = participants.get(row['id'],[])
                    chats.append(item)
            saved = {'version':1,'archive':str(self.path),'chats':chats}
            atomic_write(self.library_path,dumps(saved)+'\n')
            self._library = saved
            return saved

    def library_counts(self):
        counts = {'direct': 0, 'groups': 0, 'unknown': 0}
        for chat in self.library()['chats']:
            counts[chat_scope(chat['guid'], chat['metadata'], len(chat['participants']))] += 1
        return counts

    def contact_labels(self):
        book = self.contacts
        addresses = {address for chat in self.library()['chats'] for address in chat['participants'] if address}
        return {address:book.name(address) for address in addresses if book.name(address)!=address}

    def chats(self, search='', offset=0, chat_id=None, scope=None):
        book = self.contacts
        term = search.casefold()
        matches = []
        for original in self.library()['chats']:
            if chat_id is not None and original['id']!=chat_id:
                continue
            item = dict(original)
            item['scope'] = chat_scope(item['guid'], item['metadata'], len(item['participants']))
            if scope and item['scope'] != scope:
                continue
            item['original_name'] = item['name']
            item['name'] = book.chat_name(item['name'],item.pop('metadata'),item.pop('guid'),item['participants'])
            values = [item['name'],item['original_name'],*item['participants'],*[book.name(a) for a in item['participants']]]
            if term and not any(term in (value or '').casefold() for value in values):
                continue
            matches.append(item)
        return {'chats':matches[offset:offset+60],'has_more':len(matches)>offset+60}

    def messages(self, chat_id, offset=0):
        with self.read() as db:
            if not db.execute('SELECT 1 FROM chats WHERE id=?',(chat_id,)).fetchone():
                raise ValueError('Conversation not found.')
            rows = db.execute('''WITH ranked AS (
                SELECT m.id,m.text,m.timestamp_ns,m.source_rowid,m.is_from_me,m.kind,m.decode_status,m.decoded,
                COALESCE(CASE WHEN m.is_from_me THEN 'Me' ELSE p.address END,m.sender) AS sender,
                ROW_NUMBER() OVER(PARTITION BY m.source,COALESCE(NULLIF(m.guid,''),m.id)
                    ORDER BY m.source_rowid DESC) AS rank
                FROM messages m JOIN memberships j ON j.message_id=m.id
                LEFT JOIN participants p ON p.source=m.source AND p.handle_id=m.handle_id
                WHERE j.chat_id=? AND m.source_present=1)
                SELECT * FROM ranked WHERE rank=1 ORDER BY timestamp_ns DESC,source_rowid DESC,id DESC
                LIMIT 81 OFFSET ?''',(chat_id,offset)).fetchall()
            items = []
            for row in rows[:80]:
                item = {k:row[k] for k in ('id','text','is_from_me','kind','decode_status','sender')}
                item['timestamp'] = timestamp_iso(row['timestamp_ns'])
                parts = json.loads(row['decoded']).get('edits') or []
                item['edited'] = any(p.get('status')=='Edited' for p in parts)
                item['unsent'] = bool(parts) and all(p.get('status')=='Unsent' for p in parts)
                item['attachments'] = db.execute('SELECT COUNT(*) FROM message_attachments WHERE message_id=?',(row['id'],)).fetchone()[0]
                items.append(item)
        return {'messages':list(reversed(items)), 'has_more':len(rows)>80}

    def start(self, kind, payload):
        if kind not in ('sync','analyze','contacts','activity','semantic','word-context','explore','inspect'):
            raise ValueError('Unknown operation.')
        config = None
        if kind in ('explore', 'inspect'):
            from .explore import options
            config = self.config(payload)
            options(payload, config)
            if not self.path.exists():
                raise ValueError('Load your conversations first.')
            if kind == 'inspect':
                date.fromisoformat(payload['period'])
                if type(payload.get('offset', 0)) is not int or payload.get('offset', 0) < 0:
                    raise ValueError('Invalid message offset.')
        if kind == 'word-context':
            from .semantic import validate_context
            config = self.config(payload)
            validate_context(config,payload.get('person_ids',[]),payload.get('words',[]))
            if not self.path.exists():
                raise ValueError('Load your conversations first.')
        if kind == 'semantic':
            from .semantic import validate
            config = self.config(payload)
            validate(config,payload.get('person_ids',[]),payload.get('focus',''),payload.get('names',[]))
            if not self.path.exists():
                raise ValueError('Load your conversations first.')
        if kind == 'activity':
            from .activity import validate
            config = self.config(payload)
            validate(config,payload.get('metric','total'),payload.get('top',5),payload.get('ranking_mode','overall'))
            if not self.path.exists():
                raise ValueError('Load your conversations first.')
        if kind == 'analyze':
            config = self.config(payload)
            with self.read() as db:
                if not db.execute('SELECT 1 FROM chats WHERE id=?',(payload.get('chat_id'),)).fetchone():
                    raise ValueError('Select a conversation first.')
        with self.lock:
            if self.job['state'] == 'running':
                raise RuntimeError('Another operation is still running.')
            job_id = secrets.token_hex(12)
            self.job = {'id':job_id, 'state':'running', 'kind':kind, 'chat_id':payload.get('chat_id')}
            self.worker = threading.Thread(target=self.work, args=(kind,payload,config), daemon=False)
            self.worker.start()
        return {'id':job_id}

    def config(self, payload):
        zone = payload.get('timezone',self.args.timezone)
        ZoneInfo(zone)
        group = payload.get('group_by','week')
        if group not in ('day','week','month','day_of_week','time_of_day'):
            raise ValueError('Unknown activity grouping.')
        dates = [payload.get(key) or None for key in ('start_date','end_date')]
        for value in dates:
            if value and date.fromisoformat(value).isoformat()!=value:
                raise ValueError('Dates must use YYYY-MM-DD.')
        if all(dates) and dates[0]>=dates[1]:
            raise ValueError('End date must be after start date (the end date is exclusive).')
        return Config(timezone=zone, start_date=dates[0], end_date=dates[1], group_by=group,
                      filter_common_words=bool(payload.get('filter_common_words',True)),
                      separate_by_sender=bool(payload.get('separate_by_sender',True)), max_words=100)

    def work(self, kind, payload, config):
        try:
            if kind == 'sync':
                from .cli import execute
                args = copy(self.args)
                args.command = 'sync'
                code = execute(args)
                self.library(refresh=True)
                summary = json.loads((self.output/'latest-run.json').read_text())
                result = {'partial':code==2,'coverage':summary.get('coverage',{}), 'sync':summary.get('sync',{})}
            elif kind == 'contacts':
                if self.args.demo:
                    raise ValueError('Contact matching is disabled for fictional demo conversations.')
                book = ContactBook.from_records(read_macos_contacts())
                book.save(self.contacts_path)
                self.contacts = book
                result = {'count':book.count,'updated':book.updated,'matched_addresses':len(self.contact_labels())}
            elif kind in ('explore', 'inspect'):
                result = self.explore(config, payload, kind == 'inspect')
            elif kind == 'activity':
                result = self.activity(config,payload.get('metric','total'),payload.get('top',5),payload.get('ranking_mode','overall'))
            elif kind == 'semantic':
                result = self.semantic(config,payload.get('person_ids',[]),payload.get('focus',''),payload.get('names',[]))
            elif kind == 'word-context':
                result = self.word_context(config,payload.get('person_ids',[]),payload.get('words',[]))
            else:
                result = self.analyze(payload['chat_id'],config)
            with self.lock:
                self.job.update(state='complete',result=result)
        except Exception as error:
            with self.lock:
                self.job.update(state='failed',error=str(error) or type(error).__name__)

    def explore(self, config, payload, inspect=False):
        from .explore import VERSION, compute, options
        query = options(payload, config)
        with self.read() as db:
            db.execute('BEGIN')
            book, links = self.contacts, load_links(self.people_path)
            if inspect:
                return compute(db, People(db, book, links), query, config, payload['period'], payload.get('offset', 0))
            sources = [list(row) for row in db.execute('SELECT id,snapshot_hash,decoder,last_success FROM sources ORDER BY id')]
            key = digest([VERSION, str(self.path), sources, book.labels, book.identities, links, query])
            path = self.output / 'app-explore' / (key + '.json')
            try:
                saved = json.loads(path.read_text())
                if saved['query'] == query:
                    return saved
            except (OSError, ValueError, KeyError, TypeError):
                pass
            result = compute(db, People(db, book, links), query, config)
            atomic_write(path, dumps(result))
            return result

    def saved_views(self, payload=None):
        path = self.path.parent / 'explore-views.json'
        if payload is not None and not self.path.exists():
            raise ValueError('Load your conversations first.')
        guard = exclusive_lock(path.with_suffix('.lock')) if payload is not None else nullcontext()
        with self.lock, guard:
            try:
                views = json.loads(path.read_text())
            except FileNotFoundError:
                views = {}
            if payload is not None:
                from .explore import options
                name = payload.get('name', '')
                if not isinstance(name, str) or not name.strip() or len(name) > 80:
                    raise ValueError('Give the view a name of up to 80 characters.')
                name = name.strip()
                if payload.get('delete') is True:
                    views.pop(name, None)
                else:
                    query = options(payload, self.config(payload))
                    views[name] = query
                atomic_write(path, dumps(views))
            return views

    def activity(self, config, metric, top, ranking_mode='overall'):
        from .activity import VERSION, compute, select, validate
        validate(config,metric,top,ranking_mode)
        with self.read() as db:
            # One consistent read snapshot even if a separate CLI sync commits.
            db.execute('BEGIN')
            contacts = self.contacts
            links = load_links(self.people_path)
            sources = [list(row) for row in db.execute('SELECT id,snapshot_hash,decoder,last_success FROM sources ORDER BY id')]
            key = digest([VERSION,sources,contacts.labels,contacts.identities,links,
                          config.timezone,config.start_date,config.end_date,config.group_by,metric])
            path = self.output/'app-activity'/(key+'.json')
            cached = False
            try:
                result = json.loads(path.read_text())
                cached = isinstance(result,dict) and all(name in result for name in ('series','ranking','periods','period_leaders','total'))
            except (OSError,ValueError):
                pass
            if not cached:
                result = compute(db,People(db,contacts,links),config,metric)
                atomic_write(path,dumps(result)+'\n')
            return {**select(result,top,ranking_mode),'cached':cached}

    def semantic(self, config, person_ids, focus='', names=None):
        from .semantic import VERSION, compute, context, validate, words
        from .emoji import CONTEXT_VERSION
        from .text import TOKENIZER_VERSION
        names = [] if names is None else names
        validate(config,person_ids,focus,names)
        person_ids = sorted(set(person_ids))
        focus = words(focus)[0] if focus.strip() else ''
        names = sorted(set(name.strip() for name in names))

        def progress(message):
            with self.lock:
                if self.job.get('kind')=='semantic' and self.job['state']=='running':
                    self.job['progress'] = message

        def saved(path, required):
            try:
                result = json.loads(path.read_text())
                if isinstance(result,dict) and all(key in result for key in required):
                    return result
            except (OSError,ValueError):
                pass
            return None

        with self.read() as db:
            db.execute('BEGIN')
            contacts,links = self.contacts,load_links(self.people_path)
            sources = [list(row) for row in db.execute('SELECT id,snapshot_hash,decoder,last_success FROM sources ORDER BY id')]
            key = digest([VERSION,TOKENIZER_VERSION,sources,contacts.labels,contacts.identities,links,
                          config.timezone,config.start_date,config.end_date,config.group_by,person_ids,names])
            directory = self.output/'app-semantic'
            path = directory/(key+'.json')
            result = saved(path,('summary','top_words','timeline','people','names'))
            if result is not None:
                # Reuse full-range counts from older reports without exporting their obsolete split.
                result.pop('changes', None)
            cached = result is not None
            people = None
            if result is None:
                people = People(db,contacts,links)
                result = compute(db,people,contacts,config,person_ids,names,progress)
                atomic_write(path,dumps(result)+'\n')
            word_context = None
            if focus:
                context_path = directory/(digest([key,CONTEXT_VERSION,focus])+'.context.json')
                word_context = saved(context_path,('word','timeline','people'))
                if word_context is None:
                    cached = False
                    people = people or People(db,contacts,links)
                    word_context = context(db,people,config,person_ids,focus,[r['period'] for r in result['timeline']],progress)
                    atomic_write(context_path,dumps(word_context)+'\n')
            return {**result,'context':word_context,'cached':cached}

    def word_context(self, config, person_ids, selected_words):
        from .semantic import VERSION, contexts, validate_context
        from .emoji import CONTEXT_VERSION
        from .text import TOKENIZER_VERSION
        selected_words = validate_context(config,person_ids,selected_words)
        person_ids = sorted(set(person_ids))

        def progress(message):
            with self.lock:
                if self.job.get('kind')=='word-context' and self.job['state']=='running':
                    self.job['progress'] = message

        with self.read() as db:
            db.execute('BEGIN')
            contacts,links = self.contacts,load_links(self.people_path)
            sources = [list(row) for row in db.execute('SELECT id,snapshot_hash,decoder,last_success FROM sources ORDER BY id')]
            # Context has its own tokenizer version; word-only vocabulary caches stay valid.
            key = digest([VERSION,TOKENIZER_VERSION,sources,contacts.labels,contacts.identities,links,
                          config.timezone,config.start_date,config.end_date,config.group_by,person_ids,[]])
            directory = self.output/'app-semantic'
            found,missing = {},[]
            for word in selected_words:
                path = directory/(digest([key,CONTEXT_VERSION,word])+'.context.json')
                try:
                    result = json.loads(path.read_text())
                    if not isinstance(result,dict) or result.get('word')!=word or not all(k in result for k in ('timeline','people')):
                        raise ValueError('Invalid context cache')
                    found[word] = result
                except (OSError,ValueError):
                    missing.append(word)
            if missing:
                people = People(db,contacts,links)
                for result in contexts(db,people,config,person_ids,missing,progress=progress):
                    word = result['word']
                    found[word] = result
                    atomic_write(directory/(digest([key,CONTEXT_VERSION,word])+'.context.json'),dumps(result)+'\n')
            return {'group_by':config.group_by,'timezone':config.timezone,'start_date':config.start_date,
                    'end_date':config.end_date,'person_ids':person_ids,'words':selected_words,
                    'contexts':[found[word] for word in selected_words],'cached':not missing}

    def analyze(self, chat_id, config):
        with exclusive_lock(self.path.parent/'archive.lock'), Archive(self.path) as archive:
            chat = dict(archive.db.execute('SELECT * FROM chats WHERE id=?',(chat_id,)).fetchone())
            key = 'app:'+config.key()+':'+chat_id
            previous = archive.db.execute('SELECT * FROM reports WHERE cache_key=?',(key,)).fetchone()
            source = archive.db.execute('SELECT snapshot_hash,decoder,last_success FROM sources WHERE id=?',
                                        (chat['source'],)).fetchone()
            context = digest([list(source) if source else [],chat['metadata']])
            stamp = previous['fingerprint'] if previous and previous['context_stamp']==context else fingerprint(archive,chat,config)
            cached = bool(previous and previous['fingerprint']==stamp)
            metrics = json.loads(previous['summary']) if cached else analyze(archive,chat,config)
            directory = self.output/'app-reports'/digest(key)/stamp[:16]
            directory.mkdir(parents=True, exist_ok=True)
            os.environ.setdefault('MPLCONFIGDIR',str(self.path.parent/'matplotlib'))
            os.environ.setdefault('XDG_CACHE_HOME',str(self.path.parent/'cache'))
            if not cached or (metrics['word_frequencies'] and not (directory/'wordcloud.png').exists()):
                render_charts((chat['name'],metrics,str(directory),config.max_words))
            archive.db.execute('INSERT OR REPLACE INTO reports VALUES(?,?,?,?,?)',
                               (key,stamp,context,str(directory),dumps(metrics)))
            archive.db.commit()
            with self.lock:
                self.leaderboard_messages.update((chat_id,row['id']) for row in
                    (metrics.get('reactions_received',{}).get('hearts') or {}).get('top_messages',[]))
            artifacts = {}
            for path in directory.glob('*.png'):
                artifact_id = digest(str(path))
                with self.lock:
                    self.artifacts[artifact_id] = path
                artifacts[path.stem] = '/api/artifact?id='+artifact_id
            return {'chat_id':chat_id,'name':chat['name'],'metrics':metrics,'config':asdict(config),
                    'cached':cached,'artifacts':artifacts}

    def leaderboard_attachments(self, chat_id, message_id, attachment_id=None, preview=False):
        from .media import describe, image_preview
        with self.lock:
            if (chat_id,message_id) not in self.leaderboard_messages:
                raise ValueError('Open a group-chat analysis leaderboard first.')
        with self.read() as db:
            message = db.execute('''SELECT m.decoded FROM messages m JOIN memberships j ON j.message_id=m.id
                WHERE m.id=? AND j.chat_id=? AND m.source_present=1 AND m.kind='message' ''',(message_id,chat_id)).fetchone()
            if message is None: raise ValueError('Message is no longer available.')
            if any(part.get('status')=='Unsent' for part in (json.loads(message['decoded']).get('edits') or [])):
                if attachment_id: raise ValueError('Attachments of unsent message parts are not displayed.')
                return {'attachments':[],'note':'Attachments hidden because this message contains unsent parts.'}
            rows = db.execute('''SELECT a.*,s.path AS source_path FROM message_attachments ma
                JOIN attachments a ON a.id=ma.attachment_id JOIN sources s ON s.id=a.source
                WHERE ma.message_id=? ORDER BY a.id''',(message_id,)).fetchall()
        result = []
        for row in rows:
            if attachment_id and row['id']!=attachment_id: continue
            roots = [self.path.parent/'media',Path.home()/'Library/Messages/Attachments']
            if self.args.attachment_root: roots.append(self.args.attachment_root)
            if row['source_path']: roots.append(Path(row['source_path']).expanduser().parent/'Attachments')
            item,path = describe(row,roots)
            if attachment_id:
                if path is None: raise ValueError('Attachment is not available locally. It may be missing, stored only in iCloud, or blocked by macOS permissions.')
                if preview:
                    if item['kind']!='image': raise ValueError('This attachment does not have an image preview.')
                    converted,mime = image_preview(path,item['mime'],self.path.parent/'app-media-previews')
                    return converted,mime,item['name']
                # Originals are downloaded or loaded as authenticated blobs, never active HTML.
                return path,'application/octet-stream',item['name']
            result.append(item)
        if attachment_id: raise ValueError('Attachment does not belong to this leaderboard message.')
        return {'attachments':result}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Message identifiers and session tokens never go to access logs.

    def send(self, status, body, content_type='application/json'):
        if content_type=='application/json':
            body = dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; img-src 'self' blob:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.dispatch(False)

    def do_POST(self):
        self.dispatch(True)

    def send_attachment(self, path, mime, filename):
        from urllib.parse import quote
        with path.open('rb') as stream:
            self.send_response(200)
            self.send_header('Content-Type',mime)
            self.send_header('Content-Length',str(os.fstat(stream.fileno()).st_size))
            self.send_header('Content-Disposition',"attachment; filename*=UTF-8''"+quote(filename,safe=''))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'none'; sandbox")
            self.end_headers()
            try:
                while chunk := stream.read(128*1024): self.wfile.write(chunk)
            except (BrokenPipeError,ConnectionResetError):
                pass

    def dispatch(self, post):
        app = self.server.app
        parsed = urlsplit(self.path)
        try:
            if self.headers.get('Host') != urlsplit(app.origin).netloc:
                return self.send(403,{'error':'Invalid local host.'})
            if self.headers.get('Origin') not in (None, app.origin):
                return self.send(403,{'error':'Cross-origin requests are not allowed.'})
            if parsed.path.startswith('/api/'):
                if not secrets.compare_digest(self.headers.get('Authorization',''), 'Bearer '+app.token):
                    return self.send(401,{'error':'Open the app using the full URL printed in your terminal.'})
                query = {k:v[-1] for k,v in parse_qs(parsed.query).items()}
                offset = int(query.get('offset','0'))
                if offset<0:
                    raise ValueError('Invalid page offset.')
                if post:
                    size = int(self.headers.get('Content-Length','0'))
                    if not 0<size<=16384:
                        raise ValueError('Invalid request size.')
                    payload = json.loads(self.rfile.read(size))
                    if not isinstance(payload,dict):
                        raise ValueError('Expected an object.')
                    if parsed.path in ('/api/sync','/api/analyze','/api/contacts','/api/activity','/api/semantic','/api/word-context','/api/explore','/api/inspect'):
                        return self.send(202,app.start(parsed.path.rsplit('/',1)[1],payload))
                    if parsed.path == '/api/views':
                        return self.send(200, app.saved_views(payload))
                    if parsed.path in ('/api/people/link','/api/people/unlink'):
                        return self.send(200,app.link_people(payload,unlink=parsed.path.endswith('/unlink')))
                elif parsed.path == '/api/views':
                    return self.send(200, app.saved_views())
                elif parsed.path=='/api/status':
                    return self.send(200,app.status())
                elif parsed.path=='/api/leaderboard-attachments':
                    return self.send(200,app.leaderboard_attachments(query.get('chat_id',''),query.get('message_id','')))
                elif parsed.path=='/api/leaderboard-attachment':
                    if not query.get('attachment_id'): raise ValueError('Select an attachment.')
                    return self.send_attachment(*app.leaderboard_attachments(query.get('chat_id',''),query.get('message_id',''),query['attachment_id'],query.get('preview')=='1'))
                elif parsed.path=='/api/library-counts':
                    return self.send(200,app.library_counts())
                elif parsed.path=='/api/group-members':
                    return self.send(200, app.group_members(query.get('id','')))
                elif parsed.path=='/api/chats':
                    return self.send(200,app.chats(query.get('q',''),offset,scope=query.get('scope')))
                elif parsed.path=='/api/chat':
                    chats = app.chats(chat_id=query.get('id',''))['chats']
                    if not chats:
                        raise ValueError('Conversation not found or empty.')
                    return self.send(200,chats[0])
                elif parsed.path=='/api/people':
                    return self.send(200,app.people(query.get('q',''),offset,direct_only=query.get('scope')=='direct'))
                elif parsed.path=='/api/person':
                    return self.send(200,app.people(person_id=query.get('id','')))
                elif parsed.path=='/api/person/messages':
                    return self.send(200,app.people(person_id=query.get('id',''),scope=query.get('scope','direct'),offset=offset))
                elif parsed.path=='/api/contact-labels':
                    return self.send(200,app.contact_labels())
                elif parsed.path=='/api/messages':
                    return self.send(200,app.messages(query.get('chat_id',''),offset))
                elif parsed.path=='/api/artifact':
                    with app.lock:
                        path = app.artifacts.get(query.get('id'))
                    if path:
                        return self.send(200,path.read_bytes(),'image/png')
            elif not post:
                filename = {'/':'index.html','/app.js':'app.js','/explore.js':'explore.js','/explore.css':'explore.css','/semantic.js':'semantic.js','/heart-media.js':'heart-media.js','/style.css':'style.css','/emoji-picker.json':'emoji-picker.json'}.get(parsed.path)
                if filename:
                    return self.send(200,(WEB/filename).read_bytes(),mimetypes.guess_type(filename)[0]+'; charset=utf-8')
            self.send(404,{'error':'Not found.'})
        except (ValueError,KeyError,TypeError) as error:
            self.send(400,{'error':str(error)})
        except RuntimeError as error:
            self.send(409,{'error':str(error)})
        except (OSError,sqlite3.Error) as error:
            self.send(500,{'error':str(error)})


def make_server(args):
    app = App(args)
    server = ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    server.daemon_threads = True
    server.app = app
    app.origin = 'http://127.0.0.1:'+str(server.server_port)
    return server


def serve(args):
    if args.demo:
        from .demo import prepare
        args = prepare(args)
    server = make_server(args)
    url = server.app.origin+'/#'+server.app.token
    print('Py-Message app: '+url+'\nPress Ctrl+C to stop.',flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nClosing Py-Message. Finishing any active archive operation…',flush=True)
    finally:
        server.server_close()
        if server.app.worker:
            server.app.worker.join()
    return 0
