import copy
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import shopify_orders as reader
from cohort import person_hmac, HostContext, reconcile_cohort


class ShopifyOrdersTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
        self.secret = b'x' * 32
        self.identity = {'shop': {'myshopifyDomain': reader.SHOP, 'currencyCode': 'ILS'},
                         'currentAppInstallation': {'accessScopes': [{'handle': 'read_reports'}, {'handle': 'read_orders'}]}}
        money = lambda amount: {'shopMoney': {'amount': amount, 'currencyCode': 'ILS'}}
        self.order = {'id': 'gid://shopify/Order/1', 'createdAt': '2026-10-16T10:00:00Z',
                      'currencyCode': 'ILS', 'email': ' Person@Example.Invalid ', 'test': False,
                      'cancelledAt': None, 'sourceName': 'web', 'displayFinancialStatus': 'PAID',
                      'taxesIncluded': True, 'subtotalPriceSet': money('118'), 'totalTaxSet': money('18'),
                      'totalPriceSet': money('138'), 'totalRefundedSet': money('0')}

    def page(self, orders=None, more=False, cursor=None):
        return {'orders': {'nodes': [copy.deepcopy(self.order)] if orders is None else orders,
                           'pageInfo': {'hasNextPage': more, 'endCursor': cursor}}}

    def run_read(self, responses=None, identity=None, **kwargs):
        replies = [identity or self.identity] + ([self.page()] if responses is None else responses)
        with patch.object(reader, 'get_token', return_value='private-token'), patch.object(reader, 'graph', side_effect=replies) as graph:
            result = reader.read_orders('unused', self.secret, self.now, **kwargs)
            return result, graph

    def test_identity_scopes_and_fixed_bounded_query(self):
        result, graph = self.run_read()
        self.assertTrue(result['coverage']['complete'])
        self.assertEqual(result['binding']['scopes'], ['read_orders', 'read_reports'])
        self.assertEqual(graph.call_args_list[1].args[1], reader.QUERY)
        query = graph.call_args_list[1].args[2]['query']
        self.assertIn('2026-08-18', query)
        self.assertNotIn('mutation', reader.QUERY)
        self.assertNotIn('shippingAddress', reader.QUERY)

    def test_raw_identity_never_returned_or_stored(self):
        result, _ = self.run_read()
        output = json.dumps(result).casefold()
        self.assertNotIn('person@example.invalid', output)
        self.assertNotIn('private-token', output)
        self.assertNotIn('"email"', output)
        expected = person_hmac(reader.TENANT, self.secret, 'person@example.invalid')
        self.assertEqual(result['orders'][0]['person_hmac'], expected)

    def test_missing_costs_shipping_and_refund_split_not_invented(self):
        result, _ = self.run_read()
        order = result['orders'][0]
        self.assertIsNone(order['shipping_gross'])
        self.assertIsNone(order['product_cost'])
        self.assertIsNone(order['packaging_cost'])
        self.assertEqual(order['refund_tax'], '0')
        self.assertFalse(order['verified_profit'])

    def test_refunded_aggregates_do_not_become_guessed_components(self):
        order = copy.deepcopy(self.order)
        order['totalRefundedSet']['shopMoney']['amount'] = '59'
        order['displayFinancialStatus'] = 'PARTIALLY_REFUNDED'
        result, _ = self.run_read([self.page([order])])
        row = result['orders'][0]
        self.assertEqual(row['provider_totals']['total_refunded'], '59')
        for key in ['refund_product_gross', 'refund_shipping_gross', 'refund_tax']:
            self.assertIsNone(row[key])

    def test_tax_excluded_product_gross_unknown(self):
        order = dict(self.order, taxesIncluded=False)
        result, _ = self.run_read([self.page([order])])
        self.assertIsNone(result['orders'][0]['product_gross'])

    def test_pagination_reads_all_and_deduplicates(self):
        second = dict(self.order, id='gid://shopify/Order/2')
        result, graph = self.run_read([self.page(more=True, cursor='cursor1'), self.page([self.order, second])])
        self.assertEqual(len(result['orders']), 2)
        self.assertEqual(result['coverage']['pages_read'], 2)
        self.assertEqual(graph.call_args_list[2].args[2]['after'], 'cursor1')

    def test_repeated_cursor_is_explicit_incomplete(self):
        result, _ = self.run_read([self.page(more=True, cursor='cursor1'), self.page(more=True, cursor='cursor1')])
        self.assertFalse(result['coverage']['complete'])
        self.assertEqual(result['coverage']['error'], 'pagination_cursor_invalid')

    def test_page_cap_marks_partial_not_complete(self):
        with patch.object(reader, 'MAX_PAGES', 1):
            result, _ = self.run_read([self.page(more=True, cursor='next')])
        self.assertFalse(result['coverage']['complete'])
        self.assertEqual(result['coverage']['error'], 'orders_page_limit_reached')

    def test_provider_error_preserves_safe_partial_and_not_error_text(self):
        result, _ = self.run_read([self.page(more=True, cursor='next'), RuntimeError('secret email@example.invalid')])
        self.assertEqual(len(result['orders']), 1)
        self.assertFalse(result['coverage']['complete'])
        self.assertNotIn('email@example.invalid', json.dumps(result))

    def test_scope_and_shop_checked_before_any_order_read(self):
        for identity in [dict(self.identity, shop={'myshopifyDomain': 'other', 'currencyCode': 'ILS'}),
                         dict(self.identity, currentAppInstallation={'accessScopes': [{'handle': 'read_reports'}]}),
                         dict(self.identity, currentAppInstallation={'accessScopes': [{'handle': 'read_reports'}, {'handle': 'read_orders'}, {'handle': 'write_orders'}]})]:
            with self.subTest(identity=identity), patch.object(reader, 'get_token', return_value='t'), patch.object(reader, 'graph', return_value=identity) as graph:
                with self.assertRaises(reader.OrderReadError):
                    reader.read_orders('unused', self.secret, self.now)
                self.assertEqual(graph.call_count, 1)

    def test_currency_mismatch_is_incomplete_not_conversion(self):
        result, _ = self.run_read([self.page([dict(self.order, currencyCode='USD')])])
        self.assertFalse(result['coverage']['complete'])
        self.assertEqual(result['orders'], [])

    def test_nested_money_currency_checked(self):
        order = copy.deepcopy(self.order)
        order['totalPriceSet']['shopMoney']['currencyCode'] = 'USD'
        result, _ = self.run_read([self.page([order])])
        self.assertFalse(result['coverage']['complete'])

    def test_test_cancelled_known_exchange_and_unpaid_excluded(self):
        orders = [dict(self.order, test=True), dict(self.order, id='gid://shopify/Order/2', cancelledAt='2026-10-16T11:00:00Z'),
                  dict(self.order, id='gid://shopify/Order/3'), dict(self.order, id='gid://shopify/Order/4', displayFinancialStatus='PENDING')]
        result, _ = self.run_read([self.page(orders)], known_exchange_order_ids=frozenset({'gid://shopify/Order/3'}))
        self.assertEqual(result['orders'], [])
        self.assertTrue(result['coverage']['complete'])
        self.assertEqual({x['reason'] for x in result['excluded']}, {'provider_test_order', 'cancelled_order', 'host_confirmed_exchange_fee', 'payment_not_confirmed'})

    def test_other_channel_not_assumed_exchange_or_discarded(self):
        result, _ = self.run_read([self.page([dict(self.order, sourceName='shopify_draft_order')])])
        self.assertEqual(result['orders'][0]['sales_channel_classification'], 'other_or_unknown')
        self.assertEqual(result['orders'][0]['exchange_classification'], 'unknown')

    def test_missing_identity_explicit_not_fabricated(self):
        result, _ = self.run_read([self.page([dict(self.order, email=None)])])
        self.assertEqual(result['orders'], [])
        self.assertEqual(result['excluded'][0]['reason'], 'identity_unavailable')

    def test_conflicting_page_versions_quarantined(self):
        changed = copy.deepcopy(self.order)
        changed['totalPriceSet']['shopMoney']['amount'] = '139'
        result, _ = self.run_read([self.page(more=True, cursor='next'), self.page([changed])])
        self.assertEqual(result['orders'], [])
        self.assertFalse(result['coverage']['complete'])

    def test_changed_excluded_status_also_quarantines_old_paid_version(self):
        result, _ = self.run_read([self.page(more=True, cursor='next'), self.page([dict(self.order, test=True)])])
        self.assertEqual(result['orders'], [])
        self.assertFalse(result['coverage']['complete'])

    def test_global_time_budget_stops_without_waiting_all_pages(self):
        with patch.object(reader.time, 'monotonic', side_effect=[0, 0, 61]):
            result, graph = self.run_read([self.page(more=True, cursor='next')])
        self.assertFalse(result['coverage']['complete'])
        self.assertEqual(result['coverage']['error'], 'orders_time_budget_exceeded')
        self.assertEqual(graph.call_count, 2)

    def test_result_is_accepted_by_cohort_without_false_profit(self):
        result, _ = self.run_read()
        binding = result['binding']
        context = HostContext(binding['tenant'], binding['shop'], binding['account'], binding['currency'],
                              binding['as_of'], frozenset({binding['source_id']}))
        reconciled = reconcile_cohort([], result['orders'], context=context)
        self.assertEqual(reconciled['orders'], 1)
        self.assertEqual(reconciled['matches'][0]['contribution_status'], 'unknown')

    def test_explicit_pilot_window_and_invalid_bounds(self):
        result, graph = self.run_read(window_days=28)
        self.assertIn('2026-09-19', graph.call_args_list[1].args[2]['query'])
        self.assertIn('28 days', result['limits'][0])
        for days in (0,61,True,'28'):
            with patch.object(reader,'get_token') as token:
                with self.assertRaises(reader.OrderReadError):
                    reader.read_orders('unused',self.secret,self.now,window_days=days)
                token.assert_not_called()

    def test_invalid_secret_rejected_before_network(self):
        with patch.object(reader, 'get_token') as token:
            with self.assertRaises(reader.OrderReadError):
                reader.read_orders('unused', b'weak', self.now)
            token.assert_not_called()


if __name__ == '__main__':
    unittest.main()
