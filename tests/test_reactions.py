"""Reaction totals from small archive fixtures; no import or chart rendering."""
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from pymessage.analysis import Config, analyze, fingerprint
from pymessage.archive import Archive
from pymessage.source import associated_target

BASE = int(datetime(2024,1,1,tzinfo=timezone.utc).timestamp())*10**9
DAY = 86400*10**9


class ReactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.archive = Archive(Path(self.temp.name)/'archive.sqlite')
        db = self.archive.db
        db.execute("INSERT INTO sources(id,path,kind) VALUES('s','fixture','sqlite')")
        db.execute("INSERT INTO chats VALUES('chat','s','1','chat-guid','Group','{}')")
        db.execute("INSERT INTO chats VALUES('other','s','2','other-guid','Other','{}')")
        for sender in ('a','b','c'):
            db.execute('INSERT INTO participants VALUES(?,?,?,?)',('s',sender,sender+'@example.invalid','{}'))
            db.execute('INSERT INTO chat_participants VALUES(?,?,?)',('chat','s',sender))
        self.chat = dict(db.execute("SELECT * FROM chats WHERE id='chat'").fetchone())
        self.index = 0

    def tearDown(self):
        self.archive.close()
        self.temp.cleanup()

    def message(self, sender='a', guid=None, kind='message', stamp='auto', associated=None, present=1, chat='chat', source='s'):
        self.index += 1
        key = source+':'+str(self.index)
        if stamp=='auto':
            stamp = BASE+self.index*60*10**9
        self.archive.db.execute('''INSERT INTO messages(id,source,source_rowid,guid,timestamp_ns,text,sender,handle_id,
            is_from_me,kind,associated_guid,raw_hash,decoded,decode_status,source_present)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (key,source,self.index,guid or key,stamp,'Hello',sender or 'Unknown',sender,1 if sender=='Me' else 0,
             kind,associated,'hash-'+key,'{}','plain',present))
        self.archive.db.execute('INSERT INTO memberships VALUES(?,?,0)',(chat,key))
        return key

    def event(self, target, code=2000, sender='b', part=0, emoji=None, associated=None, **options):
        associated = associated or f'p:{part}/{target}'
        key = self.message(sender=sender,kind='reaction',associated=associated,**options)
        guid,_ = associated_target(associated)
        self.archive.db.execute('INSERT INTO reactions VALUES(?,?,?,?,?)',
            (key,guid,'remove' if code>=3000 else 'add',code,emoji))
        return key

    def result(self, **options):
        return analyze(self.archive,self.chat,Config(charts='none',**options))['reactions_received']

    def test_counts_received_by_author_not_reactor(self):
        target = self.message()
        self.event(target,sender='b')
        self.event(target,sender='c')
        self.event(target,2004,sender='Me')
        mine = self.message(sender='Me')
        self.event(mine,2003,sender='a')
        result = self.result()
        self.assertEqual(result['total'],4)
        self.assertEqual(result['by_sender']['a@example.invalid'],{'total':3,'counts':{'heart':2,'emphasis':1}})
        self.assertEqual(result['by_sender']['Me']['counts'],{'laugh':1})
        self.assertNotIn('b@example.invalid',result['by_sender'])

    def test_standard_types_and_zero_reactions(self):
        self.message(sender='c')
        for code in range(2000,2006):
            self.event(self.message(),code)
        result = self.result()
        self.assertEqual(result['by_sender']['a@example.invalid']['counts'],dict.fromkeys(['heart','like','dislike','laugh','emphasis','question'],1))
        self.assertEqual(result['by_sender']['c@example.invalid']['total'],0)

    def test_removal_replacement_and_late_old_removal(self):
        target = self.message()
        self.event(target,2000)
        self.event(target,2001)
        self.event(target,3000)  # Old-type removal must not erase the new like.
        self.event(target,2001)  # Repeated same-type add is still one tapback.
        self.event(target,2000,sender='c')
        self.event(target,3000,sender='c')
        self.assertEqual(self.result()['by_sender']['a@example.invalid']['counts'],{'like':1})
        self.event(target,3001)
        self.assertEqual(self.result()['total'],0)

    def test_duplicate_events_and_targets_count_once(self):
        self.message(sender='a',guid='target')
        self.message(sender='c',guid='target')
        self.event('target',guid='event')
        self.event('target',guid='event')
        result = self.result()
        self.assertEqual(result['total'],1)
        self.assertEqual(result['by_sender']['c@example.invalid']['counts'],{'heart':1})

    def test_parts_and_balloon_targets(self):
        target = self.message()
        self.event(target,2000,part=0)
        self.event(target,2004,part=1)
        self.event(target,2001,associated='bp:'+target)
        self.assertEqual(self.result()['by_sender']['a@example.invalid']['counts'],{'like':1,'emphasis':1})

    def test_custom_emoji_and_sticker_tapbacks(self):
        target = self.message()
        self.event(target,2006,emoji='❤️')
        self.event(target,3006,emoji='❤')
        self.assertEqual(self.result()['total'],0)
        self.event(target,2006,emoji='🔥')
        self.event(target,2007,sender='c')
        self.event(target,1000,sender='a')
        result = self.result()
        self.assertEqual(result['by_sender']['a@example.invalid']['counts'],{'emoji:🔥':1,'sticker':1})
        self.assertEqual(result['excluded_sticker_events'],1)
        self.event(target,3007,sender='c')
        self.assertEqual(self.result()['total'],1)

    def test_absent_events_and_targets(self):
        target = self.message()
        self.event(target)
        self.event(target,3000,present=0)
        self.assertEqual(self.result()['total'],1)
        self.assertEqual(self.result(include_absent=True)['total'],0)
        self.archive.db.execute('UPDATE messages SET source_present=0 WHERE id=?',(target,))
        self.assertEqual(self.result()['total'],0)

    def test_dates_select_messages_and_use_later_reaction_state(self):
        target = self.message(stamp=BASE)
        self.event(target,stamp=BASE+2*DAY)
        self.assertEqual(self.result(start_date='2024-01-01',end_date='2024-01-02')['total'],1)
        self.assertEqual(self.result(start_date='2024-01-02')['total'],0)
        self.event(target,3000,stamp=BASE+3*DAY)
        self.assertEqual(self.result(start_date='2024-01-01',end_date='2024-01-02')['total'],0)

    def test_unresolved_events_are_reported(self):
        target = self.message()
        self.event('missing')
        self.event(target,sender=None)
        self.event(target,stamp=None)
        self.event(target,2999)
        self.event(target,associated='p:invalid/'+target)
        result = self.result()
        self.assertEqual(result['total'],0)
        self.assertEqual(result['unresolved_events'],5)

    def test_targets_must_belong_to_same_conversation_and_source(self):
        target = self.message(chat='other')
        self.event(target)
        self.message(guid='foreign',source='different')
        self.event('foreign')
        result = self.result()
        self.assertEqual(result['total'],0)
        self.assertEqual(result['unresolved_events'],2)

    def test_filtered_cache_depends_on_later_reactions(self):
        target = self.message(stamp=BASE)
        config = Config(start_date='2024-01-01',end_date='2024-01-02')
        before = fingerprint(self.archive,self.chat,config)
        self.event(target,stamp=BASE+2*DAY)
        self.assertNotEqual(fingerprint(self.archive,self.chat,config),before)

    def test_html_marks_reactions_unavailable(self):
        self.archive.db.execute("UPDATE sources SET kind='html'")
        self.assertFalse(self.result()['available'])


if __name__ == '__main__':
    unittest.main()
