import unittest
from unittest.mock import patch
from pathlib import Path
import test_server


class AutomationHTTPTests(unittest.TestCase):
    setUp = test_server.ServerTests.setUp
    tearDown = test_server.ServerTests.tearDown
    request = test_server.ServerTests.request

    def test_settings_location_style_and_stop_explanation(self):
        page = Path(__file__).with_name('simple-web.html').read_text(encoding='utf-8')
        today = page.split('<section data-tab="today"', 1)[1].split('</section>', 1)[0]
        self.assertIn('id="shadow-form"', today)
        self.assertLess(today.index('id="acted"'), today.index('id="shadow-form"'))
        self.assertIn('<div class="card" aria-labelledby="shadow-title">', today)
        self.assertIn('<details class="why">', today)
        self.assertIn('<button class="btn ghost" type="submit">', today)
        self.assertIn('<button class="btn primary" type="button" id="shadow-stop"', today)
        self.assertIn('עוצר את האוטומציה וגם מבטל אישורים ידניים שעוד לא בוצעו.', today)
        self.assertIn('aria-describedby="shadow-stop-help"', today)
        self.assertEqual(page.count('id="shadow-form"'), 1)

    def test_owner_guard_errors_are_readable(self):
        source = Path(__file__).with_name('server.py').read_text(encoding='utf-8')
        guards = [b.split('# END auto-mode-shadow')[0]
                  for b in source.split('# BEGIN auto-mode-shadow')[1:]
                  if "workspace!='manager'" in b.split('# END auto-mode-shadow')[0]]
        self.assertEqual(len(guards), 2)
        for block in guards:
            self.assertIn("{'error':'זמין לבעלים בלבד'}", block)
            self.assertNotIn('????', block)

    def test_policy_routes_and_existing_stop(self):
        with patch('meta_write_connection.load', side_effect=AssertionError('No write credentials')) as loader:
            self.assertEqual(self.request('/api/automation-shadow', token=False)[0], 403)
            body = dict(action='pause_ad', mode='shadow', roas_threshold=2.1, interval_hours=2, shadow_days=14)
            self.assertEqual(self.request('/api/automation-shadow/policy', body, origin=False)[0], 403)
            code, saved = self.request('/api/automation-shadow/policy', body)
            self.assertEqual(code, 200)
            self.assertEqual(saved['effective_mode'], 'shadow')
            self.assertEqual(self.request('/api/automation-shadow')[1]['policy']['roas_threshold'], 2.1)
            self.assertEqual(self.request('/api/meta-execution/stop', {})[0], 200)
            self.assertEqual(self.request('/api/automation-shadow')[1]['effective_mode'], 'manual')
            self.assertEqual(self.request('/api/automation-shadow/policy', {**body, 'mode': 'auto'})[0], 400)
            self.assertEqual(self.request('/api/automation-shadow/policy', body)[0], 200)
            self.assertEqual(self.request('/api/automation-shadow/stop', {})[1]['effective_mode'], 'manual')
            loader.assert_not_called()

