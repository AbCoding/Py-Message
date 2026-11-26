"""Identity grouping and cross-thread views on small local fixtures."""
import json
from pathlib import Path
import tempfile
import unittest

from pymessage.app import App
from pymessage.archive import Archive
from pymessage.cli import parser
from pymessage.contacts import ContactBook
from pymessage.people import load_links
from pymessage.util import file_digest

CONTACTS = [
    {'id':'contact-a','name':'Alex','phones':['+1 317 555 0100'],'emails':['alex@example.invalid']},
    {'id':'contact-b','name':'Alex','phones':[],'emails':['other@example.invalid']},
]


class PeopleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.args = parser().parse_args(['app','--output',self.temp.name,'--timezone','UTC'])
        self.app = App(self.args)
        self.writer = Archive(self.app.path)
        self.db = self.writer.db
        self.db.execute("INSERT INTO sources(id,path,kind) VALUES('s','fixture','sqlite')")
        for handle,address in [('a','+13175550100'),('email','alex@example.invalid'),('b','other@example.invalid'),('format','(317) 555-0100')]:
            self.db.execute('INSERT INTO participants VALUES(?,?,?,?)',('s',handle,address,'{}'))
        for chat,handles in [('phone',['a']),('email',['email']),('bob',['b']),('group',['a','b']),('format',['format'])]:
            self.db.execute('INSERT INTO chats VALUES(?,?,?,?,?,?)',(chat,'s',chat,chat,chat,json.dumps({'display_name':chat})))
            for handle in handles:
                self.db.execute('INSERT INTO chat_participants VALUES(?,?,?)',(chat,'s',handle))
        self.counter = 0
        self.add('phone','Me',guid='my-phone')
        self.add('phone','a',guid='their-phone')
        self.add('email','Me',guid='my-email')
        self.add('email','email',guid='their-email')
        self.add('group','a',guid='group-a')
        self.add('group','b',guid='group-b')
        self.add('group','Me',guid='group-me')
        self.add('bob','b',guid='bob-message')
        self.db.commit()

    def tearDown(self):
        self.writer.close()
        self.temp.cleanup()

    def add(self, chat, sender, guid=None, source='s', present=1, kind='message'):
        self.counter += 1
        identifier = source+':'+str(self.counter)
        self.db.execute('''INSERT INTO messages(id,source,source_rowid,guid,timestamp_ns,text,sender,handle_id,
            is_from_me,kind,raw_hash,decoded,decode_status,source_present) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (identifier,source,self.counter,guid,self.counter*10**9,guid or identifier,sender,sender,
             int(sender=='Me'),kind,'hash','{}','plain',present))
        self.db.execute('INSERT INTO memberships VALUES(?,?,0)',(chat,identifier))
        return identifier

    def person(self, address):
        return next(p for p in self.app.people()['people'] if address in p['addresses'])

    def matched(self):
        self.app.contacts = ContactBook.from_records(CONTACTS)
        self.app.contacts.save(self.app.contacts_path)

    def test_same_number_formats_group_without_contacts(self):
        person = self.person('+13175550100')
        self.assertEqual(set(person['addresses']),{'+13175550100','(317) 555-0100'})
        self.assertEqual(person['direct_count'],2)
        self.assertEqual(person['group_count'],1)
        self.assertNotEqual(person['id'],self.person('alex@example.invalid')['id'])

    def test_contact_identity_merges_aliases_but_not_equal_names(self):
        self.matched()
        alex = self.person('+13175550100')
        self.assertIn('alex@example.invalid',alex['addresses'])
        other = self.person('other@example.invalid')
        self.assertEqual(alex['name'],other['name'])
        self.assertNotEqual(alex['id'],other['id'])
        self.assertEqual(len(self.app.people('alex')['people']),2)
        self.assertEqual(len(self.app.people('317')['people']),1)

    def test_shared_contact_address_does_not_link_two_contacts(self):
        self.app.contacts = ContactBook.from_records([
            {'id':'one','name':'Same name','phones':['+13175550100'],'emails':['alex@example.invalid']},
            {'id':'two','name':'Same name','phones':['+13175550100'],'emails':['other@example.invalid']},
        ])
        ids = {self.person(address)['id'] for address in ('+13175550100','alex@example.invalid','other@example.invalid')}
        self.assertEqual(len(ids),3)

    def test_old_name_only_cache_does_not_merge_by_name(self):
        self.app.contacts = ContactBook({'phone:3175550100':'Alex','email:alex@example.invalid':'Alex'},'date',1)
        self.assertFalse(self.app.people()['identity_ready'])
        self.assertNotEqual(self.person('+13175550100')['id'],self.person('alex@example.invalid')['id'])

    def test_direct_view_includes_both_sides_and_group_view_only_author(self):
        self.matched()
        person = self.person('+13175550100')
        direct = self.app.people(person_id=person['id'],scope='direct')['messages']
        self.assertEqual([m['text'] for m in direct],['my-phone','their-phone','my-email','their-email'])
        groups = self.app.people(person_id=person['id'],scope='groups')['messages']
        self.assertEqual([m['text'] for m in groups],['group-a'])
        self.assertEqual(groups[0]['origins'][0]['id'],'group')
        self.assertEqual(self.app.chats(chat_id='phone')['chats'][0]['id'],'phone')

    def test_deduplicates_memberships_guids_and_sources_preserving_origins(self):
        self.matched()
        self.db.execute("INSERT INTO memberships VALUES('email','s:2',0)")
        self.add('email','email',guid='their-phone')
        self.db.execute("INSERT INTO participants VALUES('copy','a','+13175550100','{}')")
        self.db.execute("INSERT INTO chats VALUES('copy-chat','copy','1','copy-chat','Copy','{}')")
        self.db.execute("INSERT INTO chat_participants VALUES('copy-chat','copy','a')")
        self.add('copy-chat','a',guid='their-phone',source='copy')
        self.db.commit()
        result = self.app.people(person_id=self.person('+13175550100')['id'],scope='direct')['messages']
        copies = [message for message in result if message['text']=='their-phone']
        self.assertEqual(len(copies),1)
        self.assertEqual({t['id'] for t in copies[0]['origins']},{'phone','email','copy-chat'})
        self.assertEqual(len(result),4)

    def test_excludes_absent_events_and_unrelated_group_authors(self):
        self.matched()
        self.add('phone','a',guid='absent',present=0)
        self.add('phone','a',guid='reaction',kind='reaction')
        self.add('phone','b',guid='unrelated')
        self.db.commit()
        messages = self.app.people(person_id=self.person('+13175550100')['id'],scope='direct')['messages']
        self.assertEqual(len(messages),4)

    def test_pagination_returns_all_messages_once(self):
        self.matched()
        for i in range(165):
            self.add('phone','a',guid='page-'+str(i))
        self.db.commit()
        person = self.person('+13175550100')
        first = self.app.people(person_id=person['id'],scope='direct')
        second = self.app.people(person_id=person['id'],scope='direct',offset=80)
        third = self.app.people(person_id=person['id'],scope='direct',offset=160)
        self.assertTrue(first['has_more'])
        self.assertTrue(second['has_more'])
        self.assertFalse(third['has_more'])
        ids = [m['id'] for page in (first,second,third) for m in page['messages']]
        self.assertEqual(len(ids),169)
        self.assertEqual(len(set(ids)),169)
        self.assertLess(third['messages'][-1]['timestamp'],second['messages'][0]['timestamp'])

    def test_manual_link_persists_and_undo_preserves_contact_groups(self):
        self.matched()
        # Close/checkpoint the fixture writer so file checks reflect data, not WAL maintenance.
        self.writer.close()
        before = file_digest(self.app.path)
        a,b = self.person('+13175550100'),self.person('other@example.invalid')
        linked = self.app.link_people({'person_id':a['id'],'other_id':b['id']})
        self.assertTrue(linked['manual_links'])
        self.assertEqual(len(self.app.people()['people']),1)
        restarted = App(self.args)
        self.assertEqual(len(restarted.people()['people']),1)
        separated = restarted.link_people({'person_id':linked['id']},unlink=True)
        self.assertFalse(separated['manual_links'])
        self.assertEqual(len(restarted.people()['people']),2)
        grouped = next(p for p in restarted.people()['people'] if '+13175550100' in p['addresses'])
        self.assertIn('alex@example.invalid',grouped['addresses'])
        self.assertEqual(file_digest(self.app.path),before)
        self.assertEqual(load_links(self.app.people_path),[])

    def test_transitive_links_can_be_undone(self):
        a,b,c = [self.person(address) for address in ('+13175550100','alex@example.invalid','other@example.invalid')]
        first = self.app.link_people({'person_id':a['id'],'other_id':b['id']})
        second = self.app.link_people({'person_id':first['id'],'other_id':c['id']})
        self.assertEqual(len(self.app.people()['people']),1)
        self.app.link_people({'person_id':second['id']},unlink=True)
        self.assertEqual(len(self.app.people()['people']),3)

    def test_validation_and_damaged_links(self):
        person = self.person('+13175550100')
        with self.assertRaises(ValueError):
            self.app.link_people({'person_id':person['id'],'other_id':person['id']})
        with self.assertRaises(ValueError):
            self.app.people(person_id='missing',scope='direct')
        with self.assertRaises(ValueError):
            self.app.people(person_id=person['id'],scope='invalid')
        self.app.people_path.write_text('invalid')
        with self.assertRaisesRegex(RuntimeError,'Saved people links'):
            self.app.people()


if __name__ == '__main__':
    unittest.main()
