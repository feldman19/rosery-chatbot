import tempfile
import unittest
from copy import deepcopy
from datetime import timedelta
from unittest.mock import Mock

from automation_policy import AutomationPolicy
from automation_shadow import AutomationShadow, evaluate_shadow
from outcome_tracking import OutcomeTracker
from rulebook import load_rulebook
from test_rule_engine import NOW, ad, report


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = NOW
        self.generation = 0
        self.book = load_rulebook()
        self.policy = AutomationPolicy(self.tmp.name, lambda: self.generation,
                                       lambda: self.book, lambda: self.now)
        self.body = dict(action='pause_ad', mode='shadow', roas_threshold=2.3,
                         interval_hours=2, shadow_days=14)
        self.policy.save(self.body)
        self.rows = [ad('a', 300, 2, 600, funnel={'add_to_cart': 0, 'initiate_checkout': 0, 'link_ctr': 0}),
                     ad('b', 300, 4, 1500)]
        for row in self.rows:
            row['hierarchy_complete'] = True
        self.report = report(self.rows)
        self.tracker = OutcomeTracker(self.tmp.name + '/outcomes.sqlite3')
        self.shadow = AutomationShadow(self.policy, lambda: (None, self.report), self.tracker,
                                       lambda: self.book, lambda: self.now)

    def test_defaults_and_validation(self):
        self.assertEqual(self.policy.status()['policy']['roas_threshold'], 2.3)
        for key, value in [('mode', 'auto'), ('action', 'scale'), ('roas_threshold', float('nan')),
                           ('interval_hours', True), ('shadow_days', 1.5)]:
            with self.assertRaises(ValueError):
                self.policy.save({**self.body, key: value})

    def test_owner_threshold_and_rulebook_unchanged(self):
        original = deepcopy(self.book)
        self.policy.save({**self.body, 'roas_threshold': 1.8})
        self.assertEqual(self.shadow.tick()['items'], [])
        self.assertEqual(self.book, original)
        self.policy.save(self.body)
        self.assertTrue(self.shadow.tick()['items'][0]['would_pause'])

    def test_last_ad_and_combined_pauses(self):
        self.report = report(self.rows[:1])
        self.assertIn('last_active_ad_in_adset', self.shadow.tick()['items'][0]['blockers'])
        self.policy.save({**self.body, 'roas_threshold': 6})
        self.report = report(self.rows)
        self.assertTrue(all(not i['would_pause'] for i in self.shadow.tick()['items']))

    def test_unknown_rescue_and_metrics(self):
        self.rows[0]['funnel'] = {}
        self.assertIn('rescue_signal_unknown', self.shadow.tick()['items'][0]['blockers'])
        self.rows[0]['purchases'] = None
        self.policy.save({**self.body, 'roas_threshold': 2.4})
        run = self.shadow.tick()
        self.assertEqual(run['items'], [])
        missing = next(d for d in run['assessments'] if d['entity_id'] == 'a')
        self.assertEqual(missing['action'], 'INSUFFICIENT_DATA')
        self.assertIsNone(missing['numbers']['purchases'])

    def test_stale_and_incomplete(self):
        self.report['freshness']['state'] = 'stale'
        self.assertFalse(self.shadow.tick()['items'][0]['would_pause'])
        self.policy.save({**self.body, 'roas_threshold': 2.4})
        self.report['windows']['3']['partial'] = True
        self.assertEqual(self.shadow.tick()['status'], 'blocked')

    def test_generation_and_book_invalidate(self):
        self.generation += 1
        self.assertIsNone(self.shadow.tick())
        self.assertIn('stopped', self.policy.status()['blockers'])
        self.policy.save(self.body)
        self.book = {**self.book, 'version': 999}
        self.assertIsNone(self.shadow.tick())
        self.book = {'status': 'unavailable'}
        self.assertIn('rulebook_unavailable', self.policy.status()['blockers'])

    def test_fake_clock_interval_restart_and_end(self):
        self.assertIsNotNone(self.shadow.tick())
        self.assertIsNone(self.shadow.tick())
        self.now += timedelta(hours=2)
        self.assertIsNotNone(self.shadow.tick())
        self.now += timedelta(days=14)
        self.assertIsNone(self.shadow.tick())
        self.assertEqual(self.policy.status()['effective_mode'], 'shadow')
        self.assertEqual(self.shadow.status()['phase'], 'ended')

    def test_stop_during_snapshot_discards_run(self):
        def snapshot():
            self.generation += 1
            return None, self.report
        self.shadow.snapshot = snapshot
        self.assertIsNone(self.shadow.tick())
        self.assertEqual(self.shadow.status()['runs'], [])

    def test_no_write_authority_dependencies(self):
        import subprocess
        import sys
        result = subprocess.run([sys.executable, '-B', '-c',
            "import sys; import automation_shadow; assert not any(x in sys.modules for x in "
            "('meta_execution','meta_write_connection','exact_approval_bridge','shopify_direct'))"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        run = self.shadow.tick()
        self.assertEqual(run['provider_writes'], 0)
        self.assertFalse(run['live_action_allowed'])

    def test_comparison_preserves_evidence_kind(self):
        self.tracker.list = Mock(return_value={'items': [
            {'proposal_id': 'pause:a', 'source': 'owner_marked', 'verification': 'not_seen'},
            {'proposal_id': 'pause:a', 'source': 'auto_detected', 'verification': 'confirmed'}]})
        comparisons = self.shadow.tick()['items'][0]['comparison']
        self.assertTrue(comparisons[0]['owner_reported'])
        self.assertFalse(comparisons[0]['change_observed'])
        self.assertTrue(comparisons[1]['change_observed'])
        self.assertTrue(all(not c['execution_verified'] for c in comparisons))

    def test_real_stop_generation_never_loads_writer(self):
        from exact_approval_bridge import ExactApprovalBridge
        loader, transport = Mock(), Mock()
        bridge = ExactApprovalBridge(self.tmp.name, b'x' * 32, loader, transport)
        self.policy.generation = lambda: bridge._control()['generation']
        self.policy.save(self.body)
        self.assertIsNotNone(self.shadow.tick())
        bridge.stop()
        self.assertIsNone(self.shadow.tick())
        loader.assert_not_called()
        transport.read.assert_not_called()
        transport.write.assert_not_called()

    def test_worker_uses_fake_clock_and_wait(self):
        class FakeEvent:
            waits = 0
            def is_set(event):
                return event.waits >= 2
            def wait(event, seconds):
                self.assertEqual(seconds, 30)
                event.waits += 1
                self.now += timedelta(hours=2)
        self.shadow.stop_event = FakeEvent()
        self.shadow.start()
        self.shadow.thread.join(timeout=3)
        self.assertFalse(self.shadow.thread.is_alive())
        self.assertEqual(len(self.shadow.status()['runs']), 2)

    def test_unknown_hierarchy_and_inventory_block(self):
        self.rows[0]['hierarchy_complete'] = None
        self.report['windows']['7']['partial'] = True
        item = self.shadow.tick()['items'][0]
        self.assertIn('hierarchy_unknown', item['blockers'])
        self.assertIn('inventory_incomplete', item['blockers'])
        self.now += timedelta(hours=2)
        self.report['windows']['7']['partial'] = False
        self.rows[0]['hierarchy_complete'] = True
        self.rows[1]['hierarchy_complete'] = None
        self.assertIn('last_active_ad_in_adset', self.shadow.tick()['items'][0]['blockers'])

    def test_no_numeric_pause_cap(self):
        base = self.rows[0]
        self.report = report([{**base, 'id': str(i)} for i in range(51)] + [self.rows[1]])
        self.assertEqual(sum(i['would_pause'] for i in self.shadow.tick()['items']), 51)

    def test_previous_offer_comparison_survives_paused_candidate(self):
        self.shadow.tick()
        self.now += timedelta(hours=2)
        self.rows[0]['configured_active'] = False
        self.tracker.list = Mock(return_value={'items': [
            {'proposal_id': 'pause:a', 'source': 'auto_detected', 'verification': 'confirmed'}]})
        run = self.shadow.tick()
        self.assertEqual(run['items'], [])
        self.assertTrue(run['comparisons'][0]['change_observed'])


if __name__ == '__main__':
    unittest.main()
