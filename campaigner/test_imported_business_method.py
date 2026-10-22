import json
import shutil
import tempfile
import unittest
from pathlib import Path
from imported_business_method import load_imported_method, load_imported_business_method, FOLDER
from business_context import load_rosery_dossier


class ImportedBusinessMethodTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'campaigner'
        self.root.mkdir()
        base = Path(__file__).resolve().parent
        self.sources = self.root.parent / 'outputs' / FOLDER
        shutil.copytree(base.parent / 'outputs' / FOLDER, self.sources)
        (self.root / 'businesses/rosery').mkdir(parents=True)
        shutil.copyfile(base / 'businesses/rosery/profile.json', self.root / 'businesses/rosery/profile.json')

    def test_structured_dossier_and_no_authority(self):
        value = load_rosery_dossier(self.root)['business_method']
        self.assertEqual('available', value['status'])
        self.assertIn('headline_no_emoji', {p['id'] for p in value['preferences']})
        self.assertIn('thesis-handoff-20261007.md', value['sources'])
        self.assertFalse(value['approval_imported'])
        self.assertFalse(value['financial_thresholds_executable'])
        self.assertNotIn('raw_text', value)

    def test_other_tenant_or_account_never_receives_preferences(self):
        for business, account in [('other','393059510164294'), ('rosery','1')]:
            value = load_imported_business_method(self.root,business_id=business,account_id=account)
            self.assertEqual([], value['preferences'])
            self.assertEqual('tenant_mismatch', value['reason'])

    def test_missing_and_changed_sources_fail_closed(self):
        path = self.sources / 'account-rules.txt'
        path.write_text('changed', encoding='utf-8')
        self.assertEqual('unavailable', load_imported_method(self.root)['status'])
        path.unlink()
        self.assertEqual([], load_imported_method(self.root)['preferences'])

    def test_unsupported_excerpt_and_traversal_fail_closed(self):
        path = self.sources / 'curated-business-method.json'
        policy = json.loads(path.read_text(encoding='utf-8'))
        policy['preferences'][0]['source_excerpt'] = 'not present in original'
        path.write_text(json.dumps(policy),encoding='utf-8')
        self.assertEqual('unavailable',load_imported_method(self.root)['status'])
        policy['sources']['../secret'] = {'sha256':'0'*64}
        path.write_text(json.dumps(policy),encoding='utf-8')
        self.assertEqual('unavailable',load_imported_method(self.root)['status'])


if __name__ == '__main__': unittest.main()
