"""Offline local visibility and waiting-age regressions."""
from datetime import datetime, timezone
from pathlib import Path
import json
import re
import subprocess
import tempfile
import unittest

from outcome_tracking import OutcomeTracker, ProposalVisibility


class ClearActionsTests(unittest.TestCase):
    def test_persistence_changed_numbers_restore_and_age(self):
        with tempfile.TemporaryDirectory() as folder:
            tracker = OutcomeTracker(Path(folder) / 'outcomes.sqlite3')
            store = ProposalVisibility(Path(folder) / 'dismissals.json')
            item = {'proposal_id': 'pause:a', 'action': 'PAUSE', 'saving_per_day': 100.01}
            report = {'recommendations': {'today': {'items': [item]}}}
            with tracker._db() as db:
                db.execute('INSERT INTO offers VALUES(?,?)', ('pause:a', json.dumps({'first_offered': '2026-10-19'})))
            now = datetime(2026, 10, 23, 10, tzinfo=timezone.utc)
            store.annotate(report, tracker, now)
            self.assertEqual(item['waiting_days'], 4)
            self.assertTrue(item['untouched'])
            store.update(item)
            store = ProposalVisibility(store.path)
            item['saving_per_day'] = 100.02
            store.annotate(report, tracker, now)
            self.assertTrue(item['dismissed'])
            self.assertFalse(item['untouched'])
            item['saving_per_day'] = 120
            store.annotate(report, tracker, now)
            self.assertFalse(item['dismissed'])
            self.assertTrue(item['returned_changed'])
            store.update(item, restore=True)
            store.annotate(report, tracker, now)
            self.assertFalse(item['returned_changed'])

    def test_missing_and_earliest_drift_date(self):
        with tempfile.TemporaryDirectory() as folder:
            tracker = OutcomeTracker(Path(folder) / 'outcomes.sqlite3')
            store = ProposalVisibility(Path(folder) / 'dismissals.json')
            items = [{'ad_id': 'a', 'features': [{'since': '2026-10-22T10:00:00Z'}, {'since': '2026-10-19T10:00:00Z'}]},
                     {'ad_id': 'b', 'features': []}]
            report = {'creative_drift': {'items': items}}
            store.annotate(report, tracker, datetime(2026, 10, 23, 10, tzinfo=timezone.utc))
            self.assertEqual(items[0]['waiting_days'], 4)
            self.assertIsNone(items[1]['waiting_days'])
            items[0]['features'] = [{'since': '2026-10-22T22:30:00Z'}]
            store.annotate(report, tracker, datetime(2026, 10, 23, 10, tzinfo=timezone.utc))
            self.assertEqual(items[0]['waiting_days'], 0)

    def test_all_inline_scripts_parse(self):
        html = Path(__file__).with_name('simple-web.html').read_text(encoding='utf-8')
        for script in re.findall(r'<script>(.*?)</script>', html, re.S):
            result = subprocess.run(['node', '--check'], input=script, encoding='utf-8', capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_theme_text_contrast(self):
        html = Path(__file__).with_name('simple-web.html').read_text(encoding='utf-8')
        def luminance(color):
            rgb = [int(color[i:i+2], 16)/255 for i in (1, 3, 5)]
            rgb = [v/12.92 if v <= .04045 else ((v+.055)/1.055)**2.4 for v in rgb]
            return sum(v*w for v, w in zip(rgb, (.2126, .7152, .0722)))
        def check(fg, bg):
            a, b = sorted((luminance(fg), luminance(bg)))
            self.assertGreaterEqual((b+.05)/(a+.05), 4.5, (fg, bg))
        blocks = re.findall(r'--bg:.*?color-scheme:(?:light|dark)', html, re.S)[:3]
        for block in blocks:
            colors = dict(re.findall(r'--([\w-]+):(#[\da-f]{6})', block))
            for surface in ('bg', 'surface', 'surface-2'):
                for text in ('ink', 'muted'):
                    check(colors[text], colors[surface])
            for kind in ('stop', 'grow', 'watch', 'idle'):
                check(colors[kind], colors[kind+'-soft'])
            for brand in ('brand', 'brand-2'):
                check(colors['on-brand'], colors[brand])


if __name__ == '__main__':
    unittest.main()
