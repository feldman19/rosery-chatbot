import json
from pathlib import Path
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import shopify_direct
import true_profit
from server import make_server

ROW = {'orders': '100', 'net_sales': '11800', 'taxes': '0', 'shipping_charges': '1180', 'cost_of_goods_sold': '2000',
       'net_sales_with_cost_recorded': '11800', 'net_sales_without_cost_recorded': '0', 'net_items_sold': '150'}
SIMPLE = dict(true_profit.DEFAULTS, fields=[])


def detailed(*fields):
    return dict(true_profit.DEFAULTS, mode='detailed', fields=[dict(name=n, type=t, amount=a, vat_included=v) for n, t, a, v in fields])


class WindowProfitTests(unittest.TestCase):
    def test_simple_removes_vat_and_subtracts_cogs(self):
        w = true_profit.window_profit(ROW, 7, 2000, SIMPLE)
        self.assertEqual((w['revenue_ex_vat'], w['cogs_ex_vat'], w['contribution']), (10000, 2000, 8000))
        self.assertEqual((w['contribution_per_shekel'], w['after_ads'], w['gross_margin']), (4.0, 6000, 0.8))
        self.assertEqual(w['label'], 'תרומה אחרי עלויות')
        self.assertIsNone(w['net_profit'])

    def test_recorded_tax_means_sales_already_exclude_vat(self):
        w = true_profit.window_profit({**ROW, 'taxes': '1800', 'net_sales_with_cost_recorded': '10000', 'net_sales': '10000'}, 7, 2000, SIMPLE)
        self.assertEqual(w['revenue_ex_vat'], 10000)

    def test_sales_without_cost_are_left_out_not_zero_cost(self):
        w = true_profit.window_profit({**ROW, 'net_sales_with_cost_recorded': '9440', 'net_sales_without_cost_recorded': '2360'}, 7, 2000, SIMPLE)
        self.assertEqual((w['revenue_with_cost_ex_vat'], w['revenue_without_cost_ex_vat'], w['contribution']), (8000, 2000, 6000))
        self.assertEqual(w['cost_coverage'], 0.8)

    def test_coverage_split_is_applied_to_net_sales_after_returns(self):
        # Shopify's split (12390 + 0) is before returns; net sales after returns are 11800.
        w = true_profit.window_profit({**ROW, 'net_sales_with_cost_recorded': '12390'}, 7, 2000, SIMPLE)
        self.assertEqual((w['revenue_with_cost_ex_vat'], w['contribution']), (10000, 8000))
        self.assertLessEqual(w['contribution'], w['revenue_ex_vat'] - w['cogs_ex_vat'])

    def test_missing_cogs_or_spend_is_reported(self):
        w = true_profit.window_profit({**ROW, 'cost_of_goods_sold': None}, 7, None, SIMPLE)
        self.assertIsNone(w['contribution'])
        self.assertEqual(len(w['missing']), 2)

    def test_detailed_fields_and_net_profit(self):
        settings = detailed(('משלוח', 'per_order', 23.6, True), ('סליקה', 'percent_of_revenue', 2, False),
                            ('אריזה', 'per_unit', 4, False), ('שכירות', 'monthly_fixed', 3000, False))
        w = true_profit.window_profit(ROW, 30, 2000, settings)
        costs = {c['name']: c['cost'] for c in w['cost_lines']}
        self.assertEqual(costs, {'משלוח': 2000, 'סליקה': 200, 'אריזה': 600, 'שכירות': 3000})
        # 10000 sales - 2000 cogs + 1000 shipping collected - 2800 variable = 6200
        self.assertEqual((w['contribution'], w['after_ads'], w['net_profit']), (6200, 4200, 1200))
        # The label always names the contribution; net profit is a separate value.
        self.assertEqual(w['label'], 'תרומה אחרי עלויות')
        self.assertEqual(w['gross_margin'], 0.8)

    def test_cost_with_missing_basis_makes_the_result_unknown(self):
        w = true_profit.window_profit({**ROW, 'net_items_sold': None}, 7, 2000, detailed(('אריזה', 'per_unit', 4, False)))
        self.assertEqual((w['contribution'], w['after_ads'], w['contribution_per_shekel']), (None, None, None))
        self.assertIn('בסיס לחישוב "אריזה"', w['missing'])


class SettingsTests(unittest.TestCase):
    def test_validation_rejects_bad_shapes(self):
        for bad in ({}, dict(SIMPLE, mode='x'), dict(SIMPLE, vat_rate=2), dict(SIMPLE, extra=1),
                    dict(SIMPLE, fields=[{'name': '', 'type': 'per_order', 'amount': 1, 'vat_included': True}]),
                    dict(SIMPLE, fields=[{'name': 'a', 'type': 'percent_of_revenue', 'amount': 150, 'vat_included': False}]),
                    dict(SIMPLE, fields=[{'name': 'a', 'type': 'per_order', 'amount': -1, 'vat_included': False}])):
            with self.assertRaises(ValueError):
                true_profit.validate_settings(bad)

    def test_save_and_load_round_trip_and_bad_file_falls_back(self):
        with tempfile.TemporaryDirectory() as temp:
            store = true_profit.ProfitSettings(Path(temp) / 'profit-settings.json')
            self.assertEqual(store.load()['mode'], 'simple')
            store.save(detailed(('משלוח', 'per_order', 20, True)))
            self.assertEqual(store.load()['fields'][0]['name'], 'משלוח')
            (Path(temp) / 'profit-settings.json').write_text('not json', encoding='utf-8')
            self.assertEqual(store.load()['mode'], 'simple')

    def test_products_ranked_by_gross_profit(self):
        rows = [{'product_title': 'A', 'net_sales_with_cost_recorded': '1180', 'net_sales_without_cost_recorded': '0',
                 'cost_of_goods_sold': '100', 'net_items_sold': '5'},
                {'product_title': 'B', 'net_sales_with_cost_recorded': '11800', 'net_sales_without_cost_recorded': '0',
                 'cost_of_goods_sold': '5000', 'net_items_sold': '9'},
                {'product_title': 'no cost', 'net_sales_with_cost_recorded': '0', 'net_sales_without_cost_recorded': '0',
                 'cost_of_goods_sold': None}]
        result = true_profit.products(rows, SIMPLE)
        self.assertEqual([p['product'] for p in result], ['B', 'A'])
        self.assertEqual(result[1]['gross_margin'], 0.9)

    def test_product_with_unknown_coverage_is_not_ranked(self):
        rows = [{'product_title': 'A', 'net_sales': '1180', 'net_sales_with_cost_recorded': '1180', 'cost_of_goods_sold': '100'}]
        self.assertEqual(true_profit.products(rows, SIMPLE), [])

    def test_summary_unavailable_without_shopify_profit(self):
        self.assertEqual(true_profit.summary({}, {}, SIMPLE)['status'], 'unavailable')

    def test_spend_from_a_different_period_is_not_used(self):
        from datetime import date
        shop = {'profit': {'checked_at': '2026-10-23T09:00:00+00:00', 'windows': {'7': dict(ROW), '90': dict(ROW)},
                           'products_90': []}}
        result = true_profit.summary(shop, {'7': (2000, date(2026, 10, 22)), '90': (9000, date(2026, 10, 20))}, SIMPLE)
        self.assertEqual(result['windows']['7']['contribution_per_shekel'], 4.0)
        self.assertIsNone(result['windows']['90']['contribution_per_shekel'])
        self.assertIn('לא מאותה תקופה', result['windows']['90']['missing'][-1])


class ShopifyProfitReadTests(unittest.TestCase):
    def test_fixed_aggregate_queries_only(self):
        seen = []
        def fake_graph(token, query, variables=None):
            seen.append(variables['q'])
            rows = [{'product_title': 'A', 'net_sales': '1'}] if 'GROUP BY' in variables['q'] else [dict(ROW)]
            return {'shopifyqlQuery': {'tableData': {'columns': [], 'rows': rows}, 'parseErrors': []}}
        with patch.object(shopify_direct, 'graph', fake_graph):
            result = shopify_direct.read_profit_tables('t')
        self.assertEqual(set(result['windows']), {'7', '90'})
        self.assertEqual(len(seen), 3)
        self.assertTrue(all(q.startswith('FROM sales SHOW') and 'customer' not in q for q in seen))

    def test_profit_failure_does_not_break_main_report(self):
        with patch.object(shopify_direct, 'read_profit_tables', side_effect=ValueError('x')), \
                patch.object(shopify_direct, 'get_token', return_value='t'), \
                patch.object(shopify_direct, 'graph', side_effect=[
                    {'shop': {'myshopifyDomain': shopify_direct.SHOP, 'currencyCode': 'ILS'}},
                    {'shopifyqlQuery': {'tableData': {'columns': [], 'rows': [{'day': '2026-10-20'}]}, 'parseErrors': []}},
                    {'shopifyqlQuery': {'tableData': None, 'parseErrors': []}}]):
            result = shopify_direct.read_reports('unused')
        self.assertTrue(result['verified'])
        self.assertIsNone(result['profit'])


class ProfitHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='campaigner-test-', dir=Path(__file__).parent)
        self.server = make_server(0, self.temp.name, workspace='manager')
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.page = urllib.request.urlopen(self.base).read().decode()
        self.token = re.search("const token='([^']+)'", self.page).group(1)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def request(self, path, body=None, token=True):
        headers = {'X-Local-Session': self.token if token else 'bad'}
        if body is not None:
            headers.update({'Content-Type': 'application/json', 'Origin': self.base})
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
        try:
            with urllib.request.urlopen(req) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def test_profit_routes(self):
        self.assertEqual(self.request('/api/profit', token=False)[0], 403)
        code, value = self.request('/api/profit')
        self.assertEqual((code, value['status']), (200, 'unavailable'))
        self.assertEqual(self.request('/api/profit/settings', dict(SIMPLE, mode='bad'))[0], 400)
        code, value = self.request('/api/profit/settings', detailed(('משלוח', 'per_order', 20, True)))
        self.assertEqual((code, value['settings']['mode']), (200, 'detailed'))
        self.assertTrue(Path(self.temp.name, 'profit-settings.json').exists())

    def test_screen_has_profit_section(self):
        self.assertIn('רווחיות אמיתית', self.page)
        self.assertIn('מפורט: עם ההוצאות שלי', self.page)


if __name__ == '__main__':
    unittest.main()
