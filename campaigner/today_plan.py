"""Plain-language "what to do today" list for an owner who does not know Meta.

Built only from rule_engine output. It never executes; each item points to the
proposal that needs explicit approval, and says what the data does not prove.
"""
from rule_engine import HOLD, KEEP_TESTING, NO_DATA, PAUSE, SCALE, SUCCESS, UNTESTED

MAX_ITEMS = 5


def _money(value):
    return f'₪{value:,.0f}' if isinstance(value, (int, float)) else 'לא ידוע'


def build_today(evaluation):
    blocked = evaluation.get('status') == 'blocked'
    decisions = evaluation.get('decisions') or []
    proposals = {p['entity_id']: p for p in evaluation.get('proposals') or [] if p['kind'] == 'pause_ad'}
    scales = [p for p in evaluation.get('proposals') or [] if p['kind'] == 'scale_adset_budget']
    items = []
    for decision in decisions:
        if decision['action'] == PAUSE:
            proposal = proposals.get(decision['entity_id']) or {}
            items.append({'kind': 'approve', 'action': PAUSE, 'headline': f'להשהות את "{decision["entity_name"]}"',
                          'body': decision['why'], 'saving_per_day': decision['numbers']['avg_daily_spend'],
                          'warnings': proposal.get('warnings', []) + decision['data_does_not_support'][:1],
                          'proposal_id': proposal.get('id'), 'ready_for_approval': proposal.get('ready_for_approval', False),
                          'link': decision['ads_manager_url']})
    for proposal in scales:
        before, after = proposal['before']['daily_budget_minor'], proposal['after']['daily_budget_minor']
        change = (f'מ-{_money(before / 100)} ל-{_money(after / 100)} ליום' if before is not None and after is not None
                  else 'ב-20% (התקציב הנוכחי לא ידוע — צריך לקרוא אותו קודם)')
        items.append({'kind': 'approve', 'action': SCALE, 'headline': f'להגדיל את "{proposal["entity_name"]}" {change}',
                      'body': proposal['reason'], 'warnings': proposal['warnings'], 'proposal_id': proposal['id'],
                      'ready_for_approval': proposal['ready_for_approval'], 'link': proposal['ads_manager_url']})
    watch = [d for d in decisions if d['action'] == KEEP_TESTING and d['numbers']['purchases'] == 0
             and (d['numbers']['spend'] or 0) >= 150]
    for decision in watch:
        items.append({'kind': 'watch', 'action': KEEP_TESTING, 'headline': f'לעקוב אחרי "{decision["entity_name"]}"',
                      'body': decision['why'], 'warnings': [], 'link': decision['ads_manager_url']})
    for alert in evaluation.get('account_alerts') or []:
        items.append({'kind': 'alert', 'action': 'ALERT', 'headline': 'שים לב למבנה החשבון', 'body': alert['text'],
                      'warnings': [], 'link': None})
    counts = {key: sum(1 for d in decisions if d['action'] == key)
              for key in (PAUSE, SCALE, HOLD, SUCCESS, KEEP_TESTING, UNTESTED, NO_DATA)}
    if blocked:
        summary = 'אין היום המלצות: ' + '; '.join(evaluation.get('blocked_by') or ['חסרים נתונים'])
    elif not items:
        summary = f'אין היום מה לשנות. {len(decisions)} מודעות נבדקו לפי החוקים — כולן בתוך הטווח.'
    else:
        approvals = sum(1 for i in items if i['kind'] == 'approve')
        summary = (f'נבדקו {len(decisions)} מודעות ב-3 הימים המלאים האחרונים. '
                   f'{approvals} פעולות מחכות לאישור שלך' + (f', ו-{len(items) - approvals} דברים למעקב.' if len(items) > approvals else '.'))
    if evaluation.get('status') == 'stale_data':
        summary += ' הנתונים לא עדכניים — לרענן לפני אישור.'
    return {'summary': summary, 'items': items[:MAX_ITEMS], 'more': max(0, len(items) - MAX_ITEMS), 'counts': counts,
            'window': evaluation.get('window'), 'status': evaluation.get('status'),
            'note': 'שום דבר לא מתבצע אוטומטית. כל שינוי דורש את האישור שלך, ואחרי הביצוע המערכת קוראת חזרה מהחשבון.',
            'live_action_allowed': False}
