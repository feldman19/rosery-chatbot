import json
import unittest

from cohort import HostContext, person_hmac, reconcile_cohort
from registration_records import normalize_registrations, TENANT, SHOP, ACCOUNT


class RegistrationRecordsTests(unittest.TestCase):
    def setUp(self):
        self.context = HostContext(TENANT, SHOP, ACCOUNT, 'ILS', '2026-10-17T12:00:00Z',
                                   frozenset({'registrations_verified', 'shopify_orders_direct'}))
        self.secret = b'x' * 32
        self.row = {'id': 'r1', 'email': ' Person@Example.Invalid ',
                    'registered_at': '2026-10-16T10:00:00Z'}

    def normalize(self, rows=None, context=None):
        return normalize_registrations([self.row] if rows is None else rows, context=context or self.context,
                                       source_id='registrations_verified', tenant_secret=self.secret)

    def test_default_unknown_and_privacy(self):
        result = self.normalize()
        record = result['records'][0]
        self.assertEqual(record['linkage'], 'unknown')
        self.assertIsNone(record['ad_id'])
        self.assertEqual(record['person_hmac'], person_hmac(TENANT, self.secret, 'person@example.invalid'))
        output = json.dumps(result).casefold()
        self.assertNotIn('person@example.invalid', output)
        self.assertNotIn('"email"', output)
        self.assertFalse(result['binding']['adapter_live_verification'])
        self.assertFalse(result['coverage']['provider_window_complete'])

    def test_no_ad_means_no_exact_click_claim(self):
        record = self.normalize([dict(self.row, linkage='exact_click')])['records'][0]
        self.assertEqual(record['linkage'], 'unknown')

    def test_ad_id_alone_does_not_create_click_evidence(self):
        record = self.normalize([dict(self.row, ad_id='ad1')])['records'][0]
        self.assertEqual(record['linkage'], 'unknown')

    def test_declared_explicit_evidence_preserved(self):
        record = self.normalize([dict(self.row, ad_id='ad1', linkage='view_through')])['records'][0]
        self.assertEqual(record['linkage'], 'view_through')

    def test_identical_normalized_duplicates_deduplicate(self):
        result = self.normalize([self.row, dict(self.row, email='person@example.invalid')])
        self.assertEqual(len(result['records']), 1)
        self.assertEqual(result['duplicates_removed'], 1)

    def test_conflicting_identical_ids_quarantined(self):
        result = self.normalize([self.row, dict(self.row, email='other@example.invalid')])
        self.assertEqual(result['records'], [])
        self.assertEqual(result['conflicts'][0]['id'], 'r1')
        self.assertNotIn('other@example.invalid', json.dumps(result))

    def test_missing_version_does_not_leave_false_unique(self):
        result = self.normalize([self.row, dict(self.row, registered_at=None)])
        self.assertEqual(result['records'], [])
        self.assertEqual(len(result['conflicts']), 1)

    def test_unsupported_conflicting_version_quarantines_id(self):
        result = self.normalize([self.row, dict(self.row, phone='not exported')])
        self.assertEqual(result['records'], [])
        self.assertEqual(len(result['conflicts']), 1)

    def test_malformed_linkage_rejected_without_crashing(self):
        result = self.normalize([dict(self.row, linkage=['exact_click'])])
        self.assertEqual(result['records'], [])
        self.assertEqual(result['coverage']['reason_counts']['invalid_linkage'], 1)

    def test_missing_identity_and_date_reported_separately(self):
        result = self.normalize([{'id': 'r2'}])
        counts = result['coverage']['reason_counts']
        self.assertEqual(counts['missing_identity'], 1)
        self.assertEqual(counts['missing_registration_date'], 1)

    def test_future_and_naive_dates_not_accepted(self):
        result = self.normalize([dict(self.row, id='r2', registered_at='2026-10-18T10:00:00Z'),
                                 dict(self.row, id='r3', registered_at='2026-10-16T10:00:00')])
        self.assertEqual(result['records'], [])
        self.assertEqual(result['coverage']['reason_counts']['future_registration_date'], 1)
        self.assertEqual(result['coverage']['reason_counts']['invalid_registration_date'], 1)

    def test_scope_and_currency_rejected(self):
        for context in [HostContext('other', SHOP, ACCOUNT, 'ILS', self.context.as_of, frozenset()),
                        HostContext(TENANT, SHOP, ACCOUNT, 'USD', self.context.as_of, frozenset())]:
            with self.subTest(context=context), self.assertRaises(ValueError):
                self.normalize(context=context)

    def test_unverified_source_cannot_become_verified_record(self):
        context = HostContext(TENANT, SHOP, ACCOUNT, 'ILS', self.context.as_of, frozenset())
        result = self.normalize(context=context)
        self.assertEqual(result['records'], [])
        self.assertFalse(result['binding']['source_verified_by_host'])

    def test_urls_and_extra_pii_not_accepted_or_exported(self):
        result = self.normalize([dict(self.row, url='https://example.invalid/?ad=123', phone='private')])
        self.assertEqual(result['records'], [])
        self.assertNotIn('private', json.dumps(result))
        self.assertNotIn('ad=123', json.dumps(result))

    def test_bad_id_is_not_echoed(self):
        result = self.normalize([dict(self.row, id='person@example.invalid')])
        self.assertEqual(result['issues'][0]['reasons'], ['missing_or_invalid_id'])
        self.assertNotIn('person@example.invalid', json.dumps(result))

    def test_reconcile_synthetic_order_preserves_unknown_attribution_and_costs(self):
        registrations = self.normalize()['records']
        order = {key: registrations[0][key] for key in ('tenant', 'shop', 'account', 'currency', 'person_hmac')}
        order.update(id='o1', source_id='shopify_orders_direct', source_verified=True,
                     as_of=self.context.as_of, ordered_at='2026-10-17T10:00:00Z',
                     product_gross='118', shipping_gross='0', tax='18', refund_product_gross='0',
                     refund_shipping_gross='0', refund_tax='0')
        result = reconcile_cohort(registrations, [order], context=self.context)
        self.assertEqual(result['matches'][0]['match_basis'], 'person_hmac_unique')
        self.assertEqual(result['matches'][0]['linkage'], 'unknown')
        self.assertEqual(result['matches'][0]['contribution_status'], 'estimate')
        self.assertFalse(result['matches'][0]['verified_profit'])

    def test_multiple_registrations_one_person_remains_ambiguous(self):
        rows = self.normalize([self.row, dict(self.row, id='r2')])['records']
        order = {key: rows[0][key] for key in ('tenant', 'shop', 'account', 'currency', 'person_hmac')}
        order.update(id='o1', source_id='shopify_orders_direct', source_verified=True,
                     as_of=self.context.as_of, ordered_at='2026-10-17T10:00:00Z')
        result = reconcile_cohort(rows, [order], context=self.context)
        self.assertEqual(result['matches'][0]['match_basis'], 'ambiguous')
        self.assertIsNone(result['matches'][0]['ad_id'])


if __name__ == '__main__':
    unittest.main()
