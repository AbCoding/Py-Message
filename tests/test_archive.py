import base64
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import plistlib
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from benchmarks.synthetic import BASE, create_database
from pymessage import cli
from pymessage.analysis import Config, analyze, conversations
from pymessage.archive import Archive
from pymessage.decoder import native_binary
from pymessage.source import snapshot_database, utc_ns
from pymessage.text import tokens
from pymessage.util import exclusive_lock, file_digest


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.source = create_database(self.root/'source.db',12,2)
        self.output = self.root/'output'
        self.archive_path = self.output/'.state'/'archive.sqlite'

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, *options, expected=0):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            status = cli.main(['--db-path',str(self.source),'--output',str(self.output),'--charts','none','--timezone','America/Indiana/Indianapolis',*options])
        self.assertEqual(status,expected)
        return json.loads((self.output/'latest-run.json').read_text())

    def mutate(self, sql, params=()):
        with sqlite3.connect(self.source) as connection:
            connection.execute(sql,params)

    def test_complete_import_preserves_unknown_bytes_and_receipts(self):
        before = file_digest(self.source)
        summary = self.run_cli()
        self.assertEqual(file_digest(self.source),before)
        self.assertEqual(summary['total_messages'],12)
        self.assertEqual(summary['sync']['reconciliation'],{'source_message_rows':12,'archived_present_rows':12})
        with Archive(self.archive_path) as archive:
            raw = next(archive.raw(summary['sync']['source_id'],'custom_metadata'))[2]
            self.assertEqual(base64.b64decode(raw['blob']['$bytes']),b'\x00\xffpreserve me')
            receipt = archive.db.execute('SELECT * FROM receipts WHERE message_id LIKE ? ORDER BY message_id',('%:1',)).fetchone()
            self.assertNotEqual(receipt['read_raw'],receipt['delivered_raw'])
            self.assertEqual(archive.db.execute('SELECT COUNT(*) FROM parts').fetchone()[0],12)
            snapshot = Path(archive.db.execute('SELECT snapshot FROM sources').fetchone()[0])
            self.assertTrue(snapshot.exists())

    def test_unchanged_run_does_not_decode_analyze_or_render(self):
        self.run_cli()
        with patch('pymessage.decoder.DecodeCache.decode_pending',side_effect=AssertionError('decode on no-op')), patch('pymessage.reports.analyze',side_effect=AssertionError('analysis on no-op')), patch('pymessage.reports.render_charts',side_effect=AssertionError('render on no-op')):
            second = self.run_cli()
        self.assertTrue(second['sync']['snapshot_unchanged'])
        self.assertEqual(second['cache']['analysis_cached'],2)

    def test_old_mutation_reactions_and_deletion_are_reconciled(self):
        self.run_cli()
        self.mutate('UPDATE message SET text=?,date_read=date_read+999 WHERE ROWID=1',('Changed old message',))
        self.mutate('UPDATE message SET associated_message_type=2001,associated_message_guid=? WHERE ROWID=2',('p:0/guid-1',))
        self.mutate('DELETE FROM message WHERE ROWID=3')
        update = self.run_cli()
        self.assertEqual(update['sync']['changed'],2)
        self.assertEqual(update['sync']['absent'],1)
        self.assertEqual(update['total_messages'],10)
        with Archive(self.archive_path) as archive:
            self.assertGreaterEqual(archive.db.execute('SELECT COUNT(*) FROM revisions').fetchone()[0],2)
            self.assertEqual(archive.db.execute('SELECT source_present FROM messages WHERE source_rowid=3').fetchone()[0],0)
            self.assertEqual(archive.db.execute('SELECT action FROM reactions').fetchone()[0],'add')
            self.assertEqual(archive.db.execute('SELECT text FROM messages WHERE source_rowid=1').fetchone()[0],'Changed old message')

    def test_incremental_current_view_matches_clean_rebuild(self):
        self.run_cli()
        self.mutate('UPDATE message SET text=?,thread_originator_guid=? WHERE ROWID=1',('Changed text','guid-4'))
        self.mutate('DELETE FROM message WHERE ROWID=3')
        self.run_cli()
        alternate = self.root/'fresh'
        self.run_cli('--output',str(alternate))
        with Archive(self.archive_path) as incremental, Archive(alternate/'.state'/'archive.sqlite') as fresh:
            config = Config(timezone='America/Indiana/Indianapolis',charts='none')
            a = {c['name']:analyze(incremental,c,config) for c in conversations(incremental,config)}
            b = {c['name']:analyze(fresh,c,config) for c in conversations(fresh,config)}
            self.assertEqual(a,b)

    def test_report_filters_do_not_discard_source_history(self):
        summary = self.run_cli('--conversation','user1@example.invalid','--start-date','2030-01-01')
        self.assertEqual(summary['total_messages'],0)
        with Archive(self.archive_path) as archive:
            self.assertEqual(archive.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],12)

    def test_wal_snapshot_includes_uncheckpointed_rows(self):
        connection = sqlite3.connect(self.source)
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('PRAGMA wal_autocheckpoint=0')
        connection.execute('UPDATE message SET text=? WHERE ROWID=1',('Committed in WAL',))
        connection.commit()
        target = self.root/'wal-snapshot.db'
        snapshot_database(self.source,target)
        with sqlite3.connect(target) as snapshot:
            self.assertEqual(snapshot.execute('SELECT text FROM message WHERE ROWID=1').fetchone()[0],'Committed in WAL')
        connection.close()

    def test_failed_transaction_keeps_previous_archive_and_can_resume(self):
        self.run_cli()
        self.mutate('UPDATE message SET text=? WHERE ROWID=1',('new data',))
        from pymessage.source import _normalize_message
        def fail(*args,**kwargs):
            _normalize_message(*args,**kwargs)
            raise RuntimeError('simulated interruption')
        with patch('pymessage.source._normalize_message',side_effect=fail):
            self.run_cli(expected=1)
        with Archive(self.archive_path) as archive:
            self.assertNotEqual(archive.db.execute('SELECT text FROM messages WHERE source_rowid=1').fetchone()[0],'new data')
            self.assertEqual(archive.db.execute('SELECT status FROM runs ORDER BY started DESC,id DESC LIMIT 1').fetchone()[0],'failed')
        resumed = self.run_cli()
        self.assertEqual(resumed['sync']['changed'],1)

    def test_unknown_body_retained_and_strict_import_is_atomic(self):
        self.mutate('UPDATE message SET text=NULL,attributedBody=? WHERE ROWID=1',(b'bad body',))
        with patch('pymessage.decoder.native_binary',return_value=None):
            summary = self.run_cli(expected=2)
        self.assertEqual(summary['coverage']['unsupported_bodies'],1)
        with Archive(self.archive_path) as archive:
            self.assertEqual(archive.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],12)
            raw = archive.db.execute("SELECT payload FROM raw_records WHERE table_name='message' AND record_key='1'").fetchone()[0]
            self.assertIn(base64.b64encode(b'bad body').decode(),raw)
        strict_output = self.root/'strict'
        with patch('pymessage.decoder.native_binary',return_value=None):
            self.run_cli('--output',str(strict_output),'--strict',expected=1)
        with Archive(strict_output/'.state'/'archive.sqlite') as archive:
            self.assertEqual(archive.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],0)

    def test_original_media_deduplicates_and_is_available_offline(self):
        media = self.root/'picture.png'
        media.write_bytes(b'original bytes')
        with sqlite3.connect(self.source) as connection:
            connection.executemany('INSERT INTO attachment VALUES(?,?,?,?,?)',[(1,'asset-1',str(media),'image/png',14),(2,'asset-2',str(media),'image/png',14)])
            connection.executemany('INSERT INTO message_attachment_join VALUES(?,?)',[(1,1),(2,2)])
        self.run_cli('--archive-media')
        archived = list((self.output/'.state'/'media').iterdir())
        self.assertEqual(len(archived),1)
        self.assertEqual(archived[0].read_bytes(),b'original bytes')
        media.unlink()
        self.run_cli('report')
        with Archive(self.archive_path) as archive:
            self.assertEqual(archive.db.execute('SELECT COUNT(DISTINCT sha256) FROM attachments').fetchone()[0],1)

    def test_duplicate_guids_are_preserved_but_not_double_counted(self):
        self.mutate('UPDATE message SET guid=? WHERE ROWID=3',('guid-1',))
        summary = self.run_cli()
        self.assertEqual(summary['total_messages'],11)
        with Archive(self.archive_path) as archive:
            self.assertEqual(archive.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],12)

    def test_offline_export_and_doctor_do_not_access_source(self):
        self.run_cli()
        self.source.unlink()
        summary = self.run_cli('export','--format','jsonl')
        files = list(Path(summary['export']['directory']).glob('*.jsonl'))
        self.assertEqual(sum(len(path.read_text().splitlines()) for path in files),12)
        self.assertIn('Raw',json.loads(files[0].read_text().splitlines()[0]))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(['doctor','--output',str(self.output)]),0)

    def test_concurrent_run_is_rejected_without_writes(self):
        self.run_cli()
        with exclusive_lock(self.output/'.state'/'archive.lock'):
            with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(['sync','--db-path',str(self.source),'--output',str(self.output)]),1)

    def test_bad_timestamps_remain_archived_and_visible_as_partial(self):
        self.mutate('UPDATE message SET date=NULL WHERE ROWID=1')
        summary = self.run_cli(expected=2)
        self.assertEqual(summary['total_messages'],12)
        self.assertEqual(summary['coverage']['invalid_timestamps'],1)

    def test_timezone_filters_handle_dst_boundaries(self):
        # 2024-03-10 local day in Indianapolis is 23 hours.
        lower,upper = Config(timezone='America/Indiana/Indianapolis',start_date='2024-03-10',end_date='2024-03-11').bounds()
        self.assertEqual((upper-lower)//10**9,23*3600)
        self.assertEqual(utc_ns(BASE)-utc_ns(BASE-1),1)
        self.assertIsNone(utc_ns(0,receipt=True))

    def test_unicode_preserves_accents_cjk_and_word_boundaries(self):
        self.assertEqual(list(tokens('café 你好 🙂 well-being don’t stop')),['café','你','好','well','being',"don't",'stop'])

    @unittest.skipUnless(native_binary(), 'native decoder requires macOS')
    def test_actual_native_binary_decodes_upstream_fixtures(self):
        cases = json.loads((Path(__file__).parent/'fixtures'/'native_cases.json').read_text())['cases']
        with sqlite3.connect(self.source) as connection:
            for i,case in enumerate(cases,1):
                raw = case['raw']
                body = base64.b64decode(raw['attributedBody']['$bytes'])
                connection.execute('UPDATE message SET text=NULL,attributedBody=? WHERE ROWID=?',(body,i))
        self.run_cli()
        with Archive(self.archive_path) as archive:
            for i,case in enumerate(cases,1):
                row = archive.db.execute('SELECT text,decode_status FROM messages WHERE source_rowid=?',(i,)).fetchone()
                self.assertEqual(row['text'],case['expected_text'])
                self.assertEqual(row['decode_status'],'decoded')
        second = self.run_cli()
        self.assertTrue(second['sync']['snapshot_unchanged'])

    @unittest.skipUnless(native_binary(), 'native decoder requires macOS')
    def test_native_edit_history_and_precise_source_plist_are_retained(self):
        fixture = Path(__file__).parent/'fixtures'/'edited.plist'
        self.mutate('UPDATE message SET text=NULL,message_summary_info=?,date_edited=? WHERE ROWID=1',(fixture.read_bytes(),BASE))
        self.run_cli()
        with Archive(self.archive_path) as archive:
            row = archive.db.execute('SELECT id,text,decoded FROM messages WHERE source_rowid=1').fetchone()
            self.assertEqual(row['text'],'Edited message')
            events = json.loads(row['decoded'])['edits'][0]['events']
            self.assertEqual([event['text'] for event in events],['First message  ','Edit 1','Edit 2','Edited message'])
            payload = json.loads(archive.db.execute('SELECT data FROM structured_payloads WHERE message_id=?',(row['id'],)).fetchone()[0])
            self.assertIsInstance(payload['ec']['0'][0]['d'],float)
            self.assertEqual(payload['ec']['0'][0]['d'],plistlib.loads(fixture.read_bytes())['ec']['0'][0]['d'])

    @unittest.skipUnless(native_binary(), 'native decoder requires macOS')
    def test_native_decode_checkpoint_survives_canonical_rollback(self):
        case = json.loads((Path(__file__).parent/'fixtures'/'native_cases.json').read_text())['cases'][1]
        self.mutate('UPDATE message SET text=NULL,attributedBody=? WHERE ROWID=1',(base64.b64decode(case['raw']['attributedBody']['$bytes']),))
        with patch('pymessage.source._normalize_message',side_effect=RuntimeError('stop after decode checkpoint')):
            self.run_cli(expected=1)
        with patch('pymessage.decoder.subprocess.Popen',side_effect=AssertionError('completed body was decoded again')):
            self.run_cli()

    def test_partial_coverage_remains_partial_on_noop(self):
        self.mutate('UPDATE message SET date=NULL WHERE ROWID=1')
        self.run_cli(expected=2)
        summary = self.run_cli(expected=2)
        self.assertTrue(summary['sync']['snapshot_unchanged'])

    def test_fixed_bad_record_clears_unresolved_error(self):
        self.mutate('UPDATE message SET date=NULL WHERE ROWID=1')
        self.run_cli(expected=2)
        self.mutate('UPDATE message SET date=? WHERE ROWID=1',(BASE,))
        summary = self.run_cli()
        self.assertEqual(summary['coverage']['unresolved_import_errors'],0)

    def test_selective_changes_reuse_other_conversations(self):
        self.run_cli()
        self.mutate('UPDATE message SET text=? WHERE ROWID=1',('new text',))
        summary = self.run_cli()
        self.assertEqual(summary['cache']['analyzed'],1)
        self.assertEqual(summary['cache']['analysis_cached'],1)

    def test_report_and_csv_render_in_bounded_worker_processes(self):
        summary = self.run_cli('--charts','all','--workers','2','--export-csv')
        self.assertEqual(summary['cache']['rendered'],2)
        stats = list((self.output/'reports').rglob('statistics.json'))
        self.assertEqual(len(stats),2)
        for stat in stats:
            self.assertTrue(stat.with_name('messages.csv').exists())
            self.assertTrue(stat.with_name('timeline_week.png').exists())
        original_mtimes = {path:path.stat().st_mtime_ns for path in (self.output/'reports').rglob('*.png')}
        second = self.run_cli('--charts','all','--workers','2','--export-csv')
        self.assertEqual(second['cache']['rendered'],0)
        self.assertEqual(original_mtimes,{path:path.stat().st_mtime_ns for path in original_mtimes})

    def test_html_sources_and_errors_are_preserved_without_false_success(self):
        html = self.root/'html'
        html.mkdir()
        original = b'<div class="message">broken record</div>'
        (html/'bad.html').write_bytes(original)
        with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            for _ in range(2):
                self.assertEqual(cli.main(['--html-dir',str(html),'--output',str(self.output),'--charts','none']),2)
        snapshots = list((self.output/'.state'/'html-snapshots').rglob('*.html'))
        self.assertEqual(len(snapshots),1)
        self.assertEqual(snapshots[0].read_bytes(),original)

    def test_migration_from_initial_archive_schema(self):
        from pymessage.archive import SCHEMA,SCHEMA_VERSION
        path = self.root/'old.sqlite'
        schema = SCHEMA.replace(' ,resolved INTEGER NOT NULL DEFAULT 0','')
        schema = '\n'.join(line for line in schema.splitlines() if not line.startswith('CREATE TABLE message_statistics') and not line.startswith('CREATE TABLE structured_payloads'))
        with sqlite3.connect(path) as connection:
            connection.executescript(schema+'PRAGMA user_version=1;')
        with Archive(path) as archive:
            self.assertEqual(archive.db.execute('PRAGMA user_version').fetchone()[0],SCHEMA_VERSION)
            self.assertIn('resolved',[row[1] for row in archive.db.execute('PRAGMA table_info(import_errors)')])


    def test_archive_path_cannot_modify_source_or_another_database(self):
        before = file_digest(self.source)
        with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            status = cli.main(['--db-path',str(self.source),'--archive',str(self.source),'--output',str(self.output)])
        self.assertEqual(status,1)
        self.assertEqual(file_digest(self.source),before)
        with self.assertRaisesRegex(RuntimeError,'another database'):
            Archive(self.source)
        self.assertEqual(file_digest(self.source),before)

    def test_absent_messages_keep_historical_chat_and_attachment_links(self):
        media = self.root/'asset.txt'; media.write_text('asset')
        with sqlite3.connect(self.source) as db:
            db.execute('INSERT INTO attachment VALUES(1,?,?,?,?)',('asset',str(media),'text/plain',5))
            db.execute('INSERT INTO message_attachment_join VALUES(1,1)')
        self.run_cli()
        with sqlite3.connect(self.source) as db:
            db.execute('DELETE FROM message WHERE ROWID=1')
            db.execute('DELETE FROM chat_message_join WHERE message_id=1')
            db.execute('DELETE FROM message_attachment_join WHERE message_id=1')
        self.run_cli()
        with Archive(self.archive_path) as archive:
            message = archive.db.execute('SELECT id FROM messages WHERE source_rowid=1').fetchone()[0]
            self.assertEqual(archive.db.execute('SELECT COUNT(*) FROM memberships WHERE message_id=?',(message,)).fetchone()[0],1)
            self.assertEqual(archive.db.execute('SELECT COUNT(*) FROM message_attachments WHERE message_id=?',(message,)).fetchone()[0],1)
        history = self.run_cli('report','--include-absent')
        self.assertEqual(history['total_messages'],12)

    def test_media_copies_are_reused_and_doctor_verifies_hashes(self):
        media = self.root/'asset.txt'; media.write_text('asset')
        with sqlite3.connect(self.source) as db:
            db.execute('INSERT INTO attachment VALUES(1,?,?,?,?)',('asset',str(media),'text/plain',5))
            db.execute('INSERT INTO message_attachment_join VALUES(1,1)')
        self.run_cli('--archive-media')
        with patch('pymessage.source._copy_original',side_effect=AssertionError('unchanged media copied twice')):
            summary = self.run_cli('--archive-media')
        self.assertEqual(summary['cache']['analyzed'],0)
        archived = next((self.output/'.state'/'media').iterdir())
        archived.write_bytes(b'bad')
        with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(['doctor','--output',str(self.output),'--verify-media']),1)


    def test_attachment_metrics_respect_the_report_date_window(self):
        from datetime import datetime,timezone
        from pymessage.source import APPLE_EPOCH_SECONDS
        stamp=int(datetime(2024,1,1,tzinfo=timezone.utc).timestamp()-APPLE_EPOCH_SECONDS)*10**9
        with sqlite3.connect(self.source) as db:
            db.execute('UPDATE message SET date=? WHERE ROWID=1',(stamp,))
            db.execute("INSERT INTO attachment VALUES(1,'asset','/missing-asset','image/png',100)")
            db.execute('INSERT INTO message_attachment_join VALUES(1,1)')
        self.run_cli()
        with Archive(self.archive_path) as archive:
            config=Config(timezone='UTC',start_date='2023-01-01',end_date='2023-12-31',conversation='user1')
            chat=next(conversations(archive,config))
            self.assertEqual(analyze(archive,chat,config)['attachments'],0)
            config=Config(timezone='UTC',start_date='2024-01-01',end_date='2024-01-02',conversation='user1')
            chat=next(conversations(archive,config))
            self.assertEqual(analyze(archive,chat,config)['attachments'],1)


    def test_reaction_targets_and_sticker_events_are_structured(self):
        self.mutate('UPDATE message SET associated_message_type=1000,associated_message_guid=? WHERE ROWID=1',('p:2/guid-2',))
        summary=self.run_cli()
        self.assertEqual(summary['total_messages'],11)
        with Archive(self.archive_path) as archive:
            row=archive.db.execute('SELECT target_guid,type_code FROM reactions').fetchone()
            self.assertEqual(tuple(row),('guid-2',1000))


    def test_legacy_patterns_module_is_safe_to_import(self):
        import importlib
        stream = io.StringIO()
        with redirect_stdout(stream):
            import TextingPatterns
            importlib.reload(TextingPatterns)
        self.assertEqual(stream.getvalue(),'')


if __name__=='__main__':
    unittest.main()
