"""Reversible identity groups and message views across existing conversations.

No canonical messages or memberships are rewritten. Contact identities and
explicit user links define groups; equal display names do not imply identity.
"""
from collections import defaultdict
import json
from pathlib import Path

from .contacts import address_key
from .reports import timestamp_iso
from .util import atomic_write, digest, dumps


def load_links(path):
    try:
        data = json.loads(Path(path).read_text())
        links = data['links']
        if data['version']!=1 or not isinstance(links,list) or any(
            not isinstance(pair,list) or len(pair)!=2 or not all(isinstance(key,str) and key for key in pair)
            for pair in links):
            raise ValueError('Invalid links')
        return links
    except FileNotFoundError:
        return []
    except (ValueError,KeyError,TypeError,AttributeError) as error:
        raise RuntimeError('Saved people links could not be read. Restore people-links.json from a backup before editing links.') from error


def save_links(path, links):
    atomic_write(path,dumps({'version':1,'links':links})+'\n')


def chat_scope(guid, metadata, participant_count):
    # Apple group GUIDs retain their group marker even after members leave.
    if ';+;' in (guid or '') or json.loads(metadata or '{}').get('style') == 43 or participant_count > 1:
        return 'groups'
    return 'direct' if participant_count == 1 else 'unknown'


class People:
    def __init__(self, db, contacts, links):
        self.contacts = contacts
        self.links = links
        parent = {}
        def root(key):
            parent.setdefault(key,key)
            while parent[key]!=key:
                parent[key] = parent[parent[key]]
                key = parent[key]
            return key
        def join(a,b):
            a,b = root(a),root(b)
            parent[max(a,b)] = min(a,b)
        def identity(key):
            return contacts.identities.get(key,key)
        for a,b in links:
            join(identity(a),identity(b))
        self.by_handle = {}
        addresses_by_handle = {}
        self.people = {}
        for row in db.execute('''SELECT DISTINCT p.source,p.handle_id,p.address FROM chat_participants cp
            JOIN participants p ON cp.source=p.source AND cp.handle_id=p.handle_id'''):
            address = row['address']
            key = address_key(address)
            # Unknown/HTML sender labels stay isolated by source and handle.
            # A shared display name never links otherwise distinct handles.
            key = key or 'handle:'+digest([row['source'],row['handle_id']])
            group = root(identity(key))
            person_id = 'person:'+digest(group)
            self.by_handle[(row['source'],row['handle_id'])] = person_id
            addresses_by_handle[(row['source'],row['handle_id'])] = address
            person = self.people.setdefault(person_id,{'id':person_id,'addresses':set(),'address_keys':set(),
                'handles':set(),'threads':[],'direct_count':0,'group_count':0,'manual_links':False})
            person['handles'].add((row['source'],row['handle_id']))
            person['address_keys'].add(key)
            if address:
                person['addresses'].add(address)
        for person in self.people.values():
            keys = person['address_keys']
            person['manual_links'] = any(root(identity(a))==root(identity(next(iter(keys)))) for a,b in links)
            addresses = sorted(person['addresses'],key=str.casefold)
            names = sorted({contacts.name(a) for a in addresses if contacts.name(a)!=a},key=str.casefold)
            person['name'] = names[0] if names else (addresses[0] if addresses else 'Unknown sender')
            person['addresses'] = addresses
        memberships = defaultdict(list)
        chat_addresses = defaultdict(set)
        for row in db.execute('SELECT chat_id,source,handle_id FROM chat_participants'):
            person_id = self.by_handle.get((row['source'],row['handle_id']))
            if person_id:
                memberships[row['chat_id']].append(person_id)
                address = addresses_by_handle[(row['source'],row['handle_id'])]
                if address:
                    chat_addresses[row['chat_id']].add(address)
        self.threads = {}
        for row in db.execute('SELECT id,name,guid,metadata FROM chats'):
            members = set(memberships[row['id']])
            scope = chat_scope(row['guid'], row['metadata'], len(memberships[row['id']]))
            addresses = sorted(chat_addresses[row['id']])
            thread = {'id':row['id'],'name':contacts.chat_name(row['name'],row['metadata'],row['guid'],addresses),
                      'scope':scope,'addresses':addresses}
            self.threads[row['id']] = thread
            for member in members:
                person = self.people[member]
                person['threads'].append(thread)
                person['direct_count' if scope=='direct' else 'group_count'] += 1
        # Participants lacking any retained chat aren't useful directory entries.
        self.people = {key:value for key,value in self.people.items() if value['threads']}

    def get(self, person_id):
        if person_id not in self.people:
            raise ValueError('Person not found. Refresh the people list after changing contacts or links.')
        return self.people[person_id]

    def public(self, person_id, detail=False):
        person = self.get(person_id)
        result = {key:person[key] for key in ('id','name','addresses','direct_count','group_count','manual_links')}
        result['thread_count'] = len(person['threads'])
        if detail:
            result['threads'] = sorted(person['threads'],key=lambda item:(item['scope'],item['name'] or '',item['id']))
        return result

    def listing(self, search='', offset=0, direct_only=False):
        term = search.casefold()
        values = sorted(self.people.values(),key=lambda person:(person['name'].casefold(),person['id']))
        if direct_only:
            values = [person for person in values if person['direct_count']]
        values = [person for person in values if not term or any(term in value.casefold() for value in [person['name'],*person['addresses']])]
        return {'people':[self.public(person['id']) for person in values[offset:offset+60]],
                'has_more':len(values)>offset+60,'identity_ready':self.contacts.identity_ready}

    def merged_links(self, first, second):
        if first==second:
            raise ValueError('Choose a different person to link.')
        a,b = self.get(first),self.get(second)
        return [*self.links,[min(a['address_keys']),min(b['address_keys'])]]

    def unlinked(self, person_id):
        person = self.get(person_id)
        # Resolve endpoints through their current identity groups; this also
        # removes edges retained for addresses no longer in the archive.
        identity = lambda key:self.contacts.identities.get(key,key)
        connected = {identity(key) for key in person['address_keys']}
        changed = True
        while changed:
            changed = False
            for a,b in self.links:
                if identity(a) in connected or identity(b) in connected:
                    before = len(connected)
                    connected.update((identity(a),identity(b)))
                    changed |= len(connected)!=before
        return [[a,b] for a,b in self.links if identity(a) not in connected and identity(b) not in connected]

    def messages(self, db, person_id, scope='direct', offset=0):
        if scope not in ('direct','groups'):
            raise ValueError('Unknown person message view.')
        person = self.get(person_id)
        selected = [thread for thread in person['threads'] if thread['scope']==scope]
        # Temporary tables avoid SQL variable limits for people with many
        # handles/threads. These exist only on this private read connection.
        db.execute('CREATE TEMP TABLE person_threads(id TEXT PRIMARY KEY)')
        db.executemany('INSERT INTO person_threads VALUES(?)',((thread['id'],) for thread in selected))
        db.execute('CREATE TEMP TABLE person_handles(source TEXT,handle TEXT,PRIMARY KEY(source,handle))')
        db.executemany('INSERT INTO person_handles VALUES(?,?)',sorted(person['handles']))
        sender = '''EXISTS(SELECT 1 FROM person_handles ph WHERE ph.source=m.source AND ph.handle=m.handle_id)'''
        direction = f'(m.is_from_me=1 OR ((m.is_from_me IS NULL OR m.is_from_me=0) AND {sender}))' if scope=='direct' else f'(COALESCE(m.is_from_me,0)=0 AND {sender})'
        query = f'''WITH ranked AS (
            SELECT m.*,COALESCE(CASE WHEN m.is_from_me THEN 'Me' ELSE p.address END,m.sender) AS display_sender,
            ROW_NUMBER() OVER(PARTITION BY COALESCE(NULLIF(m.guid,''),m.id)
                ORDER BY m.timestamp_ns DESC,m.source_rowid DESC,m.id DESC) AS rank
            FROM messages m LEFT JOIN participants p ON p.source=m.source AND p.handle_id=m.handle_id
            WHERE m.source_present=1 AND m.kind='message' AND {direction}
            AND m.id IN (SELECT j.message_id FROM person_threads t CROSS JOIN memberships j ON t.id=j.chat_id))
            SELECT * FROM ranked WHERE rank=1 ORDER BY timestamp_ns DESC,source_rowid DESC,id DESC LIMIT 81 OFFSET ?'''
        rows = db.execute(query,(offset,)).fetchall()
        db.execute('CREATE TEMP TABLE person_page(id TEXT PRIMARY KEY,guid TEXT)')
        db.executemany('INSERT INTO person_page VALUES(?,?)',((row['id'],row['guid'] or None) for row in rows[:80]))
        origins_by_message = defaultdict(dict)
        for origin in db.execute('''SELECT m.id,m.guid,j.chat_id FROM person_threads t
            CROSS JOIN memberships j ON t.id=j.chat_id JOIN messages m ON m.id=j.message_id
            WHERE m.source_present=1 AND m.kind='message' AND
            (m.id IN (SELECT id FROM person_page) OR m.guid IN (SELECT guid FROM person_page WHERE guid IS NOT NULL))'''):
            key = origin['guid'] or origin['id']
            origins_by_message[key][origin['chat_id']] = self.threads[origin['chat_id']]
        items = []
        for row in rows[:80]:
            item = {key:row[key] for key in ('id','text','is_from_me','kind','decode_status')}
            item['sender'] = row['display_sender']
            item['timestamp'] = timestamp_iso(row['timestamp_ns'])
            edits = json.loads(row['decoded']).get('edits') or []
            item['edited'] = any(part.get('status')=='Edited' for part in edits)
            item['unsent'] = bool(edits) and all(part.get('status')=='Unsent' for part in edits)
            item['attachments'] = db.execute('SELECT COUNT(*) FROM message_attachments WHERE message_id=?',(row['id'],)).fetchone()[0]
            # Retain all selected source threads even when GUIDs deduplicate
            # copies of the same message across imports and memberships.
            item['origins'] = sorted(origins_by_message[row['guid'] or row['id']].values(),key=lambda thread:thread['id'])
            items.append(item)
        return {'messages':list(reversed(items)),'has_more':len(rows)>80}
