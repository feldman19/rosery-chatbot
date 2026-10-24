"""Pure owner-facing decisions. Never executes changes or invents financial limits.

HOLD means preserve current configuration, including PAUSED. Experiment proposals
only prepare a paused reviewable plan; they never authorize creation or spend.
"""
from datetime import datetime, timezone
from math import isfinite
from order_economics import order_economics
from decision_evidence import report_assessment
from decision_uncertainty import one_event_sensitivity
from pause_recommendation_adapter import pause_assessment
from rulebook import load_rulebook
from rule_engine import evaluate_report
from today_plan import build_today
from business import ACTIVE as BUSINESS

ACCOUNT = BUSINESS.meta_account
SOURCE = {'id': 'rosery-operating-guide-v1', 'kind': 'local_operating_safeguard',
          'reference': 'outputs/rosery-media-operations-guide-v1-2026-10-15.md',
          'as_of': '2026-10-15', 'supports': 'evidence completeness, alternatives, authorization; not profitability proof'}


def _number(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) and value >= 0 else None


def _stamp(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result if result.tzinfo else None
    except (TypeError, ValueError, AttributeError):
        return None


def _evidence(report, kind, identity):
    evidence = []
    for days in ('1', '3', '7'):
        window = report.get('windows', {}).get(days, {})
        matches = [row for row in window.get(kind, []) if row.get('id') == identity]
        row = matches[0] if len(matches) == 1 and window.get('partial') is False else {}
        field = 'registrations' if row.get('result_type') == 'registration' else 'purchases'
        spend, results = _number(row.get('spend')), _number(row.get(field))
        evidence.append({'days': int(days), 'since': window.get('since'), 'until': window.get('until'),
                         'spend': spend, 'results': results, 'result_type': row.get('result_type'),
                         'cost_per_result': spend / results if spend is not None and results is not None and results > 0 else None,
                         'attributed_revenue': _number(row.get('attributed_revenue')),
                         'complete': bool(row) and spend is not None and results is not None,
                         'scope': 'this_entity_full_days_not_current_composition_history'})
    return evidence


def performance_findings(report):
    """Descriptive same-window comparisons, never a financial action or causal claim."""
    window = report.get('windows', {}).get('3', {})
    if window.get('partial') is not False:
        return []
    rows = [r for r in window.get('adsets', [])
            if r.get('configured_active') is True and r.get('result_type') == 'purchase'
            and _number(r.get('spend')) is not None and r['spend'] > 0]
    findings = []
    for row in rows:
        purchases = _number(row.get('purchases'))
        base = {'entity_id': row['id'], 'entity_name': row.get('name') or row['id'],
                'campaign_id': row.get('campaign_id'), 'since': window.get('since'),
                'until': window.get('until'), 'spend': row['spend'], 'purchases': purchases,
                'action_allowed': False, 'kind': 'observation'}
        if purchases is None:
            findings.append({**base, 'title': 'הוצאה עם דיווח רכישות חסר',
                'detail': f"דווחה הוצאה של {row['spend']:.2f} ₪. מספר הרכישות אינו זמין; אין להסיק שלא היו רכישות.",
                'priority': 0})
            continue
        if purchases == 0:
            findings.append({**base, 'title': 'לא דווחו רכישות בתקופה',
                'detail': f"דווחה הוצאה של {row['spend']:.2f} ₪ ואפס רכישות מיוחסות. זו תוצאה לתקופה, לא הוכחה להפסד או לכשל בקריאייטיב.",
                'priority': 1})
            continue
        cpa = row['spend'] / purchases
        peers = [r for r in rows if row.get('campaign_id') and
                 r.get('campaign_id') == row['campaign_id'] and
                 _number(r.get('purchases')) is not None and r['purchases'] > 0]
        best = min(peers, key=lambda r: r['spend']/r['purchases']) if len(peers) > 1 else None
        title = 'יש רכישות — העלות ניתנת לחישוב'
        comparison = ''
        if best:
            best_cpa = best['spend']/best['purchases']
            if cpa > best_cpa:
                title = 'עלות רכישה גבוהה יותר בהשוואה הנוכחית'
                comparison = f" באותו קמפיין ובאותה תקופה העלות הנמוכה ביותר שנצפתה הייתה {best_cpa:.2f} ₪."
            else:
                title = 'עלות נצפית נמוכה יותר — לא קביעה של עדיפות'
                comparison = ' מבין האדסטים הפעילים באותו קמפיין שיש להם דיווח רכישות מלא לתקופה.'
        findings.append({**base, 'title': title, 'cost_per_purchase': cpa,
            'detail': f"{purchases:g} רכישות מיוחסות מתוך הוצאה של {row['spend']:.2f} ₪ — {cpa:.2f} ₪ לרכישה."+comparison,
            'limitation': 'ההשוואה אינה ניסוי אקראי, אינה מבודדת את הקריאייטיב ואינה מוכיחה רווחיות. גם היקף המדגם מוצג כדי לא להכריז על מנצח מוקדם.',
            'priority': 2})
    return sorted(findings, key=lambda f: (f['priority'], -f['spend']))


def build_recommendations(report, dossier=None, *, now=None, rulebook=None):
    """Consume build_report output and optional owner-only load_rosery_dossier output.

    Dossier experiment labels are not verified evidence and cannot generate an
    action. Historical financial facts are context, never a CPA target.
    """
    if not isinstance(report, dict) or report.get('account_id') != ACCOUNT:
        raise ValueError('ROSERY account mismatch')
    dossier = dossier or {}
    if dossier and dossier.get('business_id') != BUSINESS.id:
        raise ValueError('ROSERY dossier mismatch')
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('Timezone-aware now required')
    checked = _stamp(report.get('checked_at'))
    source_checked = _stamp(report.get('freshness', {}).get('meta_last_success_at'))
    fresh = (report.get('freshness', {}).get('state') == 'fresh' and checked is not None
             and source_checked is not None and 0 <= (now-source_checked).total_seconds() <= 900
             and 0 <= (now - checked).total_seconds() <= 900)
    financial_context = [dict(item) for item in dossier.get('known', [])
                         if isinstance(item, dict) and item.get('kind') == 'historical_metric']
    economics = order_economics(dossier)
    decisions = []
    window = report.get('windows', {}).get('7', {})
    for kind, label in (('campaigns', 'קמפיין'), ('adsets', 'אדסט'), ('ads', 'מודעה')):
        seen = set()
        for row in window.get(kind, []):
            identity = row.get('id')
            if not isinstance(identity, str) or not identity or identity in seen:
                continue
            seen.add(identity)
            evidence = _evidence(report, kind, identity)
            latest = evidence[0]
            missing = []
            if not fresh: missing.append('fresh_source_data')
            if any(not item['complete'] for item in evidence): missing.append('complete_entity_conversion_windows')
            if row.get('result_type') not in ('purchase', 'registration'): missing.append('verified_objective')
            if row.get('configured_active') is None: missing.append('current_configuration')
            # Aggregate performance is not end-to-end measurement verification.
            change = row.get('change_evidence') or {}
            entity_update_known = (change.get('entity_id') == identity
                                   and change.get('entity_type') == kind
                                   and change.get('status') == 'entity_update_observed'
                                   and _stamp(change.get('updated_time')) is not None)
            if not entity_update_known:
                missing.append('entity_update_timestamp')
            # Even a valid updated_time does not name the changed field/event.
            missing.extend(['verified_measurement_and_attribution_maturity','current_stock_and_offer'])
            missing.extend('economics:' + gap for gap in economics['missing'])
            state = {'status': row.get('status'), 'effective_status': row.get('effective_status'),
                     'daily_budget_minor': row.get('daily_budget_minor')}
            action, title = 'INSUFFICIENT_EVIDENCE', 'אין עדיין מסקנה מבוססת'
            if not fresh:
                reason = 'המידע אינו עדכני מספיק להחלטת תקציב. נדרש מידע עדכני; לא גובשה המלצה להגדלה או לעצירה.'
            elif latest['results'] is None:
                reason = 'נתוני ההמרות של הישות חסרים. אין לפרש זאת כאפס רכישות או לעצור על בסיסם.'
            elif latest['spend'] == 0:
                reason = 'לא דווחה הוצאה ביום המלא האחרון. אין ראיה שהקריאייטיב הפסיד, ואין בסיס להגדלת תקציב.'
            elif latest['results'] == 0:
                reason = 'ביום המלא האחרון דווחה הוצאה ללא תוצאות מיוחסות. זו תצפית אמיתית, אך אינה מספיקה לבדה לעצירה: חסרים יעד רווח עדכני והבשלת ייחוס.'
            else:
                reason = 'יש תוצאות מיוחסות, אך עלות לתוצאה לבדה אינה מוכיחה רווחיות או יכולת סקייל. עדיין אין בסיס להמלצה להגדיל, לעצור או להשאיר כרגיל.'
            assessment = report_assessment(report, row, kind[:-1], economics, now=now)
            assessment['missing_evidence'] = list(dict.fromkeys(
                missing + assessment['missing_evidence']))
            pause=pause_assessment(report,row,economics,dossier,now=now) if kind=='ads' else None
            decisions.append({'id': kind + ':' + identity, 'entity_type': kind[:-1], 'entity_id': identity,
                              'entity_name': row.get('name') or identity, 'entity_label': label,
                              'action': action, 'title': title, 'rationale': reason, 'evidence': evidence,
                              'missing_data': assessment['missing_evidence'], 'evidence_assessment': assessment,
                              'system_gaps':assessment['system_gaps'],'evidence_records':assessment['evidence_records'],
                              'pause_evaluation':pause,
                              'one_event_sensitivity': one_event_sensitivity(row.get('spend'), row.get('purchases')), 'change_evidence': change if change else {
                                  'status': 'not_collected', 'entity_id': identity, 'entity_type': kind,
                                  'relevant_change_time': None, 'relevant_change_status': 'unknown',
                                  'attribution_maturity_proven': False},
                              'sensitivity_scope': {'days':7,'since':window.get('since'),'until':window.get('until'),
                                                    'result_type':'reported_purchase_events','unique_customers':False},
                              'confidence': 'insufficient_for_financial_change',
                              'before': state, 'after': dict(state), 'live_change': False,
                              'requires_approval': False,
                              'preparation': None,
                              'risks': ['ייחוס Meta אינו תוספתיות או רווח', 'הרכב הפעילות כיום עשוי להיות שונה מההרכב בחלון ההיסטורי'],
                              'recheck': 'ברענון נתונים חדש; החלטת תקציב רק לאחר השלמת הראיות הכלכליות והמדידה',
                              'rollback': 'אין שינוי חי להחזיר; הכנת ניסוי אינה משנה את הישות הקיימת',
                              'sources': [SOURCE]})
    # The owner's rulebook is the approved financial policy; it yields proposals only.
    rulebook = load_rulebook() if rulebook is None else rulebook
    rules = evaluate_report(report, rulebook, now=now)
    rules_ready = rulebook.get('status') == 'available'
    return {'account_id': ACCOUNT, 'currency': report.get('currency'), 'checked_at': report.get('checked_at'),
            'business_method': dossier.get('business_method', {'status':'unavailable'}),
            'performance_findings': performance_findings(report) if fresh else [],
            'decision_engine_status': 'owner_rulebook_proposals' if rules_ready else 'financial_action_generation_not_implemented',
            'policy_status':{'pause':'owner_rulebook_proposal' if rules_ready else 'implemented_scenario_only',
                             'scale':'owner_rulebook_proposal' if rules_ready else 'scale_forecast_not_implemented'},
            'system_gaps':['ad_level_execution_not_connected'] if rules_ready else ['executable_financial_policy_not_implemented','scale_forecast_not_implemented'],
            'rule_engine': rules, 'today': build_today(rules),
            'system_gap_explanation':'השוואת תרחישי עצירה מול המשך מחוברת. היא דורשת ראיות על חיסכון ותרומה עתידיים ואינה המלצה לביצוע. חיזוי סקייל עדיין לא מומש.',
            'fresh': fresh, 'recommendations': decisions, 'order_economics': economics, 'historical_financial_context': financial_context,
            'financial_context_is_target': False, 'live_action_allowed': False,
            'financial_decisions_blocked_by': ['אין יעד רווח תפעולי עדכני מאומת ומאושר',
                'אין אימות מדידה, הבשלת ייחוס וזמן שינוי לכל ישות',
                'LTV הכנסות וקוהורטים היסטוריים אינם תקרת CAC מבוססת תרומה'],
            'sources': [SOURCE]}
