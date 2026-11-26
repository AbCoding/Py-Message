"""Local files and tiny reaction fixtures; never read the user's attachments."""
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image
import test_reactions as fixtures
from pymessage.app import App, Handler
from pymessage.analysis import Config
from pymessage.cli import parser
from pymessage.media import image_preview


class LeaderboardMediaTests(unittest.TestCase):
    message = fixtures.ReactionTests.message
    event = fixtures.ReactionTests.event

    def setUp(self):
        fixtures.ReactionTests.setUp(self)
        self.root = Path(self.temp.name)
        self.media = self.root/'Attachments'; self.media.mkdir()
        args = parser().parse_args(['app','--archive',str(self.root/'archive.sqlite'),'--output',str(self.root/'output'),
                                    '--attachment-root',str(self.media),'--timezone','UTC'])
        self.app = App(args)
        self.target = self.message()
        self.event(self.target)
        self.other = self.message()
        self.archive.db.commit()
        self.app.leaderboard_messages.add(('chat',self.target))

    def tearDown(self):
        fixtures.ReactionTests.tearDown(self)

    def attachment(self, name='image.png', target=None, content=b'file', original=None, archived=None):
        path = original or self.media/name
        if original is None: path.write_bytes(content)
        identifier = 's:attachment:'+name
        self.archive.db.execute('INSERT INTO attachments VALUES(?,?,?,?,?,?,?,?)',
            (identifier,'s',name,str(path),1,str(archived) if archived else None,None,json.dumps({'transfer_name':name})))
        self.archive.db.execute('INSERT INTO message_attachments VALUES(?,?)',(target or self.target,identifier))
        self.archive.db.commit()
        return identifier,path

    def test_list_and_preview_and_original(self):
        buffer = io.BytesIO(); Image.new('RGB',(2000,1000),'blue').save(buffer,'PNG')
        identifier,path = self.attachment(content=buffer.getvalue())
        listing = self.app.leaderboard_attachments('chat',self.target)['attachments'][0]
        self.assertTrue(listing['available'])
        self.assertEqual(listing['kind'],'image')
        self.assertNotIn('path',listing)
        preview,mime,name = self.app.leaderboard_attachments('chat',self.target,identifier,True)
        self.assertEqual((mime,name),('image/png','image.png'))
        with Image.open(preview) as picture: self.assertEqual(picture.size,(1280,640))
        self.assertEqual(preview.stat().st_mode & 0o777,0o600)
        original,kind,_ = self.app.leaderboard_attachments('chat',self.target,identifier)
        self.assertEqual(original,path.resolve())
        self.assertEqual(kind,'application/octet-stream')
        self.assertEqual(path.read_bytes(),buffer.getvalue())

    def test_scope_relationship_and_root_checks(self):
        identifier,_ = self.attachment(target=self.other)
        with self.assertRaises(ValueError): self.app.leaderboard_attachments('chat',self.other)
        with self.assertRaises(ValueError): self.app.leaderboard_attachments('other',self.target)
        with self.assertRaises(ValueError): self.app.leaderboard_attachments('chat',self.target,identifier)
        outside = self.root/'private.txt'; outside.write_text('not an attachment root')
        rogue,_ = self.attachment('rogue.txt',original=outside)
        self.assertFalse(self.app.leaderboard_attachments('chat',self.target)['attachments'][0]['available'])
        with self.assertRaises(ValueError): self.app.leaderboard_attachments('chat',self.target,rogue)
        symlink = self.media/'symlink.txt'; symlink.symlink_to(outside)
        linked,_ = self.attachment('linked.txt',original=symlink)
        with self.assertRaises(ValueError): self.app.leaderboard_attachments('chat',self.target,linked)

    def test_missing_original_uses_archived_copy(self):
        stored = self.root/'media'/'hash'; stored.parent.mkdir(); stored.write_bytes(b'archived')
        identifier,path = self.attachment('clip.mp4',original=self.media/'missing.mp4',archived=stored)
        item = self.app.leaderboard_attachments('chat',self.target)['attachments'][0]
        self.assertTrue(item['available']); self.assertEqual(item['kind'],'video')
        self.assertEqual(self.app.leaderboard_attachments('chat',self.target,identifier)[0],stored.resolve())
        stored.unlink()
        self.assertFalse(self.app.leaderboard_attachments('chat',self.target)['attachments'][0]['available'])

    def test_unsent_and_absent_messages_do_not_expose_files(self):
        identifier,_ = self.attachment()
        self.archive.db.execute('UPDATE messages SET decoded=? WHERE id=?',
            (json.dumps({'edits':[{'status':'Edited'},{'status':'Unsent'}]}),self.target))
        self.archive.db.commit()
        self.assertEqual(self.app.leaderboard_attachments('chat',self.target)['attachments'],[])
        with self.assertRaises(ValueError): self.app.leaderboard_attachments('chat',self.target,identifier)
        self.archive.db.execute('UPDATE messages SET source_present=0 WHERE id=?',(self.target,)); self.archive.db.commit()
        with self.assertRaises(ValueError): self.app.leaderboard_attachments('chat',self.target)

    def test_invalid_and_large_images_have_download_fallback(self):
        identifier,_ = self.attachment('bad.png',content=b'not a picture')
        with self.assertRaises(ValueError): self.app.leaderboard_attachments('chat',self.target,identifier,True)
        self.assertTrue(self.app.leaderboard_attachments('chat',self.target,identifier)[0].is_file())
        large = self.media/'large.png'
        with large.open('wb') as stream: stream.truncate(33*1024*1024)
        with self.assertRaises(ValueError): image_preview(large,'image/png',self.root/'previews')

    def test_analysis_registers_cached_and_fresh_leaderboards(self):
        self.app.leaderboard_messages.clear()
        with patch('pymessage.app.render_charts'):
            first = self.app.analyze('chat',Config(charts='none'))
            self.assertIn(('chat',self.target),self.app.leaderboard_messages)
            self.app.leaderboard_messages.clear()
            second = self.app.analyze('chat',Config(charts='none'))
        self.assertFalse(first['cached']); self.assertTrue(second['cached'])
        self.assertIn(('chat',self.target),self.app.leaderboard_messages)
        self.assertNotIn(('chat',self.other),self.app.leaderboard_messages)

    def test_authenticated_routes_and_streaming_headers(self):
        identifier,path = self.attachment('file.html',content=b'<script>not inline</script>')
        self.app.origin = 'http://127.0.0.1:8765'
        handler = Handler.__new__(Handler); handler.server = SimpleNamespace(app=self.app)
        handler.send = Mock(); handler.headers = {'Host':'127.0.0.1:8765'}
        handler.path = '/api/leaderboard-attachments?chat_id=chat&message_id='+self.target
        handler.dispatch(False); self.assertEqual(handler.send.call_args.args[0],401)
        handler.headers['Authorization'] = 'Bearer '+self.app.token
        handler.dispatch(False); self.assertEqual(handler.send.call_args.args[0],200)
        handler.path = '/api/leaderboard-attachment?chat_id=chat&message_id='+self.target+'&attachment_id='+identifier
        handler.send_response = Mock(); handler.send_header = Mock(); handler.end_headers = Mock(); handler.wfile = io.BytesIO()
        handler.dispatch(False)
        self.assertEqual(handler.wfile.getvalue(),path.read_bytes())
        headers = dict(call.args for call in handler.send_header.call_args_list)
        self.assertEqual(headers['Content-Type'],'application/octet-stream')
        self.assertTrue(headers['Content-Disposition'].startswith('attachment;'))
        self.assertIn('sandbox',headers['Content-Security-Policy'])


if __name__ == '__main__':
    unittest.main()
