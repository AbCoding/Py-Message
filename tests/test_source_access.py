"""Fast access-error checks; no personal data, imports, or rendering."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from pymessage.source import snapshot_database


class SourceAccessTests(unittest.TestCase):
    def test_permission_error_explains_server_permission_and_restart(self):
        with patch.object(Path,'open',side_effect=PermissionError(1,'Operation not permitted')):
            with self.assertRaises(RuntimeError) as caught:
                snapshot_database('/private/tmp/blocked-chat.db','/private/tmp/snapshot.db')
        message = str(caught.exception)
        self.assertIn('macOS denied access',message)
        self.assertIn('Full Disk Access',message)
        self.assertIn('Fully quit and reopen',message)
        self.assertIn('browser does not grant access',message)

    def test_missing_source_keeps_specific_error(self):
        with patch.object(Path,'open',side_effect=FileNotFoundError()):
            with self.assertRaisesRegex(RuntimeError,'No Messages database at'):
                snapshot_database('/private/tmp/missing-chat.db','/private/tmp/snapshot.db')

    def test_output_permission_error_does_not_blame_source_access(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/'source.db'
            with sqlite3.connect(source) as db:
                db.execute('CREATE TABLE message (text TEXT)')
            @contextmanager
            def denied_output(destination):
                raise PermissionError('output folder denied')
                yield  # pragma: no cover
            with patch('pymessage.source.atomic_output',denied_output):
                with self.assertRaises(RuntimeError) as caught:
                    snapshot_database(source,Path(directory)/'snapshot.db')
            message = str(caught.exception)
            self.assertIn('source opened successfully',message)
            self.assertIn('--output',message)
            self.assertNotIn('Full Disk Access',message)


if __name__ == '__main__':
    unittest.main()
