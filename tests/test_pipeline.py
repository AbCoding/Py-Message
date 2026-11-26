import csv
import hashlib
import io
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import timedelta
from unittest.mock import patch

import MessageExporter as exporter
import MessageFileToDatabase as converter
import main


FIXTURE = Path(__file__).parent / 'fixtures' / 'conversation.html'


def create_database(path):
    with sqlite3.connect(path) as connection:
        for table in ('message', 'chat', 'handle'):
            connection.execute(f'CREATE TABLE {table} (id INTEGER)')


class ImportTests(unittest.TestCase):
    def test_replies_multipart_edits_and_attachments(self):
        messages = converter.ExtractAllMessages(FIXTURE)
        rows = [converter.MessageToDataEntry(message) for message in messages]
        self.assertEqual(len(rows), 5)  # A duplicate reply is excluded.
        self.assertEqual(rows[0]['Content'], 'Hello world!\nSecond part')
        self.assertEqual(rows[0]['ReadTime'], timedelta(seconds=63))
        self.assertEqual(rows[1]['Sender'], 'Test Contact')
        self.assertEqual(rows[1]['Content'], 'Reply text')
        self.assertIsNone(rows[2]['Content'])  # No reply/attachment text leakage.
        self.assertEqual(rows[3]['Content'], 'Final text')
        self.assertEqual(rows[4]['Content'], '🙂')

    def test_csv_is_compatible_with_existing_analysis(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'messages.csv'
            self.assertEqual(converter.WriteToCSV(FIXTURE, output), 5)
            with output.open(newline='') as source:
                rows = list(csv.DictReader(source))
            self.assertEqual(rows[0]['Timestamp'], '2022-07-17 17:04:04')
            self.assertEqual(rows[0]['ReadTime'], '0:01:03')
            self.assertEqual(rows[2]['Content'], '')

    def test_singular_and_plural_read_receipts(self):
        self.assertEqual(converter.extract_timedelta_from_ReadTime('1 day, 2 hours, 1 minute, 1 second'), timedelta(days=1, hours=2, minutes=1, seconds=1))

    def test_line_breaks_and_translations_do_not_merge_or_duplicate_words(self):
        from bs4 import BeautifulSoup
        message = BeautifulSoup('<div class="message"><span class="sender">Me</span><span class="timestamp">Jul 17, 2022 5:04:04 PM</span><span class="bubble">Hello<br>world</span><div class="translated"><span class="bubble">Translated copy</span></div></div>', 'html.parser').div
        self.assertEqual(converter.MessageToDataEntry(message)['Content'], 'Hello\nworld')


class ExporterTests(unittest.TestCase):
    def test_missing_database_is_not_created(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'chat.db'
            with self.assertRaisesRegex(exporter.ExportError, 'No Messages database'):
                exporter.validate_database(path)
            self.assertFalse(path.exists())

    def test_wrong_database_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'other.db'
            with sqlite3.connect(path) as connection:
                connection.execute('CREATE TABLE unrelated (id INTEGER)')
            with self.assertRaisesRegex(exporter.ExportError, 'required Messages tables'):
                exporter.validate_database(path)

    def test_permission_error_explains_full_disk_access(self):
        with patch.object(Path, 'open', side_effect=PermissionError):
            with self.assertRaisesRegex(exporter.ExportError, 'Full Disk Access'):
                exporter.validate_database('/fake/chat.db')

    def test_database_validation_does_not_change_source(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'chat.db'
            create_database(path)
            before = path.read_bytes()
            self.assertEqual(exporter.validate_database(path), path.resolve())
            self.assertEqual(path.read_bytes(), before)

    @patch('MessageExporter.platform.system', return_value='Darwin')
    @patch('MessageExporter.platform.machine', return_value='arm64')
    def test_download_is_verified_cached_and_repairs_corruption(self, *_):
        binary = b'test binary'
        expected = hashlib.sha256(binary).hexdigest()
        def download(command, **kwargs):
            Path(command[command.index('--output') + 1]).write_bytes(binary)
            return subprocess.CompletedProcess(command, 0, '', '')
        with tempfile.TemporaryDirectory() as temp, patch.dict(exporter.ASSETS, {'arm64': ('test-asset', expected)}), patch('MessageExporter.subprocess.run', side_effect=download) as run:
            path = exporter.ensure_exporter(temp)
            self.assertEqual(path.read_bytes(), binary)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(exporter.ensure_exporter(temp), path)
            self.assertEqual(run.call_count, 1)
            path.write_bytes(b'corrupted')
            self.assertEqual(exporter.ensure_exporter(temp).read_bytes(), binary)
            self.assertEqual(run.call_count, 2)

    @patch('MessageExporter.platform.system', return_value='Darwin')
    @patch('MessageExporter.platform.machine', return_value='arm64')
    def test_checksum_failure_never_installs_binary(self, *_):
        def download(command, **kwargs):
            Path(command[command.index('--output') + 1]).write_bytes(b'invalid')
            return subprocess.CompletedProcess(command, 0, '', '')
        with tempfile.TemporaryDirectory() as temp, patch('MessageExporter.subprocess.run', side_effect=download):
            with self.assertRaisesRegex(exporter.ExportError, 'checksum mismatch'):
                exporter.ensure_exporter(temp)
            self.assertFalse(any(path.is_file() for path in Path(temp).rglob('*')))

    def test_export_failure_stops_analysis(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'chat.db'
            create_database(path)
            with patch('MessageExporter.ensure_exporter', return_value=Path('/fake/exporter')), patch('MessageExporter.subprocess.run', return_value=subprocess.CompletedProcess([], 7)):
                with self.assertRaisesRegex(exporter.ExportError, 'analysis was not run'):
                    exporter.export_messages(path, Path(temp) / 'exports')

    def test_export_passes_paths_and_filters_as_separate_arguments(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'chat db.sqlite'
            create_database(path)
            output = Path(temp) / 'exports'
            def export(command):
                output.mkdir()
                shutil.copy(FIXTURE, output / 'conversation.html')
                return subprocess.CompletedProcess(command, 0)
            with patch('MessageExporter.ensure_exporter', return_value=Path('/fake/exporter')), patch('MessageExporter.subprocess.run', side_effect=export) as run:
                files = exporter.export_messages(path, output, conversation='test@example.com', start_date='2022-07-01', end_date='2022-08-01')
            self.assertEqual(len(files), 1)
            command = run.call_args.args[0]
            self.assertIn(str(path.resolve()), command)
            self.assertEqual(command[command.index('--conversation-filter') + 1], 'test@example.com')
            self.assertEqual(command[command.index('--copy-method') + 1], 'disabled')
            self.assertIn('2022-08-01', command)


class PipelineTests(unittest.TestCase):
    def test_html_pipeline_saves_all_artifacts_and_never_calls_exporter(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'html'
            source.mkdir()
            shutil.copy(FIXTURE, source / 'conversation.html')
            args = main.build_parser().parse_args(['--html-dir', str(source), '--output', str(Path(temp) / 'results'), '--separate-by-sender', '--group-by', 'all'])
            with patch('MessageExporter.export_messages') as export:
                run_dir = main.run_pipeline(args)
                export.assert_not_called()
            summary = json.loads((run_dir / 'summary.json').read_text())
            self.assertEqual(summary['total_messages'], 5)
            self.assertFalse(summary['errors'])
            for suffix in ('.csv', '_word_frequencies.csv', '_wordcloud.png', '_timeline_week.png', '_timeline_day.png'):
                self.assertTrue((run_dir / 'analysis' / ('conversation' + suffix)).is_file())
            import pandas as pd
            frequencies = pd.read_csv(run_dir / 'analysis' / 'conversation_word_frequencies.csv')
            self.assertEqual(frequencies.set_index('Word').loc['reply', 'Frequency'], 1)
            self.assertNotIn('outdated', frequencies['Word'].tolist())

    def test_database_pipeline_exports_automatically_before_analysis(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'chat.db'
            create_database(path)
            def export(source, output, **kwargs):
                output.mkdir()
                target = output / 'conversation.html'
                shutil.copy(FIXTURE, target)
                return [target]
            args = main.build_parser().parse_args(['--db-path', str(path), '--output', str(Path(temp) / 'results')])
            with patch('MessageExporter.export_messages', side_effect=export) as export_mock:
                run_dir = main.run_pipeline(args)
            export_mock.assert_called_once()
            self.assertEqual(json.loads((run_dir / 'summary.json').read_text())['total_messages'], 5)

    def test_attachment_only_conversation_skips_cloud_but_keeps_timeline(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'html'
            source.mkdir()
            source.joinpath('attachments.html').write_text('<div class="message"><span class="sender">Me</span><span class="timestamp">Jul 17, 2022 5:04:04 PM</span></div>')
            args = main.build_parser().parse_args(['--html-dir', str(source), '--output', str(Path(temp) / 'results')])
            run_dir = main.run_pipeline(args)
            summary = json.loads((run_dir / 'summary.json').read_text())
            self.assertEqual(summary['total_messages'], 1)
            self.assertFalse(summary['conversations'][0]['wordcloud'])
            self.assertTrue((run_dir / 'analysis' / 'attachments_timeline.png').exists())

    def test_invalid_export_is_reported_as_failure(self):
        with tempfile.TemporaryDirectory() as temp, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            source = Path(temp) / 'html'
            source.mkdir()
            source.joinpath('broken.html').write_text('<div class="message">missing metadata</div>')
            status = main.main(['--html-dir', str(source), '--output', str(Path(temp) / 'results')])
            self.assertEqual(status, 2)  # Quarantined record; original HTML retained.
            summaries = list((Path(temp) / 'results').rglob('summary.json'))
            self.assertEqual(len(json.loads(summaries[0].read_text())['errors']), 1)

    def test_blacklist_filters_without_a_dictionary(self):
        import FrequencyTableGenerator
        import pandas as pd
        frame = pd.DataFrame({'Word': ['hello', 'world'], 'Frequency': [2, 1]})
        filtered = FrequencyTableGenerator.filter_words_from_dataframe(frame, BlacklistWords=['HELLO'])
        self.assertEqual(filtered['Word'].tolist(), ['world'])


if __name__ == '__main__':
    unittest.main()
