"""One saved-entity acceptance check; no network, workers or server restart."""
from pathlib import Path
from datetime import datetime, timezone
from copy import deepcopy
import hashlib
import json
from pause_budget_context import budget_context
from pause_evaluation import evaluate_pause
from management_report import build_report
from business_context import load_rosery_dossier
from order_economics import order_economics

root = Path(__file__).parent
started = datetime.now(timezone.utc).isoformat()
raw = (root / '.local/live-reports.json').read_bytes()
snapshot = json.loads(raw)
ad_id = '120253138095700730'
context = budget_context(snapshot, ad_id)
report = build_report(snapshot)
row = next(x for x in report['windows']['7']['ads'] if x['id'] == ad_id)
assessment = evaluate_pause(report, row, order_economics(
    load_rosery_dossier(include_owner_financials=True)), budget_context=context)
assert assessment['budget_context'] == context
assert context['avoidable_spend_ils'] is None and context['forecast_ils'] is None
assert assessment['execution_allowed'] is False and assessment['status'] == 'ABSTAIN'
missing_campaign = deepcopy(snapshot)
for c in missing_campaign['sources']['meta']['data']['account_campaigns']['campaigns']:
    c.pop('daily_budget', None)
test = budget_context(missing_campaign, ad_id)
assert any(x['field'] == 'daily_budget' and '.account_campaigns.' in x['path']
           and not x['present'] and x['value'] is None for x in test['evidence'])
wrong = deepcopy(context)
wrong['scope']['entity_id'] = 'other'
try:
    evaluate_pause(report, row, {}, budget_context=wrong)
except ValueError:
    pass
else:
    raise AssertionError('Cross-entity budget evidence accepted')
receipt = {
    'goal': 'Bound pause evaluation to saved budget and schedule evidence',
    'stage': 'local_only_integration_verified',
    'source_path': str(root / '.local/live-reports.json'),
    'source_sha256': hashlib.sha256(raw).hexdigest(),
    'started_at': started, 'completed_at': datetime.now(timezone.utc).isoformat(),
    'assessment': assessment,
    'checks': ['exact_entity_integration', 'no_savings_forecast',
               'missing_budget_not_zero', 'wrong_scope_rejected'],
    'active_server_changed': False, 'account_calls': 0,
    'next_step': 'Obtain missing current configuration and validated future outcome estimates before an actionable pause recommendation'}
output = root.parent / 'outputs/pause-budget-context-acceptance-2026-10-20.json'
output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'status': assessment['status'], 'checks': receipt['checks'],
                  'sibling_ads': [x['ad_id'] for x in context['other_observed_active_ads']
                                  if x['same_adset']],
                  'started_at': started, 'completed_at': receipt['completed_at']},
                 ensure_ascii=False))
