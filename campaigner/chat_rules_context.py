"""Compact rulebook assessment for the advisory chat. Data only, no authority."""
from rulebook import load_rulebook
from rule_engine import evaluate_report
from today_plan import build_today

FIELDS = ('entity_id', 'entity_name', 'adset_name', 'campaign_name', 'label', 'title', 'why', 'numbers',
          'thresholds', 'data_does_not_support')


def chat_rule_context(report, ad_id=None, *, rulebook=None, limit=15):
    evaluation = evaluate_report(report, rulebook or load_rulebook())
    decisions = evaluation['decisions']
    if ad_id is not None:
        decisions = [d for d in decisions if d['entity_id'] == ad_id]
    today = build_today(evaluation)
    return {'status': evaluation['status'], 'window': evaluation.get('window'), 'blocked_by': evaluation['blocked_by'],
            'today_summary': today['summary'],
            'decisions': [{k: d[k] for k in FIELDS} for d in decisions[:limit]],
            'omitted_decisions': max(0, len(decisions) - limit),
            'rule_semantics': 'ספר החוקים של הבעלים; הצעות בלבד, כל שינוי דורש אישור מפורש'}
