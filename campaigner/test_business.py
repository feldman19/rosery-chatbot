"""The business config: ROSERY's values are exactly the old constants, and another business is truly separate."""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import business
from business import ROSERY, Business

HERE = Path(__file__).resolve().parent
# The literals every module had before business.py; they must now live only in business.py.
OLD = {'meta_account': '393059510164294', 'meta_app_id': '1101281589359409', 'meta_sales_campaign': '120246379886090730',
       'meta_launch_campaign': '120253309360290730', 'shop': '21cc82-3.myshopify.com'}
# ROSERY-only one-off scripts, synthetic fixtures and sandbox data keep their literals (see .agents/design/multi-business.md).
KEEP = {'business.py', 'build_reviewed_launch.py', 'build_rosery_economics_profile.py', 'create_gift_test_20261019.py',
        'verify_gift_test_20261019.py', 'launch_proposal_schema.py', 'sandbox_runner.py', 'strategy.py', 'server.py'}


class RoseryUnchangedTests(unittest.TestCase):
    def test_rosery_config_is_exactly_the_old_constants(self):
        for field, value in OLD.items():
            self.assertEqual(getattr(ROSERY, field), value, field)
        self.assertEqual((ROSERY.id, ROSERY.name, ROSERY.currency, ROSERY.timezone, ROSERY.language, ROSERY.country,
                          ROSERY.rulebook_file, ROSERY.approval_tenant),
                         ('rosery', 'ROSERY', 'ILS', 'Asia/Jerusalem', 'he', 'IL', 'rosery-rulebook.json', 'rosery-local'))
        self.assertEqual(ROSERY.rules_path, HERE / 'rules')

    def test_without_a_business_file_this_process_is_rosery(self):
        self.assertNotIn(business.ENV, os.environ)
        self.assertIs(business.ACTIVE, ROSERY)

    def test_every_module_constant_still_has_rosery_s_value(self):
        import campaign_operations_transport, connections, creative_drift, management_report, meta_batch, meta_direct
        import meta_execution, operations_config, registration, registration_records, rulebook, shopify_orders, true_profit
        expected = [(meta_direct.APP_ID, OLD['meta_app_id']), (meta_direct.ACCOUNT, OLD['meta_account']),
                    (meta_direct.LAUNCH, OLD['meta_launch_campaign']), (meta_direct.SALES_RESTART, OLD['meta_sales_campaign']),
                    (campaign_operations_transport.ACCOUNT, OLD['meta_account']), (campaign_operations_transport.PARENT, OLD['meta_sales_campaign']),
                    (meta_batch.PARENT, OLD['meta_sales_campaign']), (meta_execution.CAMPAIGN, OLD['meta_launch_campaign']),
                    (operations_config.CAMPAIGN, OLD['meta_sales_campaign']), (registration.CAMPAIGN, OLD['meta_launch_campaign']),
                    (connections.SHOP, OLD['shop']), (management_report.SHOP, OLD['shop']), (registration_records.SHOP, OLD['shop']),
                    (registration_records.TENANT, 'rosery'), (shopify_orders.TENANT, 'rosery'), (creative_drift.ACCOUNT, OLD['meta_account']),
                    (rulebook.DIRECTORY, HERE / 'rules'), (rulebook.SOURCE_REF, 'campaigner/rules/rosery-rulebook.json'),
                    (true_profit.STORE_TZ, 'Asia/Jerusalem')]
        for got, want in expected:
            self.assertEqual(got, want)

    def test_the_rulebook_still_loads_for_rosery(self):
        from rulebook import load_rulebook
        book = load_rulebook()
        self.assertEqual((book['status'], book['business_id'], book['account_id']), ('available', 'rosery', OLD['meta_account']))
        self.assertEqual(book['source_ref'], 'campaigner/rules/rosery-rulebook.json')

    def test_no_module_hard_codes_rosery_any_more(self):
        pattern = re.compile('|'.join(re.escape(v) for v in OLD.values()) + r"|'ILS'|'Asia/Jerusalem'")
        found = []
        for path in HERE.rglob('*.py'):
            if path.name.startswith('test_') or 'tests' in path.parts or path.name in KEEP or 'campaigner-test-' in str(path):
                continue
            for number, line in enumerate(path.read_text(encoding='utf-8', errors='replace').splitlines(), 1):
                general = (path.name == '__init__.py' and 'evidence' in path.parts and 'UTC' in line) or line.startswith('SYMBOL = {')
                if pattern.search(line) and not general:
                    found.append(f'{path.relative_to(HERE)}:{number}')
        self.assertEqual(found, [])

    def test_server_keeps_only_its_synthetic_sandbox_literal(self):
        lines = [l for l in (HERE / 'server.py').read_text(encoding='utf-8').splitlines() if re.search(r"'ILS'|393059510164294", l)]
        self.assertEqual(len(lines), 1)
        self.assertIn("'budget_minor': 6000", lines[0])  # the seeded sandbox entity, not the live account


def sample(**changes):
    value = {'id': 'shop-two', 'name': 'Shop Two', 'description': 'Israeli home goods shop', 'meta_account': '111222333',
             'meta_sales_campaign': '999888777666', 'shop': 'shop-two.myshopify.com', 'currency': 'ILS',
             'timezone': 'Asia/Jerusalem', 'language': 'he', 'country': 'IL'}
    value.update(changes)
    return {k: v for k, v in value.items() if v is not ...}


class OtherBusinessTests(unittest.TestCase):
    def test_a_valid_file_gives_defaults_for_what_is_not_connected_yet(self):
        b = business.validate(sample())
        self.assertEqual((b.meta_app_id, b.meta_launch_campaign, b.rules_dir, b.rulebook_file, b.approval_tenant),
                         (None, None, 'rules', 'rulebook.json', 'shop-two-local'))

    def test_wrong_values_are_refused_in_hebrew(self):
        for bad in (sample(meta_account='act_123'), sample(shop='https://shop.com'), sample(currency='ils'), sample(timezone='Mars/Base'),
                    sample(language='fr'), sample(id='Shop Two'), sample(rules_dir='../../etc'), sample(rulebook_file='x/rulebook.json'),
                    sample(extra=1), sample(meta_account=...), [], sample(name='')):
            with self.assertRaises(ValueError) as caught:
                business.validate(bad)
            self.assertRegex(str(caught.exception), '[א-ת]')

    def test_rules_folder_is_the_business_s_own(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'business.json').write_text(json.dumps(sample()), encoding='utf-8')
            b = business.load(Path(folder) / 'business.json')
            self.assertEqual(b.rules_path, (Path(folder) / 'rules').resolve())

    def test_a_data_folder_cannot_be_run_by_another_business(self):
        with tempfile.TemporaryDirectory() as folder:
            business.check_data_folder(folder, ROSERY)  # the original layout (no business.json) is ROSERY's
            other = business.validate(sample())
            with self.assertRaises(ValueError):
                business.check_data_folder(folder, other)
            business.write(folder, other)
            business.check_data_folder(folder, other)
            with self.assertRaises(ValueError) as caught:
                business.check_data_folder(folder, ROSERY)
            self.assertIn('Shop Two', str(caught.exception))

    def test_the_server_refuses_another_business_s_data_folder(self):
        from server import make_server
        with tempfile.TemporaryDirectory() as folder:
            business.write(folder, business.validate(sample()))
            with self.assertRaises(ValueError):
                make_server(0, folder, workspace='manager')

    def test_a_process_started_for_another_business_uses_its_ids_and_its_own_empty_rules(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'business.json').write_text(json.dumps(sample()), encoding='utf-8')
            code = ('import json,meta_direct,connections,campaign_operations_transport as t,rulebook;'
                    'b=rulebook.load_rulebook();'
                    'print(json.dumps([meta_direct.ACCOUNT,connections.SHOP,t.PARENT,meta_direct.LAUNCH,b["status"],b["reason"]]))')
            done = subprocess.run([sys.executable, '-B', '-c', code], cwd=HERE, capture_output=True, text=True, timeout=120,
                                  env={**os.environ, business.ENV: str(Path(folder) / 'business.json')})
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(json.loads(done.stdout.strip().splitlines()[-1]),
                             ['111222333', 'shop-two.myshopify.com', '999888777666', None, 'unavailable', 'missing_changed_or_invalid_rulebook'])


if __name__ == '__main__':
    unittest.main()
