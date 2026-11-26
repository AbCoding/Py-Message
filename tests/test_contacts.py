"""Contact matching and app integration without reading personal Contacts."""
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pymessage.app import App
from pymessage.archive import Archive
from pymessage.cli import parser
from pymessage.contacts import ContactBook, address_key, read_macos_contacts
from pymessage.util import file_digest

RECORDS = [
    {'name':'Alex Rivera','phones':['+1 (317) 555-0123'],'emails':['Alex@Example.com']},
    {'name':'Zoë Chen','phones':['+44 20 7946 0958'],'emails':['zoe@example.com']},
]


class ContactTests(unittest.TestCase):
    def test_normalization_and_unmatched_values(self):
        book = ContactBook.from_records(RECORDS)
        self.assertEqual(book.name('317-555-0123'),'Alex Rivera')
        self.assertEqual(book.name('tel:+13175550123'),'Alex Rivera')
        self.assertEqual(book.name(' ALEX@example.com '),'Alex Rivera')
        self.assertEqual(book.name('00442079460958'),'Zoë Chen')
        self.assertEqual(book.name('02079460958'),'02079460958')
        self.assertEqual(book.name('3175550123 ext 5'),'3175550123 ext 5')
        self.assertIsNone(address_key('12345'))
        self.assertEqual(book.name('unknown@example.com'),'unknown@example.com')
        self.assertTrue(book.matches('zoe@example.com','ZOË'))

    def test_ambiguous_matches_stay_unresolved(self):
        book = ContactBook.from_records(RECORDS+[
            {'name':'Someone else','phones':['3175550123'],'emails':[]},
        ])
        self.assertEqual(book.name('+13175550123'),'+13175550123')
        self.assertEqual(book.name('alex@example.com'),'Alex Rivera')
        duplicate = ContactBook.from_records(RECORDS+RECORDS)
        self.assertEqual(duplicate.name('3175550123'),'Alex Rivera')

    def test_preserves_group_and_html_titles(self):
        book = ContactBook.from_records(RECORDS)
        self.assertEqual(book.chat_name('+13175550123','{}','guid',['+13175550123']),'Alex Rivera')
        self.assertEqual(book.chat_name('chat123','{}','chat123',['+13175550123','zoe@example.com']),'Alex Rivera, Zoë Chen')
        self.assertEqual(book.chat_name('Family','{"display_name":"Family"}','chat123',['+13175550123']),'Family')
        self.assertEqual(book.chat_name('Old friend','{"html_file":"chat.html"}',None,['+13175550123']),'Old friend')
        self.assertEqual(book.chat_name('Custom title','{}','chat123',['+13175550123']),'Custom title')

    def test_cache_is_private_and_bad_cache_is_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'contacts.json'
            ContactBook.from_records(RECORDS).save(path)
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            self.assertEqual(ContactBook.load(path).name('alex@example.com'),'Alex Rivera')
            for bad in ('not json','[]','{"version":1,"labels":[]}'):
                path.write_text(bad)
                self.assertEqual(ContactBook.load(path).labels,{})

    @patch('pymessage.contacts.platform.system',return_value='Darwin')
    def test_reader_only_returns_expected_data(self, system):
        with patch('pymessage.contacts.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(RECORDS),stderr='')) as run:
            self.assertEqual(read_macos_contacts(),RECORDS)
            self.assertEqual(run.call_args.args[0][:3],['/usr/bin/osascript','-l','JavaScript'])
            self.assertEqual(run.call_args.kwargs['timeout'],90)

    @patch('pymessage.contacts.platform.system',return_value='Darwin')
    def test_permission_timeout_and_malformed_responses(self, system):
        with patch('pymessage.contacts.subprocess.run',return_value=SimpleNamespace(returncode=1,stdout='',stderr='not authorized (-1743)')):
            with self.assertRaisesRegex(RuntimeError,'Automation'):
                read_macos_contacts()
        with patch('pymessage.contacts.subprocess.run',side_effect=subprocess.TimeoutExpired('osascript',90)):
            with self.assertRaisesRegex(RuntimeError,'permission prompt'):
                read_macos_contacts()
        with patch('pymessage.contacts.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout='{}',stderr='')):
            with self.assertRaisesRegex(RuntimeError,'unexpected format'):
                read_macos_contacts()


class ContactAppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.args = parser().parse_args(['app','--output',self.temp.name,'--timezone','UTC'])
        self.app = App(self.args)
        with Archive(self.app.path) as archive:
            db = archive.db
            db.execute("INSERT INTO participants VALUES('source','1','+13175550123','{}')")
            db.execute("INSERT INTO participants VALUES('source','2','zoe@example.com','{}')")
            for i,(name,metadata,handle) in enumerate([
                ('+13175550123','{}','1'),
                ('zoe@example.com','{}','2'),
                ('Family','{"display_name":"Family"}','1'),
            ],1):
                db.execute('INSERT INTO chats VALUES(?,?,?,?,?,?)',(str(i),'source',str(i),'chat'+str(i),name,metadata))
                db.execute('INSERT INTO chat_participants VALUES(?,?,?)',(str(i),'source',handle))
                db.execute('''INSERT INTO messages(id,source,source_rowid,text,sender,handle_id,kind,raw_hash,decoded,decode_status,timestamp_ns)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)''',(str(i),'source',i,'Hello',handle,handle,'message','hash','{}','plain',i*1000000000))
                db.execute('INSERT INTO memberships VALUES(?,?,0)',(str(i),str(i)))
            db.commit()

    def tearDown(self):
        self.temp.cleanup()

    def refresh(self, records):
        with patch('pymessage.app.read_macos_contacts',return_value=records), patch('pymessage.cli.execute',side_effect=AssertionError('must not re-import')):
            self.app.start('contacts',{})
            self.app.worker.join(timeout=2)
        self.assertFalse(self.app.worker.is_alive())
        self.assertEqual(self.app.status()['job']['state'],'complete')

    def test_labels_search_and_persistence_without_archive_writes(self):
        before = file_digest(self.app.path)
        self.refresh(RECORDS)
        chats = {chat['id']:chat for chat in self.app.chats()['chats']}
        self.assertEqual(chats['1']['name'],'Alex Rivera')
        self.assertEqual(chats['1']['participants'],['+13175550123'])
        self.assertEqual(chats['3']['name'],'Family')
        self.assertEqual(len(self.app.chats('Rivera')['chats']),2)
        self.assertEqual(len(self.app.chats('ZOË')['chats']),1)
        self.assertEqual(self.app.contact_labels()['+13175550123'],'Alex Rivera')
        self.assertEqual(App(self.args).contacts.name('+13175550123'),'Alex Rivera')
        self.assertEqual(file_digest(self.app.path),before)

    def test_refresh_renames_removes_and_keeps_cache_on_failure(self):
        self.refresh(RECORDS)
        renamed = [{**RECORDS[0],'name':'New name'}]
        self.refresh(renamed)
        self.assertEqual(self.app.contacts.name('+13175550123'),'New name')
        self.assertEqual(self.app.contacts.name('zoe@example.com'),'zoe@example.com')
        with patch('pymessage.app.read_macos_contacts',side_effect=RuntimeError('Denied')):
            self.app.start('contacts',{})
            self.app.worker.join(timeout=2)
        self.assertEqual(self.app.status()['job']['state'],'failed')
        self.assertEqual(self.app.contacts.name('+13175550123'),'New name')
        self.refresh([])
        self.assertEqual(self.app.contact_labels(),{})

    def test_demo_does_not_access_contacts(self):
        self.app.args.demo = True
        with patch('pymessage.app.read_macos_contacts',side_effect=AssertionError('must not access Contacts')):
            self.app.start('contacts',{})
            self.app.worker.join(timeout=2)
        self.assertEqual(self.app.status()['job']['state'],'failed')
        self.assertIn('demo',self.app.status()['job']['error'])


if __name__ == '__main__':
    unittest.main()
