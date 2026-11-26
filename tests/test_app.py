"""App regression coverage; tiny fixtures and stubbed chart rendering."""
from contextlib import redirect_stdout, redirect_stderr
import http.client
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from benchmarks.synthetic import create_database
from pymessage import cli
from pymessage.app import App, make_server
from pymessage.util import exclusive_lock, file_digest


def fake_charts(job):
    # Rendering itself is covered by the existing pipeline suite.
    directory = Path(job[2])
    if job[1]['word_frequencies']:
        (directory/'wordcloud.png').write_bytes(b'fake-png')
    return []


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = create_database(self.root/'source.db',170,2)
        self.args = cli.parser().parse_args(['app','--db-path',str(self.source),
            '--output',str(self.root/'output'),'--port','0','--no-browser','--timezone','UTC'])
        self.app = App(self.args)
        self.sync()

    def tearDown(self):
        self.temp.cleanup()

    def sync(self):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.app.start('sync',{})
            self.app.worker.join(timeout=10)
        self.assertFalse(self.app.worker.is_alive())
        self.assertEqual(self.app.status()['job']['state'],'complete')

    def analyze(self, chat_id, **options):
        with patch('pymessage.app.render_charts',side_effect=fake_charts):
            self.app.start('analyze',{'chat_id':chat_id,**options})
            self.app.worker.join(timeout=10)
        self.assertFalse(self.app.worker.is_alive())
        job = self.app.status()['job']
        self.assertEqual(job['state'],'complete',job)
        return job['result']

    def test_browse_and_page_without_modifying_source(self):
        before = file_digest(self.source)
        chats = self.app.chats()['chats']
        self.assertEqual(len(chats),2)
        self.assertEqual(sum(chat['count'] for chat in chats),170)
        chat = chats[0]
        latest = self.app.messages(chat['id'])
        earlier = self.app.messages(chat['id'],80)
        self.assertEqual(len(latest['messages']),80)
        self.assertTrue(latest['has_more'])
        self.assertEqual(len(earlier['messages']),5)
        self.assertFalse(earlier['has_more'])
        self.assertFalse({m['id'] for m in latest['messages']} & {m['id'] for m in earlier['messages']})
        self.assertLess(earlier['messages'][-1]['timestamp'],latest['messages'][0]['timestamp'])
        self.assertEqual(file_digest(self.source),before)

    def test_search_addresses_and_untrusted_text(self):
        chat = self.app.chats('user1@example.invalid')['chats']
        self.assertEqual(len(chat),1)
        with sqlite3.connect(self.app.path) as db:
            db.execute('UPDATE chats SET name=? WHERE id=?',('<script>alert(1)</script>',chat[0]['id']))
        self.app.library(refresh=True)
        self.assertEqual(len(self.app.chats('<script>')['chats']),1)
        self.assertEqual(self.app.chats("' OR 1=1 --")['chats'],[])

    def test_selected_analysis_cache_and_invalidation(self):
        chat = self.app.chats('user1@example.invalid')['chats'][0]
        result = self.analyze(chat['id'])
        self.assertEqual(result['metrics']['message_count'],85)
        self.assertFalse(result['cached'])
        self.assertIn('wordcloud',result['artifacts'])
        with patch('pymessage.app.fingerprint',side_effect=AssertionError('unnecessary scan')), patch('pymessage.app.analyze',side_effect=AssertionError('unnecessary analysis')):
            cached = self.analyze(chat['id'])
        self.assertTrue(cached['cached'])
        with sqlite3.connect(self.source) as db:
            db.execute('UPDATE message SET text=? WHERE ROWID=1',('new vocabulary',))
        self.sync()
        updated = self.analyze(chat['id'])
        self.assertFalse(updated['cached'])
        self.assertIn('vocabulary',updated['metrics']['word_frequencies'])

    def test_date_filter_empty_result_and_offline_analysis(self):
        chat = self.app.chats()['chats'][0]
        self.source.unlink()
        result = self.analyze(chat['id'],start_date='2099-01-01')
        self.assertEqual(result['metrics']['message_count'],0)
        self.assertEqual(result['artifacts'],{})

    def test_validation_and_single_writer(self):
        chat = self.app.chats()['chats'][0]
        for payload in ({'group_by':'invalid'}, {'start_date':'bad'},
                        {'start_date':'2025-01-02','end_date':'2025-01-01'}, {'timezone':'Not/AZone'}):
            with self.assertRaises((ValueError,KeyError)):
                self.app.start('analyze',{'chat_id':chat['id'],**payload})
        with self.assertRaises(ValueError):
            self.app.start('analyze',{'chat_id':'unknown'})
        self.app.job = {'state':'running'}
        with self.assertRaises(RuntimeError):
            self.app.start('sync',{})
        self.app.job = {'state':'idle'}
        with exclusive_lock(self.app.path.parent/'archive.lock'):
            self.app.start('analyze',{'chat_id':chat['id']})
            self.app.worker.join(timeout=10)
        self.assertEqual(self.app.status()['job']['state'],'failed')
        self.assertIn('Another Py-Message process',self.app.status()['job']['error'])

    def test_deduplication_and_absent_messages(self):
        with sqlite3.connect(self.source) as db:
            db.execute("UPDATE message SET guid='guid-1' WHERE ROWID=3")
            db.execute('DELETE FROM message WHERE ROWID=5')
        self.sync()
        chat = self.app.chats('user1@example.invalid')['chats'][0]
        self.assertEqual(chat['count'],83)
        messages = self.app.messages(chat['id'])['messages'] + self.app.messages(chat['id'],80)['messages']
        self.assertEqual(len(messages),83)
        self.assertFalse(any(message['id'].endswith(':1') or message['id'].endswith(':5') for message in messages))

    def test_source_cannot_be_used_as_archive(self):
        self.args.archive = self.source
        with self.assertRaises(ValueError):
            App(self.args)

    def test_http_auth_origin_static_allowlist_and_jobs(self):
        server = make_server(self.args)
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        def request(path, method='GET', data=None, headers=None):
            connection = http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            try:
                connection.request(method,path,body=data,headers=headers or {})
                response = connection.getresponse()
                return response.status,dict(response.getheaders()),response.read()
            finally:
                connection.close()
        try:
            auth = {'Authorization':'Bearer '+server.app.token}
            self.assertEqual(request('/api/chats')[0],401)
            self.assertEqual(request('/api/people')[0],401)
            self.assertEqual(request('/api/people/link','POST','{}')[0],401)
            self.assertEqual(request('/api/people/unlink','POST','{}')[0],401)
            self.assertEqual(request('/api/activity','POST','{}')[0],401)
            self.assertEqual(request('/api/activity','POST','{"metric":"invalid"}',auth)[0],400)
            self.assertEqual(request('/api/people',headers=auth)[0],200)
            self.assertEqual(request('/api/person?id=missing',headers=auth)[0],400)
            self.assertEqual(request('/api/chats',headers=auth)[0],200)
            self.assertEqual(request('/api/chats',headers={**auth,'Origin':'https://example.com'})[0],403)
            self.assertEqual(request('/api/chats',headers={**auth,'Host':'attacker.invalid'})[0],403)
            self.assertEqual(request('/api/chats?offset=-1',headers=auth)[0],400)
            self.assertEqual(request('/api/analyze','POST','[]',auth)[0],400)
            self.assertEqual(request('/api/sync','POST','{}')[0],401)
            self.assertEqual(request('/api/artifact?id=../../archive.sqlite',headers=auth)[0],404)
            for path in ('/','/app.js','/style.css'):
                status,headers,body = request(path)
                self.assertEqual(status,200)
                self.assertTrue(body)
                self.assertEqual(headers['Cache-Control'],'no-store')
                self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy'])
            self.assertEqual(request('/../app.py')[0],404)
            self.assertEqual(request('/GeneratedFiles/.state/archive.sqlite')[0],404)
            self.assertEqual(request('/api/activity','POST','{"group_by":"month","metric":"total","top":5}',auth)[0],202)
            server.app.worker.join(timeout=3)
            self.assertFalse(server.app.worker.is_alive())
            self.assertEqual(server.app.status()['job']['state'],'complete')
            self.assertEqual(server.app.status()['job']['result']['total'],170)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


class AppLaunchTests(unittest.TestCase):
    def test_cli_dispatches_app_without_sync(self):
        with patch('pymessage.app.serve',return_value=0) as serve, patch('pymessage.cli.execute',side_effect=AssertionError('unexpected import')):
            self.assertEqual(cli.main(['app','--no-browser']),0)
        self.assertEqual(serve.call_args.args[0].command,'app')

    def test_missing_archive_is_an_empty_library(self):
        with tempfile.TemporaryDirectory() as directory:
            args = cli.parser().parse_args(['app','--output',directory])
            app = App(args)
            self.assertEqual(app.chats(),{'chats':[],'has_more':False})
            self.assertFalse(app.path.exists())


if __name__ == '__main__':
    unittest.main()
