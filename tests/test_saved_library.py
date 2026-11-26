"""App reloads use a persistent list; source refresh remains explicit."""
from pathlib import Path
from unittest.mock import patch
import unittest

import test_people as fixtures
from pymessage.app import App
from pymessage.contacts import ContactBook
from pymessage.util import file_digest


class SavedLibraryTests(unittest.TestCase):
    add = fixtures.PeopleTests.add
    matched = fixtures.PeopleTests.matched

    def setUp(self):
        fixtures.PeopleTests.setUp(self)
        self.matched()

    def tearDown(self):
        fixtures.PeopleTests.tearDown(self)

    def test_restart_and_reload_need_no_database_connection(self):
        original = self.app.chats()
        with patch('sqlite3.connect',side_effect=AssertionError('database opened during startup')), \
             patch('pymessage.cli.execute',side_effect=AssertionError('unexpected source refresh')):
            restarted = App(self.args)
            self.assertEqual(restarted.chats(),original)
            self.assertTrue(restarted.contact_labels())
            restarted.status()
            self.assertEqual(restarted.chats(),original)
            self.assertTrue(restarted.chats('Alex')['chats'])

    def test_startup_does_not_probe_source_permissions(self):
        self.app.library()
        original = Path.exists
        source = Path.home()/'Library/Messages/chat.db'
        def exists(path):
            if path==source:
                raise AssertionError('source was probed')
            return original(path)
        with patch.object(Path,'exists',exists), patch.object(Path,'samefile',side_effect=AssertionError('source inode checked')):
            restarted = App(self.args)
            self.assertTrue(restarted.chats()['chats'])

    def test_archive_changes_wait_for_explicit_refresh(self):
        first = self.app.chats(chat_id='phone')['chats'][0]
        self.assertEqual(first['count'],2)
        self.add('phone','a',guid='new message')
        self.db.commit()
        restarted = App(self.args)
        self.assertEqual(restarted.chats(chat_id='phone')['chats'][0]['count'],2)
        restarted.library(refresh=True)
        self.assertEqual(restarted.chats(chat_id='phone')['chats'][0]['count'],3)
        self.assertEqual(App(self.args).chats(chat_id='phone')['chats'][0]['count'],3)

    def test_contact_names_refresh_without_recounting_messages(self):
        self.app.library()
        self.app.contacts = ContactBook.from_records([{**fixtures.CONTACTS[0],'name':'Renamed contact'}])
        with patch.object(self.app,'read',side_effect=AssertionError('archive reopened')):
            self.assertEqual(self.app.contact_labels()['+13175550100'],'Renamed contact')
            self.assertTrue(self.app.chats('Renamed')['chats'])

    def test_first_index_build_is_local_and_does_not_write_archive(self):
        self.writer.close()
        before = file_digest(self.app.path)
        with patch('pymessage.source.snapshot_database',side_effect=AssertionError('source acquisition')):
            self.assertTrue(self.app.chats()['chats'])
        self.assertTrue(self.app.library_path.is_file())
        self.assertEqual(self.app.library_path.stat().st_mode & 0o777,0o600)
        self.assertEqual(file_digest(self.app.path),before)

    def test_failed_load_keeps_saved_conversations(self):
        original = self.app.chats()
        saved = file_digest(self.app.library_path)
        with patch('pymessage.cli.execute',side_effect=RuntimeError('Source unavailable')):
            self.app.start('sync',{})
            self.app.worker.join(timeout=2)
        self.assertEqual(self.app.status()['job']['state'],'failed')
        self.assertEqual(file_digest(self.app.library_path),saved)
        self.assertEqual(self.app.chats(),original)

    def test_missing_or_invalid_index_bootstraps_once(self):
        self.app.library_path.write_text('invalid json')
        original = self.app.chats()
        with patch.object(self.app,'read',side_effect=AssertionError('repeated rebuild')):
            self.assertEqual(self.app.chats(),original)


if __name__ == '__main__':
    unittest.main()
