import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone

from campaign_operations_transport import ProviderRejected
from experiments import Arm, Plan
from meta_experiment import (META, SpecRefused, approval_preview, build_spec, daily_rows, interpret, study_path,
                             study_payload)
from meta_experiment_actions import ExperimentActions, ExperimentBlocked, MetaStudyTransport, spec_from_dict, spec_to_dict
from meta_experiment_check import GraphGet, check
from rulebook import load_rulebook

BUSINESS, CAMPAIGN, A, B, STUDY = '111111111', '222222222', '333333333', '444444444', '555555555'
NOW = datetime(2026, 10, 24, 9, 0, tzinfo=timezone.utc)
JARGON = ('A/B', 'split', 'CPA', 'ROAS', 'CTR', 'ad set', 'אד-סט', 'ספליט')


def plan(**kw):
    values = dict(question='מה מוכר יותר: סרטוני חולצות או סרטוני שרשראות?', variable='סוג המוצר בסרטון',
                  a=Arm('סרטוני חולצות', 'shirt', [f'a{i}' for i in range(5)]),
                  b=Arm('סרטוני שרשראות', 'necklace', [f'b{i}' for i in range(5)]),
                  daily_budget_minor=10000, start_date='2026-11-01')
    values.update(kw)
    return Plan(**values)


def spec(**kw):
    return build_spec(plan(**kw), experiment_id='exp1', rulebook=load_rulebook(), business_id=BUSINESS,
                      campaign_id=CAMPAIGN, adset_ids=(A, B))


def meta_time(stamp):
    return datetime.fromtimestamp(stamp, timezone(timedelta(hours=2))).strftime('%Y-%m-%dT%H:%M:%S%z')


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.adsets = {x: {'id': x, 'name': x, 'status': 'PAUSED', 'campaign_id': CAMPAIGN, 'daily_budget': '10000',
                           'ads': {'data': [{'id': str(i)} for i in range(5)]}} for x in (A, B)}
        self.study = None
        self.fail_update = None
        self.reject_create = False
        self.bad_readback = False
        self.rows = {}

    def read(self, token, node, fields):
        self.calls.append(('GET', node, fields))
        if node in self.adsets:
            return json.loads(json.dumps(self.adsets[node]))
        if node == STUDY:
            if self.study is None:
                raise ProviderRejected(details={'code': 100})
            return dict(self.study)
        raise ProviderRejected(details={'code': 803})

    def update(self, token, node, params):
        self.calls.append(('POST', node, params))
        if self.fail_update == node:
            raise ProviderRejected(details={'code': 100, 'message': 'no'})
        self.adsets[node]['status'] = params['status']
        return {'success': True}

    def create_study(self, token, business_id, payload):
        self.calls.append(('POST', business_id + '/ad_studies', payload))
        if self.reject_create:
            raise ProviderRejected(details={'code': 10, 'message': 'needs business_management'})
        self.study = {'id': STUDY, 'type': payload['type'], 'start_time': meta_time(payload['start_time']),
                      'end_time': meta_time(payload['end_time'] + (3600 if self.bad_readback else 0)),
                      'cells': {'data': [{'name': c['name'], 'treatment_percentage': c['treatment_percentage'],
                                          'ad_entities_count': len(c['adsets'])} for c in payload['cells']]}}
        return {'id': STUDY}

    def delete_study(self, token, study_id):
        self.calls.append(('DELETE', study_id, None))
        self.study = None
        return {'success': True}

    def insights(self, token, adset_id, since, until):
        self.calls.append(('GET', adset_id + '/insights', (since, until)))
        return self.rows.get(adset_id)


def rows(start, days, spend, purchases_per_day):
    first = date.fromisoformat(start)
    out = []
    for i in range(days):
        day = (first + timedelta(days=i)).isoformat()
        out.append({'date_start': day, 'date_stop': day, 'spend': str(spend),
                    'actions': [{'action_type': 'link_click', 'value': '30'},
                                {'action_type': 'offsite_conversion.fb_pixel_purchase', 'value': str(purchases_per_day(i))},
                                {'action_type': 'omni_purchase', 'value': str(purchases_per_day(i))},
                                {'action_type': 'offsite_conversion.fb_pixel_initiate_checkout', 'value': '2'}]})
    return out


class SpecTests(unittest.TestCase):
    def test_spec_uses_owner_rules_and_exact_payload(self):
        s = spec()
        self.assertEqual(s.success['primary'], 'purchases_per_ils')
        self.assertEqual(s.success['breakeven_cpa_ils'], 190)
        self.assertIn('ads_per_adset', [r['rule_id'] for r in s.rules])
        self.assertEqual(study_path(s), BUSINESS + '/ad_studies')
        start = datetime(2026, 11, 1, tzinfo=timezone(timedelta(hours=2)))  # Israel winter time
        self.assertEqual(study_payload(s), {
            'name': 'ROSERY ניסוי exp1 | סוג המוצר בסרטון', 'description': plan().question, 'type': 'SPLIT_TEST',
            'start_time': int(start.timestamp()), 'end_time': int((start + timedelta(days=21)).timestamp()),
            'cells': [{'name': 'A סרטוני חולצות', 'treatment_percentage': 50, 'adsets': [A]},
                      {'name': 'B סרטוני שרשראות', 'treatment_percentage': 50, 'adsets': [B]}]})

    def test_refusals(self):
        cases = [dict(max_days=20), dict(max_days=35, min_days=7), dict(min_days=3), dict(daily_budget_minor=2000)]
        for kw in cases:
            with self.subTest(kw=kw), self.assertRaises(SpecRefused):
                spec(**kw)
        with self.assertRaises(SpecRefused):
            build_spec(plan(), experiment_id='exp1', rulebook={'status': 'unavailable'}, business_id=BUSINESS,
                       campaign_id=CAMPAIGN, adset_ids=(A, B))
        for ids in [(A, A), (A,), ('x', B)]:
            with self.subTest(ids=ids), self.assertRaises(SpecRefused):
                build_spec(plan(), experiment_id='exp1', rulebook=load_rulebook(), business_id=BUSINESS,
                           campaign_id=CAMPAIGN, adset_ids=ids)
        with self.assertRaises(SpecRefused):
            build_spec(plan().transition('prepared'), experiment_id='exp1', rulebook=load_rulebook(),
                       business_id=BUSINESS, campaign_id=CAMPAIGN, adset_ids=(A, B))

    def test_low_budget_warns_about_purchases(self):
        self.assertTrue(any('5 רכישות' in w for w in spec(daily_budget_minor=3000).warnings))
        self.assertEqual(spec(daily_budget_minor=50000).warnings, ())

    def test_preview_is_plain_hebrew_with_cost_and_dates(self):
        text = approval_preview(spec())
        blob = json.dumps(text, ensure_ascii=False)
        self.assertIn('₪100 ליום לכל גרסה', text['cost'])
        self.assertIn('₪4,200', text['cost'])
        self.assertIn('לא יוצא כסף', text['cost'])
        self.assertIn('21 ימים, מ־01/11/2026 עד 21/11/2026', text['duration'])
        self.assertTrue(all('from' in c and 'to' in c for c in text['changes']))
        for word in JARGON:
            self.assertNotIn(word, blob)
        self.assertEqual([c['to'] for c in approval_preview(spec(), 'activate')['changes']], ['פעילה', 'פעילה'])

    def test_spec_round_trip(self):
        s = spec()
        self.assertEqual(spec_from_dict(json.loads(json.dumps(spec_to_dict(s)))), s)


class ResultTests(unittest.TestCase):
    def daily(self, a, b, days=21):
        return {k: daily_rows(rows('2026-11-01', days, 100, f), '2026-11-01', '2026-11-30') for k, f in (('A', a), ('B', b))}

    def test_daily_rows_keep_unknown_unknown(self):
        good = rows('2026-11-01', 1, 100, lambda i: 1)
        self.assertEqual(daily_rows(good, '2026-11-01', '2026-11-01'),
                         [{'date': '2026-11-01', 'spend_minor': 10000, 'purchases': 1, 'checkouts': 2}])
        self.assertIsNone(daily_rows(None, '2026-11-01', '2026-11-01'))
        self.assertIsNone(daily_rows([{**good[0], 'spend': None}], '2026-11-01', '2026-11-01'))
        self.assertIsNone(daily_rows([{k: v for k, v in good[0].items() if k != 'actions'}], '2026-11-01', '2026-11-01'))
        self.assertEqual(daily_rows([{**good[0], 'actions': [{'action_type': 'link_click', 'value': '3'}]}], '2026-11-01', '2026-11-01')[0]['purchases'], 0)

    def test_clear_difference_is_decided_by_engine(self):
        out = interpret(spec(), self.daily(lambda i: 0 if i % 3 else 1, lambda i: 2), '2026-11-21')
        self.assertEqual((out['state'], out['winner'], out['decided_by']), ('decided', 'B', 'engine'))
        self.assertIn('גרסה ב', out['text'])

    def test_noise_is_never_a_winner(self):
        out = interpret(spec(), self.daily(lambda i: 1, lambda i: 1), '2026-11-21')
        self.assertEqual((out['state'], out['winner']), ('inconclusive', None))
        self.assertIn('לא הוכרע', out['text'])

    def test_meta_confidence_below_bar_is_not_enough(self):
        out = interpret(spec(), self.daily(lambda i: 1, lambda i: 1), '2026-11-21', {'winner': 'B', 'confidence': 0.8})
        self.assertIsNone(out['winner'])
        self.assertIn('80%', out['text'])

    def test_meta_confident_winner_needs_enough_data(self):
        few = self.daily(lambda i: 0, lambda i: 1 if i < 4 else 0, days=7)
        out = interpret(spec(), few, '2026-11-07', {'winner': 'B', 'confidence': 0.99})
        self.assertIsNone(out['winner'])
        more = self.daily(lambda i: 1 if i % 2 else 0, lambda i: 1, days=10)
        out = interpret(spec(), more, '2026-11-10', {'winner': 'B', 'confidence': 0.99})
        self.assertEqual((out['winner'], out['decided_by']), ('B', 'meta'))

    def test_disagreement_is_inconclusive(self):
        out = interpret(spec(), self.daily(lambda i: 0 if i % 3 else 1, lambda i: 2), '2026-11-21', {'winner': 'A', 'confidence': 0.99})
        self.assertEqual((out['state'], out['winner']), ('inconclusive', None))

    def test_missing_day_is_unknown(self):
        daily = self.daily(lambda i: 1, lambda i: 2)
        daily['A'] = daily['A'][1:]
        out = interpret(spec(), daily, '2026-11-21')
        self.assertEqual((out['state'], out['winner']), ('unknown', None))


class ActionTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.fake = FakeTransport()
        self.now = NOW
        self.actions = ExperimentActions(self.dir.name, transport=self.fake, credentials=lambda: 'token', clock=lambda: self.now)
        self.actions.register(spec())

    def tearDown(self):
        self.dir.cleanup()

    def writes(self):
        return [c for c in self.fake.calls if c[0] != 'GET']

    def test_full_flow_with_fake_transport(self):
        preview = self.actions.preview('create:exp1')
        self.assertEqual(self.writes(), [])
        self.assertIn('₪4,200', preview['owner_text']['cost'])
        receipt = self.actions.confirm(preview['confirm_token'])
        self.assertEqual(receipt['state'], 'verified')
        self.assertEqual(self.writes(), [('POST', BUSINESS + '/ad_studies', study_payload(spec()))])
        self.assertEqual(self.actions.spec('exp1').plan.state, 'prepared')
        self.assertEqual(self.actions.spec('exp1').plan.meta_study_id, STUDY)
        with self.assertRaises(ExperimentBlocked):
            self.actions.confirm(preview['confirm_token'])  # single use
        receipt = self.actions.confirm(self.actions.preview('activate:exp1')['confirm_token'])
        self.assertEqual(receipt['state'], 'verified')
        self.assertEqual({x['status'] for x in self.fake.adsets.values()}, {'ACTIVE'})
        self.assertEqual(self.actions.spec('exp1').plan.state, 'running')
        self.now = datetime(2026, 11, 22, 9, 0, tzinfo=timezone.utc)
        self.fake.rows = {A: rows('2026-11-01', 21, 100, lambda i: 0 if i % 3 else 1), B: rows('2026-11-01', 21, 100, lambda i: 2)}
        status = self.actions.status('exp1')
        self.assertEqual((status['as_of'], status['winner'], status['days_left']), ('2026-11-21', 'B', 0))
        receipt = self.actions.confirm(self.actions.preview('stop:exp1')['confirm_token'])
        self.assertEqual(receipt['state'], 'verified')
        self.assertEqual(self.actions.spec('exp1').plan.state, 'interrupted')
        self.assertEqual({x['status'] for x in self.fake.adsets.values()}, {'PAUSED'})

    def test_changed_state_blocks_before_write(self):
        preview = self.actions.preview('create:exp1')
        self.fake.adsets[A]['daily_budget'] = '20000'
        with self.assertRaises(ExperimentBlocked):
            self.actions.confirm(preview['confirm_token'])
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.actions.receipts()[0]['state'], 'blocked')

    def test_preview_refuses_active_or_wrong_adsets(self):
        self.fake.adsets[B]['status'] = 'ACTIVE'
        with self.assertRaises(ExperimentBlocked):
            self.actions.preview('create:exp1')
        self.fake.adsets[B].update(status='PAUSED', ads={'data': [{'id': '1'}]})
        with self.assertRaises(ExperimentBlocked):
            self.actions.preview('create:exp1')

    def test_expired_token(self):
        preview = self.actions.preview('create:exp1')
        self.now += timedelta(minutes=11)
        with self.assertRaises(ExperimentBlocked):
            self.actions.confirm(preview['confirm_token'])

    def test_rejected_and_unverified(self):
        self.fake.reject_create = True
        receipt = self.actions.confirm(self.actions.preview('create:exp1')['confirm_token'])
        self.assertEqual(receipt['state'], 'rejected')
        self.assertEqual(self.actions.spec('exp1').plan.state, 'planned')
        self.fake.reject_create, self.fake.bad_readback = False, True
        receipt = self.actions.confirm(self.actions.preview('create:exp1')['confirm_token'])
        self.assertEqual(receipt['state'], 'not_verified')
        self.assertIn(STUDY, receipt['detail'])
        self.assertEqual(self.actions.spec('exp1').plan.state, 'planned')

    def test_partial_activation_is_reported_not_retried(self):
        self.actions.confirm(self.actions.preview('create:exp1')['confirm_token'])
        self.fake.fail_update = B
        receipt = self.actions.confirm(self.actions.preview('activate:exp1')['confirm_token'])
        self.assertEqual(receipt['state'], 'partial')
        self.assertIn('גרסה א', receipt['detail'])
        self.assertEqual([c for c in self.writes() if c[1] == B], [('POST', B, {'status': 'ACTIVE'})])
        self.assertEqual(self.actions.spec('exp1').plan.state, 'running')
        self.assertEqual(self.actions.preview('stop:exp1')['changes'], [{'field': 'גרסה א', 'from': 'פעילה', 'to': 'מושהית'}])

    def test_cancel_before_start_is_the_undo(self):
        self.actions.confirm(self.actions.preview('create:exp1')['confirm_token'])
        receipt = self.actions.confirm(self.actions.preview('cancel:exp1')['confirm_token'])
        self.assertEqual(receipt['state'], 'verified')
        self.assertIn(('DELETE', STUDY, None), self.fake.calls)
        self.assertEqual(self.actions.spec('exp1').plan.state, 'interrupted')

    def test_cancel_after_start_refused(self):
        self.actions.confirm(self.actions.preview('create:exp1')['confirm_token'])
        self.now = datetime(2026, 11, 2, tzinfo=timezone.utc)
        with self.assertRaises(ExperimentBlocked):
            self.actions.preview('cancel:exp1')

    def test_one_test_at_a_time_and_start_in_past(self):
        with self.assertRaises(ExperimentBlocked):
            self.actions.register(build_spec(plan(), experiment_id='exp2', rulebook=load_rulebook(), business_id=BUSINESS,
                                             campaign_id=CAMPAIGN, adset_ids=(A, B)))
        self.now = datetime(2026, 11, 1, tzinfo=timezone.utc)
        with self.assertRaises(ExperimentBlocked):
            self.actions.preview('create:exp1')

    def test_inflight_after_restart_becomes_unknown(self):
        state = json.loads(self.actions.path.read_text(encoding='utf-8'))
        state['receipts'].append({'id': 'exp-x', 'state': 'inflight'})
        self.actions.path.write_text(json.dumps(state), encoding='utf-8')
        again = ExperimentActions(self.dir.name, transport=self.fake, credentials=lambda: 'token', clock=lambda: self.now)
        self.assertEqual(again.receipts()[0]['state'], 'unknown')


class RealAdapterShapeTests(unittest.TestCase):
    """The real adapter is not exercised against Meta; only its fixed paths and methods are checked."""

    class Base:
        def __init__(self):
            self.calls = []

        def _request(self, token, path, method='GET', params=None):
            self.calls.append((path, method, params))
            return {'id': STUDY, 'data': []}

        def read(self, token, node, fields):
            return {}

        def update(self, token, node, params):
            self.calls.append((node, 'POST', params))

    def test_paths(self):
        base = self.Base()
        adapter = MetaStudyTransport(base)
        adapter.create_study('t', BUSINESS, {'type': 'SPLIT_TEST'})
        adapter.delete_study('t', STUDY)
        adapter.insights('t', A, '2026-11-01', '2026-11-02')
        self.assertEqual([c[:2] for c in base.calls], [(BUSINESS + '/ad_studies', 'POST'), (STUDY, 'DELETE'), (A + '/insights', 'GET')])
        self.assertEqual(json.loads(base.calls[2][2]['time_range']), {'since': '2026-11-01', 'until': '2026-11-02'})
        for bad in (lambda: adapter.create_study('t', 'me', {}), lambda: adapter.update('t', A, {'daily_budget': '1'})):
            with self.assertRaises(ValueError):
                bad()


class CheckTests(unittest.TestCase):
    def reader(self, scopes=('ads_read', 'ads_management', 'business_management'), studies=(), fail=()):
        calls = []

        def get(path, params):
            calls.append(path)
            if path in fail:
                raise ProviderRejected(details={'code': 200})
            if path == 'me/permissions':
                return {'data': [{'permission': s, 'status': 'granted'} for s in scopes]}
            if path == 'act_393059510164294':
                return {'account_id': '393059510164294', 'currency': 'ILS', 'timezone_name': 'Asia/Jerusalem',
                        'account_status': 1, 'business': {'id': BUSINESS}}
            if path.endswith('/ad_studies'):
                return {'data': list(studies)}
            return {'id': BUSINESS}
        return get, calls

    def test_ready(self):
        get, calls = self.reader()
        out = check(get, NOW)
        self.assertTrue(out['ready'])
        self.assertEqual(calls, ['me/permissions', 'act_393059510164294', BUSINESS, BUSINESS + '/ad_studies',
                                 'act_393059510164294/ad_studies'])
        self.assertIn('קריאה בלבד', out['text'])

    def test_missing_business_permission_and_running_study(self):
        running = {'id': '9', 'end_time': '2026-11-30T00:00:00+0200'}
        get, _ = self.reader(scopes=('ads_read', 'ads_management'), studies=[running], fail=(BUSINESS + '/ad_studies',))
        out = check(get, NOW)
        self.assertFalse(out['ready'])
        text = out['text']
        self.assertIn('ניהול עסק', text)
        self.assertIn('כבר רץ ניסוי', text)
        self.assertIn('קוד 200', text)
        self.assertGreaterEqual(len(out['missing']), 3)

    def test_graph_get_is_get_only_and_allowlisted(self):
        class Base:
            def __init__(self):
                self.calls = []

            def _request(self, token, path, method='GET', params=None):
                self.calls.append((path, method))
                return {}
        base = Base()
        get = GraphGet('secret-token', base)
        get('me/permissions', {})
        self.assertEqual(base.calls, [('me/permissions', 'GET')])
        for path in ('act_1/ads', '../x', 'me/accounts'):
            with self.assertRaises(ValueError):
                get(path, {})
        self.assertNotIn('secret-token', check(get, NOW)['text'])


if __name__ == '__main__':
    unittest.main()
