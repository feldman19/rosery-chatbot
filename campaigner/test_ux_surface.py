"""Offline rendering checks for owner cards, including dynamic provider text."""
from pathlib import Path
import re
import json
import subprocess
import unittest


class SurfaceTests(unittest.TestCase):
    def test_cards_hide_evidence_and_translate_provider_jargon(self):
        from test_card_component import HELPERS, DOM
        from creative_drift import LABELS
        harness = "\nconst keys=" + json.dumps(list(LABELS)+['unknown']) + r""";
for(const key of keys){const c=driftCard({ad_id:'a',name:'מודעה',features:[{feature:key}]});
assert.ok(!/ROAS|CTR|Advantage\+/i.test(surface(c)));assert.ok(c.querySelector('details'));assert.ok(c.querySelector('.primary').onclick)}
const phrase=ownerText('ROAS CTR Advantage+ אד-סט קריאייטיב ריטרגטינג פרוספקטינג');
assert.ok(!/ROAS|CTR|Advantage\+|אד-סט|קריאייטיב|ריטרגטינג|פרוספקטינג/i.test(phrase));
"""
        result = subprocess.run(['node'], input=HELPERS+'\n'+DOM+harness, capture_output=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_every_known_meta_feature_has_customer_language(self):
        from creative_drift import LABELS
        page = Path(__file__).with_name('simple-web.html').read_text(encoding='utf-8')
        mapping = page.split('function featureInfo(', 1)[1].split('function driftAfter(', 1)[0]
        for key in LABELS:
            with self.subTest(key=key):
                self.assertRegex(mapping, r"\b" + re.escape(key) + r":\['[^']+','מטא [^']+'\]")
        self.assertIn("node.hidden=!pageVersion", page)
        self.assertNotIn('גרסה לא ידועה', page)

    def test_internal_hypotheses_are_not_experiment_candidates(self):
        source = Path(__file__).with_name('server.py').read_text(encoding='utf-8')
        self.assertIn("{'candidates':experiment_store.candidates([])}", source)

    def test_no_corrupted_hebrew_and_valid_script(self):
        page = Path(__file__).with_name('simple-web.html').read_text(encoding='utf-8')
        self.assertNotIn('????', page)
        script = re.search(r'<script>([\s\S]*?)</script>', page)[1]
        result = subprocess.run(['node', '--check'], input=script,
                                capture_output=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
