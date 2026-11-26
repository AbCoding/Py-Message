"""Cross-person timelines on tiny fixtures, without rendering or real imports."""
from datetime import datetime, timezone
from unittest.mock import patch
import unittest

import test_people as fixtures
from pymessage.analysis import Config
from pymessage.contacts import ContactBook

BASE = int(datetime(2024,1,1,tzinfo=timezone.utc).timestamp())*10**9
DAY = 86400*10**9


class ActivityTests(unittest.TestCase):
    add = fixtures.PeopleTests.add
    person = fixtures.PeopleTests.person
    matched = fixtures.PeopleTests.matched

    def setUp(self):
        fixtures.PeopleTests.setUp(self)
        self.matched()
        self.revision = 0
        self.db.execute('UPDATE messages SET timestamp_ns=timestamp_ns+?',(BASE,))
        self.commit()

    def tearDown(self):
        fixtures.PeopleTests.tearDown(self)

    def commit(self):
        self.revision += 1
        self.db.execute('UPDATE sources SET last_success=?',(str(self.revision),))
        self.db.commit()

    def activity(self, metric='total', top=5, **kwargs):
        ranking_mode = kwargs.pop('ranking_mode','overall')
        return self.app.activity(Config(group_by=kwargs.pop('group_by','day'),**kwargs),metric,top,ranking_mode)

    def test_period_leader_changes_with_gaps_and_reuses_cache(self):
        self.db.execute("UPDATE messages SET timestamp_ns=? WHERE id='s:8'",(BASE+2*DAY,))
        self.commit()
        overall = self.activity(top=1)
        with patch('pymessage.activity.compute',side_effect=AssertionError('unnecessary recount')):
            result = self.activity(top=1,ranking_mode='period')
            wider = self.activity(top=15,ranking_mode='period')
        self.assertTrue(result['cached'])
        self.assertEqual(len(overall['series']),1)
        self.assertEqual(len(result['series']),2)
        self.assertEqual([len(rows) for rows in result['period_leaders']],[1,0,1])
        self.assertNotEqual(result['period_leaders'][0][0]['id'],result['period_leaders'][2][0]['id'])
        self.assertEqual([p['points'] for p in result['series']],
                         [[{'index':0,'count':4}],[{'index':2,'count':1}]])
        self.assertEqual(wider['period_leaders'],result['period_leaders'])

    def test_period_leaders_include_people_outside_global_top_fifteen(self):
        for i in range(16):
            handle = 'extra'+str(i)
            self.db.execute('INSERT INTO participants VALUES(?,?,?,?)',('s',handle,handle+'@example.invalid','{}'))
            self.db.execute('INSERT INTO chats VALUES(?,?,?,?,?,?)',(handle,'s',handle,handle,handle,'{}'))
            self.db.execute('INSERT INTO chat_participants VALUES(?,?,?)',(handle,'s',handle))
            for _ in range(2):
                identifier = self.add(handle,handle)
                self.db.execute('UPDATE messages SET timestamp_ns=? WHERE id=?',(BASE,identifier))
        self.db.execute("UPDATE messages SET timestamp_ns=? WHERE id='s:8'",(BASE+DAY,))
        self.commit()
        result = self.activity(top=1,ranking_mode='period')
        late_leader = result['period_leaders'][1][0]['id']
        self.assertNotIn(late_leader,[row['id'] for row in result['ranking'][:15]])
        self.assertIn(late_leader,[row['id'] for row in result['series']])
        for top in (5,15):
            rows = self.activity(top=top,ranking_mode='period')['period_leaders'][0]
            self.assertEqual(len(rows),top)
            metadata = {row['id']:row for row in result['ranking']}
            self.assertEqual(rows,sorted(rows,key=lambda row:(-row['count'],metadata[row['id']]['name'].casefold(),row['id'])))

    def test_period_ranking_by_direction_and_calendar_interval(self):
        self.db.execute("UPDATE messages SET timestamp_ns=? WHERE id='s:8'",(BASE+35*DAY,))
        self.commit()
        for group,period in [('day','2024-02-05'),('week','2024-02-05'),('month','2024-02-01')]:
            result = self.activity(metric='received',top=1,ranking_mode='period',group_by=group)
            self.assertEqual(result['periods'][-1],period)
            self.assertEqual(result['period_leaders'][0][0]['count'],2)
            self.assertEqual(result['period_leaders'][-1][0]['count'],1)
            sent = self.activity(metric='sent',ranking_mode='period',group_by=group,end_date='2024-03-01')
            self.assertEqual(sent['period_leaders'][-1],[])

    def test_combines_aliases_counts_both_directions_and_excludes_groups(self):
        result = self.activity()
        self.assertEqual(result['total'],5)
        self.assertEqual(result['people_count'],2)
        alex = result['ranking'][0]
        self.assertEqual((alex['sent'],alex['received'],alex['count']),(2,2,4))
        self.assertEqual(result['periods'],['2024-01-01'])
        self.assertEqual(result['series'][0]['values'],[4])
        self.assertEqual(result['period_totals'],[5])

    def test_sent_and_received_rank_separately(self):
        sent = self.activity('sent')
        received = self.activity('received')
        self.assertEqual(sent['total'],2)
        self.assertEqual(sent['people_count'],1)
        self.assertEqual(received['total'],3)
        self.assertEqual([r['count'] for r in received['ranking']],[2,1])

    def test_zero_periods_and_exclusive_date_end(self):
        self.db.execute("UPDATE messages SET timestamp_ns=? WHERE id='s:8'",(BASE+2*DAY,))
        self.commit()
        result = self.activity(start_date='2024-01-01',end_date='2024-01-05')
        self.assertEqual(result['periods'],['2024-01-01','2024-01-02','2024-01-03','2024-01-04'])
        self.assertEqual(result['period_totals'],[4,0,1,0])
        self.assertEqual(self.activity(end_date='2024-01-03')['total'],4)

    def test_duplicate_memberships_and_guids_across_sources(self):
        self.db.execute("INSERT INTO memberships VALUES('email','s:2',0)")
        self.add('email','email',guid='their-phone')
        self.db.execute("INSERT INTO participants VALUES('copy','a','+13175550100','{}')")
        self.db.execute("INSERT INTO chats VALUES('copy-chat','copy','1','copy','Copy','{}')")
        self.db.execute("INSERT INTO chat_participants VALUES('copy-chat','copy','a')")
        self.add('copy-chat','a',guid='their-phone',source='copy')
        self.commit()
        self.assertEqual(self.activity()['total'],5)

    def test_absent_reactions_invalid_dates_and_unknown_direction(self):
        self.add('phone','a',kind='reaction')
        self.add('phone','a',present=0)
        self.db.execute("UPDATE messages SET timestamp_ns=NULL WHERE id='s:1'")
        self.db.execute("UPDATE messages SET is_from_me=NULL WHERE id='s:2'")
        self.commit()
        result = self.activity()
        self.assertEqual(result['total'],3)
        self.assertEqual(result['invalid_dates'],1)
        self.assertEqual(result['unknown_direction'],1)

    def test_local_day_handles_dst(self):
        def ns(value):
            return int(datetime.fromisoformat(value).timestamp())*10**9
        self.db.execute("UPDATE messages SET timestamp_ns=? WHERE id='s:1'",(ns('2024-03-10T05:00:00+00:00'),))
        self.db.execute("UPDATE messages SET timestamp_ns=? WHERE id='s:2'",(ns('2024-03-11T03:59:59+00:00'),))
        self.db.execute("UPDATE messages SET timestamp_ns=? WHERE id='s:3'",(ns('2024-03-11T04:00:00+00:00'),))
        self.commit()
        result = self.activity(timezone='America/Indiana/Indianapolis',start_date='2024-03-10',end_date='2024-03-11')
        self.assertEqual(result['periods'],['2024-03-10'])
        self.assertEqual(result['total'],2)

    def test_month_and_week_buckets(self):
        self.db.execute("UPDATE messages SET timestamp_ns=? WHERE id='s:4'",(BASE+60*DAY,))  # March 1, leap year.
        self.commit()
        result = self.activity(group_by='month')
        self.assertEqual(result['periods'],['2024-01-01','2024-02-01','2024-03-01'])
        self.assertEqual(result['period_totals'],[4,0,1])
        week = self.activity(group_by='week',start_date='2024-01-02',end_date='2024-03-02')
        self.assertEqual(week['periods'][0],'2024-01-01')
        self.assertEqual(week['periods'][-1],'2024-02-26')
        self.assertEqual(week['total'],1)

    def test_cache_reuses_counts_when_top_changes_and_invalidates_on_sync(self):
        self.assertFalse(self.activity()['cached'])
        with patch('pymessage.activity.compute',side_effect=AssertionError('unnecessary recount')):
            result = self.activity(top=1)
        self.assertTrue(result['cached'])
        self.assertEqual(len(result['series']),1)
        self.db.execute("UPDATE messages SET source_present=0 WHERE id='s:1'")
        self.commit()
        self.assertFalse(self.activity()['cached'])
        self.assertEqual(self.activity()['total'],4)

    def test_contact_and_manual_link_changes_invalidate_cache(self):
        self.app.contacts = ContactBook()
        before = self.activity()
        self.assertEqual(before['people_count'],3)
        a = self.person('+13175550100')
        b = self.person('alex@example.invalid')
        self.app.link_people({'person_id':a['id'],'other_id':b['id']})
        after = self.activity()
        self.assertFalse(after['cached'])
        self.assertEqual(after['people_count'],2)
        self.app.contacts = ContactBook.from_records(fixtures.CONTACTS)
        self.assertFalse(self.activity()['cached'])

    def test_empty_range_and_invalid_requests(self):
        self.assertEqual(self.activity(start_date='2099-01-01')['series'],[])
        self.assertEqual(self.activity(start_date='2099-01-01',ranking_mode='period')['series'],[])
        for options in ({'metric':'invalid'}, {'ranking_mode':'invalid'}, {'top':0}, {'top':True}, {'group_by':'all'},
                        {'start_date':'2030-01-01','end_date':'2020-01-01'},
                        {'start_date':'2000-01-01','end_date':'2020-01-01'}):
            with self.assertRaises(ValueError):
                self.activity(**options)

    def test_background_job_without_importing_or_rendering(self):
        with patch('pymessage.cli.execute',side_effect=AssertionError('must not import')), patch('pymessage.app.render_charts',side_effect=AssertionError('must not render charts')):
            self.app.start('activity',{'group_by':'month','metric':'sent','top':5,'ranking_mode':'period'})
            self.app.worker.join(timeout=3)
        self.assertFalse(self.app.worker.is_alive())
        job = self.app.status()['job']
        self.assertEqual(job['state'],'complete',job)
        self.assertEqual(job['result']['total'],2)
        self.assertEqual(job['result']['ranking_mode'],'period')
        self.assertEqual(job['result']['period_leaders'][0][0]['count'],2)


if __name__ == '__main__':
    unittest.main()
