"""Group-chat heart charts and rankings on tiny in-memory-text fixtures."""
import json
import unittest

import test_reactions as fixtures
from test_reactions import BASE, DAY


class HeartTests(unittest.TestCase):
    message = fixtures.ReactionTests.message
    event = fixtures.ReactionTests.event
    result = fixtures.ReactionTests.result

    def setUp(self):
        fixtures.ReactionTests.setUp(self)

    def tearDown(self):
        fixtures.ReactionTests.tearDown(self)

    def test_exact_buckets_timeline_and_message_ranking(self):
        one = self.message(stamp=BASE)
        two = self.message(stamp=BASE+2*DAY)
        mine = self.message(sender='Me',stamp=BASE+2*DAY)
        self.message(sender='b',stamp=BASE)
        self.event(one,sender='b')
        for target in (two,mine):
            self.event(target,sender='b')
            self.event(target,sender='c')
        self.archive.db.execute('UPDATE messages SET text=? WHERE id=?',('<script>literal message</script>',two))
        hearts = self.result(group_by='day',start_date='2024-01-01',end_date='2024-01-05')['hearts']
        self.assertEqual((hearts['total'],hearts['message_count']),(5,3))
        self.assertEqual(hearts['buckets'],[1,2])
        self.assertEqual(hearts['by_sender']['a@example.invalid'],{'hearts':3,'messages':2,'buckets':{'1':1,'2':1}})
        self.assertEqual(hearts['by_sender']['Me']['buckets'],{'2':1})
        self.assertEqual(hearts['by_sender']['b@example.invalid']['hearts'],0)
        self.assertEqual(hearts['periods'],['2024-01-01','2024-01-02','2024-01-03','2024-01-04'])
        self.assertEqual(hearts['period_totals'],[1,0,4,0])
        self.assertEqual([row['hearts'] for row in hearts['top_messages']],[2,2,1])
        self.assertEqual(next(row['text'] for row in hearts['top_messages'] if row['id']==two),'<script>literal message</script>')
        self.assertEqual(sum(sum(row['values']) for row in hearts['series']),5)

    def test_matrix_direction_denominators_and_reactor_only_people(self):
        a = self.message(sender='a')
        self.message(sender='a')
        b = self.message(sender='b')
        self.event(a,sender='b',part=0)
        self.event(a,sender='b',part=1)  # One message, not two hearts in this cell.
        self.event(b,sender='a')
        self.event(a,sender='c')  # Reactor with no authored messages.
        self.event(a,sender='a')  # Self-reaction excluded from matrix.
        self.event(b,sender='Me')
        matrix = self.result()['hearts']['matrix']
        self.assertEqual(matrix['message_counts'],{'a@example.invalid':2,'b@example.invalid':1})
        self.assertEqual(matrix['counts']['b@example.invalid']['a@example.invalid'],1)
        self.assertEqual(matrix['counts']['a@example.invalid'],{'b@example.invalid':1})
        self.assertEqual(matrix['counts']['c@example.invalid'],{'a@example.invalid':1})
        self.assertEqual(matrix['counts']['Me'],{'b@example.invalid':1})
        self.assertEqual(set(matrix['people']),{'a@example.invalid','b@example.invalid','c@example.invalid','Me'})

    def test_matrix_filters_removals_duplicates_and_replacements(self):
        self.message(sender='a',stamp=BASE-DAY)
        a = self.message(sender='a',stamp=BASE,guid='shared')
        a = self.message(sender='a',stamp=BASE,guid='shared')
        self.message(sender='a',stamp=BASE,present=0)
        self.message(sender='a',stamp=BASE,chat='other')
        self.event('shared',sender='b',stamp=BASE+20*DAY)
        self.event('shared',sender='c',stamp=BASE+20*DAY)
        self.event('shared',3000,sender='c',stamp=BASE+21*DAY)
        matrix = self.result(start_date='2024-01-01',end_date='2024-01-02')['hearts']['matrix']
        self.assertEqual(matrix['message_counts'],{'a@example.invalid':1})
        self.assertEqual(matrix['counts'],{'b@example.invalid':{'a@example.invalid':1}})
        self.event('shared',2001,sender='b',stamp=BASE+22*DAY)
        matrix = self.result(start_date='2024-01-01',end_date='2024-01-02')['hearts']['matrix']
        self.assertEqual(matrix['counts'],{})
        self.assertEqual(matrix['message_counts'],{'a@example.invalid':1})

    def test_one_person_per_whole_message_and_current_state(self):
        target = self.message()
        self.event(target,part=0)
        self.event(target,part=1)
        self.assertEqual(self.result()['total'],2)  # Existing tapback totals retain part-level semantics.
        self.assertEqual(self.result()['hearts']['total'],1)
        self.event(target,3000,part=0)
        self.assertEqual(self.result()['hearts']['total'],1)
        self.event(target,2001,part=1)
        self.assertEqual(self.result()['hearts']['total'],0)
        self.event(target,2006,emoji='❤️',sender='c')
        self.assertEqual(self.result()['hearts']['total'],0)  # Only standard Loved tapbacks.
        self.event(target,sender='Me')
        self.assertEqual(self.result()['hearts']['total'],1)

    def test_date_filter_uses_message_date_and_later_removals(self):
        target = self.message(stamp=BASE)
        self.event(target,stamp=BASE+40*DAY)
        result = self.result(group_by='day',start_date='2024-01-01',end_date='2024-01-02')['hearts']
        self.assertEqual(result['period_totals'],[1])
        self.event(target,3000,stamp=BASE+41*DAY)
        result = self.result(group_by='day',start_date='2024-01-01',end_date='2024-01-02')['hearts']
        self.assertEqual(result['period_totals'],[0])
        self.assertEqual(result['buckets'],[])
        self.assertEqual(result['top_messages'],[])

    def test_local_time_grouping_and_undated_hearted_message(self):
        target = self.message(stamp=BASE)
        self.event(target)
        unknown = self.message(stamp=None)
        self.event(unknown,sender='c')
        result = self.result(group_by='day',timezone='America/Indiana/Indianapolis')['hearts']
        self.assertEqual(result['periods'],['2023-12-31'])
        self.assertEqual(result['period_totals'],[1])
        self.assertEqual(result['undated_messages'],1)
        self.assertEqual(result['total'],2)

    def test_duplicate_target_and_event_only_count_once(self):
        self.message(sender='a',guid='shared')
        latest = self.message(sender='c',guid='shared')
        self.event('shared',guid='event')
        self.event('shared',guid='event')
        result = self.result()['hearts']
        self.assertEqual(result['total'],1)
        self.assertEqual(result['top_messages'][0]['id'],latest)
        self.assertEqual(result['top_messages'][0]['sender'],'c@example.invalid')

    def test_preview_limit_and_unsent_text_not_exposed(self):
        for _ in range(53):
            self.event(self.message())
        target = self.message()
        self.event(target,sender='b'); self.event(target,sender='c')
        self.archive.db.execute('UPDATE messages SET text=? WHERE id=?',('x'*1200,target))
        result = self.result()['hearts']
        self.assertEqual(result['message_count'],54)
        self.assertEqual(len(result['top_messages']),50)
        self.assertEqual(len(result['top_messages'][0]['text']),1000)
        self.assertTrue(result['top_messages'][0]['truncated'])
        self.archive.db.execute('UPDATE messages SET decoded=? WHERE id=?',(json.dumps({'edits':[{'status':'Unsent'}]}),target))
        row = self.result()['hearts']['top_messages'][0]
        self.assertEqual(row['text'],'')
        self.assertTrue(row['unsent'])

    def test_non_group_and_html_excluded(self):
        self.event(self.message())
        self.archive.db.execute("UPDATE sources SET kind='html'")
        self.assertIsNone(self.result()['hearts'])
        self.archive.db.execute("UPDATE sources SET kind='sqlite'")
        self.archive.db.execute("DELETE FROM chat_participants WHERE handle_id!='a'")
        self.assertIsNone(self.result()['hearts'])

    def test_week_month_hour_and_long_range_grouping(self):
        self.event(self.message(stamp=BASE))
        self.event(self.message(stamp=BASE+14*DAY))
        week = self.result(group_by='week')['hearts']
        self.assertEqual(week['period_totals'],[1,0,1])
        self.assertEqual(self.result(group_by='month')['hearts']['period_totals'],[2])
        self.assertEqual(len(self.result(group_by='time_of_day')['hearts']['periods']),24)
        self.assertEqual(len(self.result(group_by='day_of_week')['hearts']['periods']),7)
        long = self.result(group_by='day',start_date='2000-01-01',end_date='2025-01-01')['hearts']
        self.assertTrue(long['coarsened'])
        self.assertEqual(long['group_by'],'month')
        self.assertEqual(sum(long['period_totals']),2)


if __name__ == '__main__':
    unittest.main()
