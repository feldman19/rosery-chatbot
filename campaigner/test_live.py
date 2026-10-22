import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock
from live import LiveReports


class LiveTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
    def tearDown(self):self.temp.cleanup()

    def test_failure_preserves_last_good_and_restart(self):
        reader=Mock(side_effect=[{'verified':True,'rows':[{'spend':'4'}]},ValueError('SECRET_TOKEN')])
        live=LiveReports(self.temp.name,readers={'meta':reader})
        live.refresh(force=True);first=live.snapshot()['sources']['meta']
        live.refresh(force=True);second=live.snapshot()['sources']['meta']
        self.assertEqual(first['data'],second['data'])
        self.assertEqual(first['last_success_at'],second['last_success_at'])
        self.assertNotIn('SECRET_TOKEN',second['error'])
        restarted=LiveReports(self.temp.name,readers={'meta':reader})
        self.assertEqual(restarted.snapshot()['sources']['meta']['data'],first['data'])

    def test_single_flight_and_throttle(self):
        entered=threading.Event();release=threading.Event()
        def reader(_):entered.set();release.wait(2);return {'verified':True,'rows':[]}
        live=LiveReports(self.temp.name,readers={'meta':reader})
        thread=threading.Thread(target=live.refresh);thread.start();self.assertTrue(entered.wait(1))
        self.assertFalse(live.refresh(force=True));self.assertTrue(live.snapshot()['refreshing'])
        release.set();thread.join(3)
        self.assertFalse(live.refresh());self.assertFalse(live.snapshot()['refreshing'])

    def test_partial_source_failure_is_independent(self):
        live=LiveReports(self.temp.name,readers={'meta':lambda _: {'verified':True,'rows':[]},'shopify':Mock(side_effect=OSError())})
        live.refresh(force=True);state=live.snapshot()
        self.assertIsNotNone(state['sources']['meta']['last_success_at'])
        self.assertIsNone(state['sources']['shopify']['data'])
        self.assertIsNotNone(state['sources']['shopify']['error'])
        self.assertFalse(state['live_action_allowed'])

    def test_snapshot_is_copy(self):
        live=LiveReports(self.temp.name,readers={'meta':lambda _: {'verified':True}})
        state=live.snapshot();state['sources']['meta']['error']='injected'
        self.assertIsNone(live.snapshot()['sources']['meta']['error'])

    def test_unstarted_collector_is_not_active(self):
        live=LiveReports(self.temp.name,readers={'meta':lambda _: {'verified':True}})
        self.assertFalse(live.snapshot()['collection_active'])

    def test_partial_does_not_replace_complete(self):
        reader=Mock(side_effect=[{'verified':True,'complete':True,'rows':[{'spend':'4'}]}, {'verified':True,'complete':False,'rows':[]}])
        live=LiveReports(self.temp.name,readers={'meta':reader})
        live.refresh(force=True);live.refresh(force=True)
        source=live.snapshot()['sources']['meta']
        self.assertTrue(source['data']['complete']);self.assertEqual(len(source['data']['rows']),1)
        self.assertIsNotNone(source['error'])

    def test_background_loop_collects_without_browser_request(self):
        called=threading.Event()
        def reader(_):called.set();return {'verified':True,'rows':[]}
        live=LiveReports(self.temp.name,readers={'meta':reader})
        live.start()
        try:
            self.assertTrue(called.wait(2))
            # Wait on a bounded condition, not a browser-triggered refresh.
            for _ in range(100):
                if not live.snapshot()['refreshing']:break
                threading.Event().wait(.01)
            self.assertIsNotNone(live.snapshot()['sources']['meta']['last_success_at'])
        finally:live.stop()
