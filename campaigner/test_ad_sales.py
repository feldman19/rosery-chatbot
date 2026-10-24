import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from ad_sales import entity_sales, read_sales
from connections import SHOP
import test_server


class AdSalesTests(unittest.TestCase):
    def aggregate(self, orders, **changes):
        source = dict(complete=True, currency='ILS', timezone='Asia/Jerusalem',
                      since='2026-10-10', until='2026-10-23', orders=orders)
        source.update(changes)
        rows = [dict(id='1', name='Ad', spend=10, purchases=3)]
        entity_sales(source, rows, '2026-10-10', '2026-10-23', 'ILS', 'Asia/Jerusalem')
        return rows[0]['ad_sales']

    def order(self, new=True, tag='1', cost=20):
        return dict(day='2026-10-20', new=new, ad_tag=tag, economics=dict(
            net_sales=118, taxes=0, shipping_charges=0, cost_of_goods_sold=cost,
            net_sales_with_cost_recorded=118, net_sales_without_cost_recorded=0, net_items_sold=1))

    def test_new_returning_profit(self):
        out = self.aggregate([self.order(), self.order(False)])
        self.assertEqual(out['store_orders'], 2)
        self.assertEqual(out['new_customer_percent'], 50)
        self.assertEqual(out['profit_after_product_and_ads'], 150)
        self.assertEqual(out['meta_minus_store_orders'], 1)

    def test_missing_utm_name_fallback(self):
        self.assertEqual(self.aggregate([self.order(tag=None), self.order(tag='Ad')])['store_orders'], 1)

    def test_unknown_cost_customer(self):
        out = self.aggregate([self.order(new=None, cost=None)])
        self.assertIsNone(out['profit_after_product_and_ads'])
        self.assertIsNone(out['new_customer_percent'])

    def test_partial_and_currency_unknown(self):
        self.assertIsNone(self.aggregate([], complete=False)['store_orders'])
        self.assertIsNone(self.aggregate([], currency='USD')['store_orders'])

    def test_partial_cost_unknown(self):
        order = self.order()
        order['economics']['net_sales_without_cost_recorded'] = 50
        self.assertIsNone(self.aggregate([order])['profit_after_product_and_ads'])

    @patch('ad_sales.get_token', return_value='synthetic')
    @patch('ad_sales.graph')
    def test_reader(self, graph, token):
        identity = dict(shop=dict(myshopifyDomain=SHOP, currencyCode='ILS', ianaTimezone='Asia/Jerusalem'),
                        currentAppInstallation=dict(accessScopes=[dict(handle='read_orders')]))
        node = dict(id='gid://shopify/Order/1', createdAt='2026-10-20T10:00:00Z', currencyCode='ILS',
                    test=False, cancelledAt=None, displayFinancialStatus='PAID', customerJourneySummary=dict(
                        ready=True, customerOrderIndex=1, lastVisit=dict(landingPage='https://example.com/?adset_id=s&private=secret',
                            utmParameters=dict(source='facebook', campaign='c', content='1'))))
        graph.side_effect = [identity, dict(orders=dict(nodes=[node], pageInfo=dict(hasNextPage=False)))]
        out = read_sales('synthetic', datetime(2026, 10, 24, 10, tzinfo=timezone.utc))
        self.assertTrue(out['complete'])
        self.assertTrue(out['orders'][0]['new'])
        self.assertEqual(out['orders'][0]['adset_tag'], 's')
        self.assertNotIn('secret', str(out))
        self.assertEqual(out['missing_scopes'], ['read_reports'])
        self.assertIn('+03:00', graph.call_args.args[2]['query'])

    @patch('ad_sales.get_token', return_value='synthetic')
    @patch('ad_sales.graph')
    def test_missing_scope(self, graph, token):
        graph.return_value = dict(shop=dict(myshopifyDomain=SHOP), currentAppInstallation=dict(accessScopes=[]))
        self.assertEqual(read_sales('synthetic')['reason'], 'read_orders_missing')
        self.assertEqual(graph.call_count, 1)

    @patch('ad_sales.get_token', side_effect=ValueError('secret'))
    def test_error_redacted(self, token):
        self.assertNotIn('secret', str(read_sales('synthetic')))


class AdSalesEndpointTests(unittest.TestCase):
    setUp = test_server.ServerTests.setUp
    tearDown = test_server.ServerTests.tearDown
    request = test_server.ServerTests.request

    def test_owner_endpoint_session_readonly(self):
        with patch('ad_sales.read_sales', return_value=dict(status='unavailable', complete=False)) as reader:
            self.assertEqual(self.request('/api/ad-sales', token=False)[0], 403)
            reader.assert_not_called()
            code, out = self.request('/api/ad-sales')
            self.assertEqual(code, 200)
            self.assertFalse(out['live_action_allowed'])
            reader.assert_called_once()
            self.assertEqual(self.request('/api/ad-sales', {})[0], 404)
