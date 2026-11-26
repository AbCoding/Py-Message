"""Tiny fictional text fixtures only; no real-message imports or model runs."""
from collections import Counter
from datetime import datetime, timezone
import json
import io
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import test_people as fixtures
from pymessage.analysis import Config
from pymessage.contacts import ContactBook
from pymessage.app import Handler
from pymessage.semantic import Aggregate, NameMatcher, distinctive_words, tone_cues, words
from pymessage import semantic
from pymessage.util import digest
from pymessage.text import TOKENIZER_VERSION


class SemanticTests(unittest.TestCase):
    add = fixtures.PeopleTests.add
    person = fixtures.PeopleTests.person
    matched = fixtures.PeopleTests.matched

    def setUp(self):
        fixtures.PeopleTests.setUp(self)
        self.matched()
        self.db.execute('UPDATE messages SET source_present=0')
        self.revision = 0
        self.commit()

    def tearDown(self):
        fixtures.PeopleTests.tearDown(self)

    def commit(self):
        self.revision += 1
        self.db.execute('UPDATE sources SET last_success=?',(str(self.revision),))
        self.db.commit()

    def message(self, text, day='2024-01-02', chat='phone', sender='Me', **kwargs):
        identifier = self.add(chat,sender,**kwargs)
        stamp = int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp())*10**9
        self.db.execute('UPDATE messages SET text=?,timestamp_ns=? WHERE id=?',(text,stamp,identifier))
        return identifier

    def report(self, person_ids=None, focus='', names=None, **kwargs):
        return self.app.semantic(Config(group_by=kwargs.pop('group_by','month'),**kwargs),person_ids or [],focus,names or [])

    def test_sent_only_combines_aliases_and_excludes_duplicates(self):
        first = self.message('coffee coffee coding',guid='shared')
        self.db.execute("INSERT INTO memberships VALUES('email',?,0)",(first,))
        self.message('coffee coffee coding',chat='email',guid='shared')
        self.message('tea tea lunch',chat='email')
        self.message('incoming secret',sender='a')
        self.message('group secret',chat='group')
        self.message('reaction secret',kind='reaction')
        self.message('absent secret',present=0)
        self.commit()
        result = self.report()
        self.assertEqual(result['summary']['messages'],2)
        self.assertEqual(result['summary']['words'],6)
        self.assertEqual(result['people_count'],1)
        self.assertEqual({r['word'] for r in result['top_words']},{'coffee','coding','tea','lunch'})

    def test_ambiguous_guid_recipients_and_excluded_bodies(self):
        self.message('ambiguous',guid='two-recipients')
        self.message('ambiguous',chat='bob',guid='two-recipients')
        identifier = self.message('deleted text')
        self.db.execute('UPDATE messages SET decoded=? WHERE id=?',(json.dumps({'edits':[{'status':'Unsent'}]}),identifier))
        identifier = self.message('undecoded')
        self.db.execute("UPDATE messages SET decode_status='unsupported' WHERE id=?",(identifier,))
        identifier = self.message('unknown date')
        self.db.execute('UPDATE messages SET timestamp_ns=NULL WHERE id=?',(identifier,))
        self.message('https://example.invalid/path 123')
        self.message('current words')
        self.commit()
        result = self.report()
        self.assertEqual(result['summary']['messages'],1)
        self.assertEqual(result['coverage'],{'ambiguous_recipient':1,'unsent':1,'unsupported':1,'invalid_dates':1,'no_words':1})

    def test_full_range_vocabulary_distinctive_words_and_period_gaps(self):
        self.message('coffee '*30)
        self.message('tea '*60,day='2024-03-02',chat='bob')
        self.commit()
        result = self.report()
        self.assertEqual([r['period'] for r in result['timeline']],['2024-01-01','2024-02-01','2024-03-01'])
        self.assertEqual(result['timeline'][1]['words'],0)
        self.assertIsNone(result['timeline'][1]['words_per_message'])
        self.assertIsNone(result['timeline'][1]['tone']['laughter']['rate'])
        self.assertNotIn('changes', result)
        self.assertEqual([(r['word'],r['count']) for r in result['top_words']], [('tea',60),('coffee',30)])
        self.assertEqual(result['summary']['unique_words'], 2)
        self.assertEqual({p['distinctive'][0]['word'] for p in result['people']},{'coffee','tea'})
        # Equal proportions at different message volumes produce no change.
        self.assertEqual(distinctive_words(Counter(coffee=30),Counter(coffee=60)),[])

    def test_selected_people_have_aligned_timelines(self):
        self.message('coffee '*110)
        self.message('coffee '*110,day='2024-02-02',chat='email')
        self.message('tea '*110,day='2024-02-02',chat='bob')
        self.commit()
        a,b = self.person('+13175550100'),self.person('other@example.invalid')
        result = self.report(person_ids=[a['id'],b['id']])
        self.assertEqual(len(result['people']),2)
        for person in result['people']:
            self.assertEqual(len(person['timeline']),2)
        only_a = self.report(person_ids=[a['id']])
        self.assertEqual(only_a['summary']['messages'],2)
        self.assertEqual(only_a['summary']['diversity'],1)
        self.assertEqual(only_a['summary']['diversity_windows'],2)
        self.assertEqual(only_a['people'][0]['distinctive'],[])
        focused = self.report(person_ids=[a['id'],b['id']],focus='coffee')['context']
        series = {p['id']:p for p in focused['people']}
        self.assertEqual(series[a['id']]['rate'],1000)
        self.assertEqual(series[b['id']]['rate'],0)
        self.assertIsNone(series[b['id']]['timeline'][0]['rate'])
        self.assertEqual(series[b['id']]['timeline'][1]['rate'],0)

    def test_context_is_message_bounded_and_requires_support(self):
        for _ in range(5):
            self.message('work coffee')
            self.message('work coding',day='2024-02-02')
        self.message('unrelated banana',day='2024-02-02')
        self.commit()
        result = self.report(focus='WORK')
        context = result['context']
        self.assertEqual(context['word'],'work')
        self.assertEqual([r['count'] for r in context['timeline']],[5,5])
        self.assertIsNone(context['timeline'][0]['context_change'])
        self.assertEqual(context['timeline'][1]['context_change'],1)
        self.assertEqual(context['timeline'][1]['neighbors'][0]['word'],'coding')
        self.assertNotIn('banana',{r['word'] for r in context['people'][0]['neighbors']})
        self.assertEqual(self.report(focus='missing')['context']['timeline'][0]['count'],0)

    def test_standalone_multiple_words_share_one_scan_without_vocabulary(self):
        self.message('coffee coffee coding')
        self.message('tea coding',day='2024-03-02',chat='bob')
        self.message('incoming coffee',sender='a')
        self.commit()
        with patch('pymessage.semantic.compute',side_effect=AssertionError('must not analyze vocabulary')), patch('pymessage.semantic.rows',wraps=semantic.rows) as scan:
            result = self.app.word_context(Config(group_by='month'),[],['Coffee','tea','coffee'])
        self.assertEqual(scan.call_count,1)
        self.assertEqual(result['words'],['coffee','tea'])
        coffee,tea = result['contexts']
        self.assertEqual([r['count'] for r in coffee['timeline']],[2,0,0])
        self.assertEqual([r['count'] for r in tea['timeline']],[0,0,1])
        self.assertIsNone(tea['timeline'][1]['rate'])
        self.assertEqual(tea['timeline'][0]['rate'],0)
        self.assertEqual(tea['timeline'][2]['neighbors'][0]['word'],'coding')

    def test_emoji_only_mixed_and_compound_messages(self):
        self.message('😂😂')
        self.message('love 😂❤️',chat='email')
        self.message('👍🏽 👨‍👩‍👧‍👦 🇺🇸 1️⃣',day='2024-02-02',chat='bob')
        self.message('❤',day='2024-03-02')
        self.message('😂',sender='a')
        self.message('😂',chat='group')
        self.message('😂',kind='reaction')
        self.commit()
        targets = ['😂','❤️','👍🏽','👨‍👩‍👧‍👦','🇺🇸','1️⃣']
        result = self.app.word_context(Config(group_by='month'),[],targets)
        by_word = {c['word']:c for c in result['contexts']}
        self.assertEqual([r['count'] for r in by_word['😂']['timeline']],[3,0,0])
        self.assertEqual(by_word['😂']['timeline'][0]['rate'],600)
        self.assertEqual(by_word['❤️']['timeline'][2]['rate'],1000)
        self.assertEqual({r['word'] for r in by_word['😂']['timeline'][0]['neighbors']},{'love','❤️'})
        for target in targets[2:]:
            self.assertEqual(by_word[target]['timeline'][1]['count'],1)
            self.assertEqual(by_word[target]['timeline'][1]['rate'],250)
        mixed = self.app.word_context(Config(group_by='month'),[],['love','😂','👍','👨'])
        self.assertEqual(mixed['contexts'][0]['timeline'][0]['rate'],200)
        self.assertTrue(all(row['count']==0 for c in mixed['contexts'][2:] for row in c['timeline']))
        a = self.person('+13175550100')
        filtered = self.app.word_context(Config(group_by='month'),[a['id']],['😂','👨‍👩‍👧‍👦'])
        self.assertEqual(filtered['contexts'][0]['people'][0]['rate'],500)
        self.assertIsNone(filtered['contexts'][0]['timeline'][1]['rate'])
        self.assertEqual(filtered['contexts'][1]['people'][0]['count'],0)
        # The separate vocabulary view still ignores emoji-only messages.
        self.assertEqual(self.report()['summary']['words'],1)

    def test_emoji_context_cache_normalizes_variants_and_rejects_word_only_cache(self):
        self.message('❤ ❤️')
        self.commit()
        config = Config(group_by='month')
        first = self.app.word_context(config,[],['❤','❤️'])
        self.assertEqual(first['words'],['❤️'])
        with patch('pymessage.semantic.contexts',side_effect=AssertionError('same emoji must reuse cache')):
            self.assertTrue(self.app.word_context(config,[],['❤︎'])['cached'])
        sources = [list(row) for row in self.db.execute('SELECT id,snapshot_hash,decoder,last_success FROM sources ORDER BY id')]
        base = digest([semantic.VERSION,TOKENIZER_VERSION,sources,self.app.contacts.labels,self.app.contacts.identities,[],
                       config.timezone,None,None,'month',[],[]])
        legacy_path = self.app.output/'app-semantic'/(digest([base,'love'])+'.context.json')
        legacy_path.write_text(json.dumps({'word':'love','timeline':[{'count':999}],'people':[]}))
        with patch('pymessage.semantic.contexts',wraps=semantic.contexts) as scan:
            current = self.app.word_context(config,[],['love'])
        self.assertEqual(scan.call_count,1)
        self.assertFalse(current['cached'])
        self.assertEqual(current['contexts'][0]['timeline'][0]['count'],0)
        self.assertEqual(current['contexts'][0]['rate_unit'],'tokens')

    def test_standalone_reuses_original_cache_and_only_scans_missing_words(self):
        self.message('coffee coding')
        self.commit()
        self.report(focus='coffee')
        config = Config(group_by='month')
        with patch('pymessage.semantic.contexts',side_effect=AssertionError('cache should be reused')):
            self.assertTrue(self.app.word_context(config,[],['coffee'])['cached'])
        with patch('pymessage.semantic.contexts',wraps=semantic.contexts) as batch:
            result = self.app.word_context(config,[],['coding','coffee'])
        self.assertEqual(batch.call_count,1)
        self.assertEqual(batch.call_args.args[4],['coding'])
        self.assertEqual([c['word'] for c in result['contexts']],['coding','coffee'])
        with patch('pymessage.semantic.contexts',side_effect=AssertionError('cache should be reused')):
            self.assertTrue(self.app.word_context(config,[],['coffee','coding'])['cached'])

    def test_standalone_person_and_date_filters_invalidate_cache(self):
        self.message('coffee coding')
        self.message('tea coffee',day='2024-02-02',chat='bob')
        self.commit()
        config = Config(group_by='month')
        self.app.word_context(config,[],['coffee','tea'])
        a = self.person('+13175550100')
        result = self.app.word_context(config,[a['id']],['coffee','tea'])
        self.assertFalse(result['cached'])
        self.assertEqual(result['contexts'][0]['people'][0]['id'],a['id'])
        self.assertEqual(result['contexts'][1]['people'][0]['rate'],0)
        later = self.app.word_context(Config(group_by='month',start_date='2024-02-01'),[],['coffee'])
        self.assertFalse(later['cached'])
        self.assertEqual([r['count'] for r in later['contexts'][0]['timeline']],[1])
        self.message('tea coffee coffee')
        self.commit()
        self.assertFalse(self.app.word_context(config,[a['id']],['coffee'])['cached'])

    def test_standalone_empty_and_validation(self):
        result = self.app.word_context(Config(group_by='month'),[],['coffee'])
        self.assertEqual(result['contexts'][0]['timeline'],[])
        for selected in ([],['two words'],[''],['123'],['coffee']*7,'coffee',[None]):
            with self.assertRaises(ValueError):
                self.app.word_context(Config(group_by='month'),[],selected)
        with self.assertRaises(ValueError):
            self.app.word_context(Config(group_by='month'),['unknown'],['coffee'])

    def test_standalone_background_job(self):
        self.message('coffee tea')
        self.commit()
        with patch('pymessage.semantic.compute',side_effect=AssertionError('no vocabulary scan')), patch('pymessage.cli.execute',side_effect=AssertionError('no import')):
            self.app.start('word-context',{'group_by':'month','words':['coffee','tea']})
            self.app.worker.join(timeout=3)
        job = self.app.status()['job']
        self.assertEqual(job['state'],'complete',job)
        self.assertEqual(job['result']['words'],['coffee','tea'])

    def test_tone_and_names_are_per_message_not_per_repetition(self):
        self.app.contacts = ContactBook.from_records([
            {'id':'a','name':'Alex Smith','phones':['+13175550100'],'emails':['alex@example.invalid']},
            {'id':'b','name':'Alex Jones','phones':[],'emails':['other@example.invalid']},
        ])
        self.message('Alex Alex lol lol 😂 thanks please Taylor')
        self.message('Alex Smith is not happy. Maybe?')
        self.commit()
        result = self.report(names=['Taylor'])
        names = {row['name']:row for row in result['names']}
        self.assertEqual(names['alex']['count'],1)
        self.assertTrue(names['alex']['ambiguous'])
        self.assertTrue(names['alex']['first_name_only'])
        self.assertEqual(names['alex smith']['count'],1)
        self.assertTrue(names['taylor']['custom'])
        self.assertEqual(result['summary']['tone']['laughter'],{'count':1,'rate':50})
        self.assertEqual(result['summary']['tone']['politeness']['count'],1)
        self.assertEqual(result['summary']['tone']['positive wording']['count'],0)
        self.assertEqual(result['summary']['tone']['uncertainty']['count'],1)
        self.assertEqual(result['summary']['tone']['questions']['count'],1)
        missing = self.report(names=['Unmentioned'])
        self.assertEqual(missing['names'][0]['name'],'unmentioned')
        self.assertEqual(missing['names'][0]['count'],0)

    def test_cache_base_and_context_reused_and_invalidate_on_source_or_contacts(self):
        self.message('coding coffee')
        self.commit()
        self.assertFalse(self.report()['cached'])
        with patch('pymessage.semantic.compute',side_effect=AssertionError('base recount')):
            self.assertFalse(self.report(focus='coffee')['cached'])
        with patch('pymessage.semantic.compute',side_effect=AssertionError('base recount')), patch('pymessage.semantic.context',side_effect=AssertionError('context recount')):
            self.assertTrue(self.report(focus='coffee')['cached'])
        self.message('new text')
        self.commit()
        self.assertFalse(self.report()['cached'])
        self.app.contacts = ContactBook()
        self.assertFalse(self.report()['cached'])

    def test_boundaries_timezone_empty_and_validation(self):
        self.message('first word',day='2024-03-10')
        self.message('last word',day='2024-03-11')
        self.commit()
        result = self.report(group_by='day',timezone='America/Indiana/Indianapolis',start_date='2024-03-09',end_date='2024-03-10')
        self.assertEqual(result['summary']['messages'],1)
        self.assertEqual(result['timeline'][0]['period'],'2024-03-09')
        self.assertEqual(self.report(start_date='2099-01-01')['timeline'],[])
        for options in ({'focus':'two words'},{'names':['']},{'group_by':'time_of_day'},{'person_ids':['bad']},{'person_ids':['p']*7},{'start_date':'2024-02-01','end_date':'2024-01-01'}):
            with self.assertRaises((ValueError,KeyError)):
                self.report(**options)

    def test_job_no_sync_render_or_source_access(self):
        self.message('great coffee thanks Alex')
        self.commit()
        with patch('pymessage.cli.execute',side_effect=AssertionError('sync')), patch('pymessage.app.render_charts',side_effect=AssertionError('render')):
            self.app.start('semantic',{'group_by':'month','focus':'coffee'})
            self.app.worker.join(timeout=3)
        self.assertFalse(self.app.worker.is_alive())
        job = self.app.status()['job']
        self.assertEqual(job['state'],'complete',job)
        self.assertEqual(job['result']['summary']['words'],4)

    def test_http_dispatch_auth_validation_and_static_script_without_socket(self):
        self.app.origin = 'http://127.0.0.1:8765'
        handler = Handler.__new__(Handler)
        handler.server = SimpleNamespace(app=self.app)
        handler.send = Mock()
        handler.path = '/api/semantic'
        handler.headers = {'Host':'127.0.0.1:8765'}
        handler.dispatch(True)
        self.assertEqual(handler.send.call_args.args[0],401)
        payload = json.dumps({'focus':'two words'}).encode()
        handler.headers.update({'Authorization':'Bearer '+self.app.token,'Content-Length':str(len(payload))})
        handler.rfile = io.BytesIO(payload)
        handler.dispatch(True)
        self.assertEqual(handler.send.call_args.args[0],400)
        handler.path = '/api/word-context'
        payload = json.dumps({'words':[]}).encode()
        handler.headers['Content-Length'] = str(len(payload))
        handler.rfile = io.BytesIO(payload)
        handler.dispatch(True)
        self.assertEqual(handler.send.call_args.args[0],400)
        payload = json.dumps({'words':['coffee','tea'],'group_by':'month'}).encode()
        handler.headers['Content-Length'] = str(len(payload))
        handler.rfile = io.BytesIO(payload)
        handler.dispatch(True)
        self.assertEqual(handler.send.call_args.args[0],202)
        self.app.worker.join(timeout=3)
        self.assertEqual(self.app.status()['job']['state'],'complete')
        handler.path = '/semantic.js'
        handler.dispatch(False)
        self.assertEqual(handler.send.call_args.args[0],200)
        self.assertIn(b'function renderSemantic',handler.send.call_args.args[1])
        handler.path = '/emoji-picker.json'
        handler.dispatch(False)
        self.assertEqual(handler.send.call_args.args[0],200)
        self.assertGreater(len(json.loads(handler.send.call_args.args[1])['emoji']),3000)


class SemanticHelpersTests(unittest.TestCase):
    def test_unicode_links_and_diversity(self):
        self.assertEqual(words("CAFÉ café I'm 123 https://example.invalid/alex person@example.invalid"),['café','café',"i'm"])
        aggregate = Aggregate()
        aggregate.add(['word']*99,set(),set())
        self.assertIsNone(aggregate.summary()['diversity'])
        aggregate.add(['other'],set(),set())
        self.assertEqual(aggregate.summary()['diversity'],2)

    def test_name_boundaries_and_tone_overlap(self):
        matcher = NameMatcher(ContactBook(),['Ann','Alex Smith'])
        self.assertEqual(matcher.matches(words('annex ANN Alex Smith')),{'ann','alex smith'})
        self.assertEqual(tone_cues(words('lol maybe great?'),'lol maybe great?'),{'laughter','uncertainty','positive wording','questions'})


if __name__ == '__main__':
    unittest.main()
