import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import subprocess
from quality_review import classify, run_check, save, exit_code
from claude_quality_review import subscription_environment, subscription_auth


class QualityReviewTests(unittest.TestCase):
    def test_failed_gate_returns_failure_to_automated_callers(self):
        self.assertEqual(exit_code('tests_failed'), 1)
        self.assertEqual(exit_code('stale_source'), 1)
        self.assertEqual(exit_code('tests_passed_external_review_pending'), 0)
        self.assertEqual(exit_code('review_assessment_pending'), 0)
    def test_api_routing_environment_is_not_forwarded(self):
        with patch.dict('os.environ', {'ANTHROPIC_API_KEY': 'fake', 'CLAUDE_CODE_USE_BEDROCK': '1', 'PATH': 'safe'}, clear=True):
            self.assertEqual(subscription_environment(), {'PATH': 'safe'})

    def test_api_auth_cannot_be_mistaken_for_subscription(self):
        response = subprocess.CompletedProcess([], 0, stdout=b'{"loggedIn":true,"authMethod":"api_key","apiProvider":"firstParty","subscriptionType":"max"}')
        with patch('claude_quality_review.subprocess.run', return_value=response):
            self.assertFalse(subscription_auth('claude', {}))

    def test_generated_owner_pages_match_actual_ui_sources(self):
        from quality_review import ROOT
        template = (ROOT / 'owner-portal.template.html').read_text(encoding='utf-8')
        template=template.replace('/* STUDIO_DESIGN */',(ROOT/'studio-design.css').read_text(encoding='utf-8'))
        template=template.replace('/* DASHBOARD_DESIGN */',(ROOT/'dashboard-design.css').read_text(encoding='utf-8'))
        for marker, name in [('/* OWNER_PORTAL_MODEL */', 'owner-portal-model.js'), ('/* PREPARATION_TASK_UI */', 'preparation-task-ui.js'), ('/* OWNER_PORTAL_APP */', 'owner-portal.js')]:
            template = template.replace(marker, (ROOT / name).read_text(encoding='utf-8'))
        for filename, configuration in [('owner-web.html', "const token='__SESSION__';const localMode=true;"), ('mobile-web.html', 'const token=null;const localMode=false;')]:
            self.assertEqual((ROOT / filename).read_text(encoding='utf-8'), template.replace('/* LOCAL_CONFIGURATION */', configuration))

    def test_auth_failure_never_counts_as_review_or_production_acceptance(self):
        self.assertEqual(classify([{'state': 'passed'}], False, {'state': 'authentication_required'}),
                         'tests_passed_external_review_pending')
        self.assertEqual(classify([{'state': 'passed'}], False, {'state': 'completed'}),
                         'review_assessment_pending')

    def test_stale_or_failed_evidence_cannot_pass(self):
        self.assertEqual(classify([{'state': 'passed'}], True, {}), 'stale_source')
        for state in ('failed', 'timeout', 'unavailable'):
            self.assertEqual(classify([{'state': state}], False, {'state': 'completed'}), 'tests_failed')
        self.assertEqual(classify([], False, {}), 'tests_failed')

    def test_timeout_receipt_and_no_retry(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory, patch('quality_review.subprocess.run', side_effect=subprocess.TimeoutExpired('test', 1)) as command:
            result = run_check('sample', 'python', ('-V',), Path(directory), timeout=1)
            self.assertEqual(result['state'], 'timeout')
            self.assertEqual(command.call_count, 1)

    def test_report_escapes_external_text(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory)
            report = {'state': '<script>', 'checks': [], 'claude': {'state': '<img>'}}
            save(root, report)
            page = (root / 'index.html').read_text(encoding='utf-8')
            self.assertNotIn('<script>', page)
            self.assertIn('&lt;script&gt;', page)
            self.assertTrue((root / 'status.json').exists())


if __name__ == '__main__':
    unittest.main()
