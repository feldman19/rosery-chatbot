"""Local checks against imported preferences; never sends private project memory."""
import re
from imported_business_method import load_imported_method


def review_creative_method(rows, method=None):
    method = load_imported_method() if method is None else method
    result = {'status': method.get('status', 'unavailable'), 'issues': [],
              'live_action_allowed': False, 'coverage': 'bounded_copy_preferences_not_full_strategy',
              'source_ref': method.get('source_ref'), 'sources': method.get('sources', {})}
    if result['status'] != 'available':
        return result
    rules = {r['id']: r for r in method.get('preferences', [])}
    patterns = [('headline_no_emoji', ('headline',), r'[\U0001F000-\U0001FAFF\u2600-\u27BF]',
                 'לפי העדפת המותג, הכותרת צריכה להיות ללא אימוג׳ים.'),
                ('no_unverified_superlatives', ('primary_text', 'headline', 'description'),
                 r'בסט\s*סלר|הנמכר\s+ביותר|הכי\s+(?:טוב|נמכר)|\bbest[ -]?seller\b|\bbest\s+in\b',
                 'טענת עדיפות דורשת ראיה עדכנית; היסטוריית השיחה אינה הוכחה לטענה.')]
    for row in rows:
        for rule, fields, pattern, explanation in patterns:
            if rule not in rules:
                continue
            for field in fields:
                match = re.search(pattern, str(row.get(field) or ''), re.I)
                if match:
                    result['issues'].append({'asset_id': row.get('asset_id'), 'field': field,
                        'rule_id': rule, 'text': explanation, 'source': rules[rule]['source'],
                        'matched_text': match.group(0), 'status': 'draft_review_required'})
    result['status'] = 'review_required' if result['issues'] else 'checked_supported_preferences'
    return result
