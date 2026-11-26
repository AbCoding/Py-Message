"""Cross-page scope, drill-down and archive-backed view regression checks."""
import unittest

import test_people as fixtures


class ExploreTests(unittest.TestCase):
    setUp = fixtures.PeopleTests.setUp
    tearDown = fixtures.PeopleTests.tearDown
    add = fixtures.PeopleTests.add

    def run_query(self, inspect=False, **fields):
        payload = {'group_by': 'month', **fields}
        return self.app.explore(self.app.config(payload), payload, inspect)

    def test_global_counts_drill_into_pages_and_preserve_group_membership(self):
        self.add('group', 'Me', guid='group-me')
        self.add('group', 'Me', kind='reaction')
        self.add('group', 'b', present=0)
        self.db.execute("INSERT INTO memberships VALUES('email','s:7',1)")
        self.db.commit()
        overview = self.run_query()
        by_type = {s['id']: s['count'] for s in overview['ranking']}
        self.assertEqual(by_type, {'direct': 5, 'groups': 3})
        for page in by_type:
            self.assertEqual(by_type[page], self.run_query(page=page)['total'])
        selected = self.run_query(page='groups', target='group')
        self.assertEqual(sorted(s['count'] for s in selected['ranking']), [1, 1, 1])
        alex = self.app.group_members('group')[0]
        member = self.run_query(page='groups', target='group', author=alex['id'])
        self.assertEqual(member['total'], 1)
        drill = self.run_query(inspect=True, page='groups', target='group', period='1970-01-01')
        self.assertEqual(len(drill['messages']), selected['total'])
        self.assertTrue(all(m['chat'] == 'group' for m in drill['messages']))

    def test_group_language_and_reply_timing_keep_their_own_denominators(self):
        self.db.execute("UPDATE messages SET text='Nice 🙂' WHERE guid='group-me'")
        self.db.commit()
        group = self.run_query(page='groups', target='group', measure='language')
        self.assertEqual(group['total'], 1)
        self.assertEqual(dict(group['emoji']), {'🙂': 1})
        self.assertEqual(group['ranking'][0]['value'], 1)
        person = next(p for p in self.app.people()['people'] if '+13175550100' in p['addresses'])
        reply = self.run_query(page='direct', target=person['id'], measure='reply')
        self.assertEqual((reply['total'], reply['ranking'][0]['value']), (1, 1))
        with self.assertRaises(ValueError):
            self.run_query(page='groups', target='group', measure='reply')

    def test_word_and_emoji_trends_and_matching_examples(self):
        self.db.execute("UPDATE messages SET text='Hello 🙂' WHERE guid='group-me'")
        self.db.execute("UPDATE messages SET text='HELLO hello' WHERE guid='group-a'")
        self.db.execute("UPDATE messages SET text='later' WHERE guid='group-b'")
        self.db.commit()
        filters = dict(page='groups', measure='language', direction='all')
        terms = self.run_query(**filters, language_stat='term', term='HELLO')
        self.assertEqual(terms['ranking'][0]['value'], 600)
        self.assertEqual(terms['query']['term'], 'hello')
        self.assertEqual(self.run_query(**filters, language_stat='emoji')['ranking'][0]['value'], 200)
        examples = self.run_query(True, **filters, language_stat='term', term='hello', period='1970-01-01')
        self.assertEqual(len(examples['messages']), 2)
        self.assertEqual(len(self.run_query(True, **filters, language_stat='emoji', period='1970-01-01')['messages']), 1)

    def test_missing_participants_and_hidden_bodies(self):
        self.db.execute("INSERT INTO chats VALUES('orphan','s','x','orphan','Unknown','{}')")
        self.add('orphan','Me',guid='unknown')
        self.db.execute("UPDATE messages SET decoded=? WHERE guid='group-me'", ('{"edits":[{"status":"Unsent"}]}',))
        self.db.commit()
        self.assertIn('unknown', [r['id'] for r in self.run_query()['ranking']])
        self.assertEqual(self.run_query(page='groups', measure='language')['total'],0)
        drill = self.run_query(inspect=True, page='groups', period='1970-01-01')
        self.assertEqual(next(m for m in drill['messages'] if m['sender']=='You')['text'], '')

    def test_saved_views_persist_only_filters_and_job_uses_archive(self):
        from pymessage.app import App
        views = self.app.saved_views({'name':'Monthly groups','page':'groups','group_by':'month','text':'private'})
        self.assertNotIn('text', views['Monthly groups'])
        self.assertEqual(App(self.args).saved_views(), views)
        self.app.start('explore', views['Monthly groups'])
        self.app.worker.join(5)
        self.assertEqual(self.app.job['result']['total'],3)
        self.assertEqual(self.app.saved_views({'name':'Monthly groups','delete':True}),{})

    def test_linking_people_keeps_actual_groups_on_the_group_page(self):
        people = self.app.people()['people']
        a,b = [next(p for p in people if address in p['addresses']) for address in ['+13175550100','other@example.invalid']]
        self.app.link_people({'person_id':a['id'], 'other_id':b['id']})
        self.assertEqual(self.run_query(page='groups')['total'],3)
        self.assertEqual([c['id'] for c in self.app.chats(scope='groups')['chats']],['group'])
