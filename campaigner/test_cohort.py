import copy
import unittest

from cohort import HostContext, person_hmac, reconcile_cohort


class CohortTests(unittest.TestCase):
    def setUp(self):
        self.context = HostContext('rosery', 'store.myshopify.com', '393', 'ILS',
                                   '2026-10-17T12:00:00+03:00', frozenset({'meta', 'shopify'}))
        self.base = dict(tenant='rosery', shop='store.myshopify.com', account='393', currency='ILS',
                         as_of='2026-10-17T11:00:00+03:00', source_verified=True,
                         person_hmac=person_hmac('rosery', b'x' * 32, 'normalized-person'))
        self.registration = dict(self.base, id='r1', event_id='event1', source_id='meta',
                                 registered_at='2026-10-16T10:00:00+03:00', ad_id='ad1', linkage='exact_click')
        self.order = dict(self.base, id='o1', source_id='shopify', ordered_at='2026-10-17T10:00:00+03:00',
                          product_gross='118', shipping_gross='23.60', tax='21.60',
                          refund_product_gross='0', refund_shipping_gross='0', refund_tax='0',
                          product_cost='20', shipping_cost='15', packaging_cost='4', payment_fee='2', other_cost='0')

    def run_case(self, regs=None, orders=None, context=None):
        return reconcile_cohort([self.registration] if regs is None else regs,
                                [self.order] if orders is None else orders,
                                context=context or self.context)

    def test_exact_match_and_complete_contribution(self):
        result = self.run_case()
        self.assertEqual(result['unique_registrants'], 1)
        match = result['matches'][0]
        self.assertEqual(match['linkage'], 'exact_click')
        self.assertEqual(match['net_revenue_ex_tax'], '120.00')
        self.assertEqual(match['contribution_before_ads'], '79.00')
        self.assertEqual(match['contribution_status'], 'calculated')
        self.assertFalse(match['verified_profit'])

    def test_distinct_orders_same_person_are_not_collapsed(self):
        result = self.run_case(orders=[self.order, dict(self.order, id='o2')])
        self.assertEqual(result['orders'], 2)
        self.assertEqual(result['cohorts'][0]['orders'], 2)
        self.assertEqual(result['cohorts'][0]['unique_registrants'], 1)

    def test_duplicate_events_and_orders_do_not_inflate(self):
        result = self.run_case(regs=[self.registration, dict(self.registration, id='r2')],
                               orders=[self.order, copy.deepcopy(self.order)])
        self.assertEqual(result['registrations'], 1)
        self.assertEqual(result['orders'], 1)
        self.assertEqual(result['duplicates_removed'], 2)

    def test_duplicate_event_alias_still_links_explicit_order(self):
        result = self.run_case(regs=[self.registration, dict(self.registration, id='alias')],
                               orders=[dict(self.order, registration_id='alias')])
        self.assertEqual(result['matches'][0]['registration_id'], 'r1')

    def test_conflicting_event_quarantined(self):
        result = self.run_case(regs=[self.registration, dict(self.registration, id='r2', ad_id='ad2')])
        self.assertEqual(result['registrations'], 0)
        self.assertEqual(result['conflicts']['event_ids'], ['event1'])
        self.assertEqual(result['matches'][0]['linkage'], 'unknown')

    def test_conflicting_order_quarantined(self):
        result = self.run_case(orders=[self.order, dict(self.order, product_gross='119')])
        self.assertEqual(result['orders'], 0)
        self.assertEqual(result['conflicts']['order_ids'], ['o1'])

    def test_multiple_ads_not_guessed(self):
        other = dict(self.registration, id='r2', event_id='e2', ad_id='ad2')
        result = self.run_case(regs=[self.registration, other])
        self.assertEqual(result['matches'][0]['match_basis'], 'ambiguous')
        self.assertIsNone(result['matches'][0]['ad_id'])

    def test_explicit_reference_resolves_multiple_registrations(self):
        other = dict(self.registration, id='r2', event_id='e2', ad_id='ad2')
        result = self.run_case(regs=[self.registration, other], orders=[dict(self.order, registration_id='r2')])
        self.assertEqual(result['matches'][0]['ad_id'], 'ad2')

    def test_explicit_other_person_cannot_link(self):
        result = self.run_case(orders=[dict(self.order, registration_id='r1', person_hmac='b' * 64)])
        self.assertEqual(result['matches'][0]['match_basis'], 'invalid_registration_reference')

    def test_view_through_separate(self):
        result = self.run_case(regs=[dict(self.registration, linkage='view_through')])
        self.assertEqual(result['matches'][0]['linkage'], 'view_through')
        self.assertEqual(result['cohorts'][0]['linkage'], 'view_through')

    def test_missing_cost_is_not_zero_or_verified(self):
        order = dict(self.order)
        del order['packaging_cost']
        result = self.run_case(orders=[order])
        match = result['matches'][0]
        self.assertEqual(match['missing_cost_fields'], ['packaging_cost'])
        self.assertEqual(match['contribution_status'], 'estimate')
        self.assertFalse(match['cost_coverage']['packaging_cost'])
        self.assertFalse(match['verified_profit'])

    def test_missing_refund_data_is_unknown(self):
        order = dict(self.order)
        del order['refund_tax']
        match = self.run_case(orders=[order])['matches'][0]
        self.assertIsNone(match['net_revenue_ex_tax'])
        self.assertIsNone(match['contribution_before_ads'])
        self.assertEqual(match['contribution_status'], 'unknown')
        cohort = self.run_case(orders=[order])['cohorts'][0]
        self.assertIsNone(cohort['contribution_before_ads'])
        self.assertEqual(cohort['contribution_status'], 'unknown')

    def test_conflicting_event_does_not_make_other_event_false_unique(self):
        regs = [self.registration, dict(self.registration, id='bad', ad_id='ad2'),
                dict(self.registration, id='r3', event_id='event3', ad_id='ad3')]
        match = self.run_case(regs=regs)['matches'][0]
        self.assertEqual(match['match_basis'], 'ambiguous')
        self.assertIsNone(match['ad_id'])

    def test_refunds_deduct_gross_and_return_tax(self):
        order = dict(self.order, refund_product_gross='59', refund_tax='9')
        match = self.run_case(orders=[order])['matches'][0]
        self.assertEqual(match['net_revenue_ex_tax'], '70.00')
        self.assertEqual(match['contribution_before_ads'], '29.00')

    def test_currency_and_tenant_mismatch_rejected(self):
        for field, value in [('tenant', 'other'), ('currency', 'USD'), ('shop', 'other'), ('account', '999')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.run_case(orders=[dict(self.order, **{field: value})])

    def test_source_declaration_does_not_authenticate(self):
        result = self.run_case(regs=[dict(self.registration, source_id='arbitrary')])
        self.assertEqual(result['registrations'], 0)
        self.assertEqual(result['declared_untrusted_records_excluded'], 1)

    def test_false_source_claim_excluded(self):
        result = self.run_case(regs=[dict(self.registration, source_verified=False)])
        self.assertEqual(result['registrations'], 0)

    def test_invalid_amounts_and_raw_pii_rejected(self):
        for extra in [dict(product_cost=True), dict(product_cost='NaN'), dict(product_cost='-1'),
                      dict(email='person@example.invalid'), dict(refund_product_gross='999')]:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.run_case(orders=[dict(self.order, **extra)])

    def test_future_and_naive_timestamps_rejected(self):
        for date in ['2026-10-18T10:00:00+03:00', '2026-10-17T10:00:00']:
            with self.subTest(date=date), self.assertRaises(ValueError):
                self.run_case(orders=[dict(self.order, ordered_at=date)])

    def test_registration_after_order_not_linked(self):
        result = self.run_case(regs=[dict(self.registration, registered_at='2026-10-17T10:30:00+03:00')])
        self.assertEqual(result['matches'][0]['match_basis'], 'unmatched')

    def test_hmac_is_tenant_isolated_and_deterministic(self):
        first = person_hmac('one', b'x' * 32, 'identity')
        self.assertEqual(first, person_hmac('one', b'x' * 32, 'identity'))
        self.assertNotEqual(first, person_hmac('two', b'x' * 32, 'identity'))
        with self.assertRaises(ValueError):
            person_hmac('one', b'weak', 'identity')


if __name__ == '__main__':
    unittest.main()
