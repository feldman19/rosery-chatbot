"""Read-only projection of explicitly allowlisted ROSERY review artifacts."""
import hashlib
import json
from datetime import date
from pathlib import Path
from business import ACTIVE as BUSINESS


def review_history(root):
    outputs = Path(root).parent / 'outputs'
    entries = []
    for path in sorted(outputs.glob('rosery-daily-review-????-??-??.json'), reverse=True)[:100]:
        filename = path.name
        if not path.exists():
            continue
        raw = path.read_bytes()
        d = json.loads(raw)
        if d.get('account') != BUSINESS.meta_account or d.get('shop') != BUSINESS.shop or d.get('readOnly') is not True:
            raise ValueError('Review binding mismatch')
        reviewed = date.fromisoformat(d['checkedDate'])
        until = date.fromisoformat(d['fullDaysUntil'])
        if until > reviewed:
            raise ValueError('Invalid review window')
        launch = d['launch']
        if d.get('decision', {}).get('actionsExecuted') != []:
            raise ValueError('Unsupported execution history')
        if launch['id'] != BUSINESS.meta_launch_campaign:
            raise ValueError('Campaign mismatch')
        daily = launch['yesterday']
        entries.append({'date': reviewed.isoformat(), 'period_end': until.isoformat(),
                        'spend_ils': daily.get('spendILS'), 'registrations': daily.get('attributedRegistrations'),
                        'landing_page_views': daily.get('landingPageViews'),
                        'cpl_ils': daily.get('attributedCplILS'),
                        'budget_ils': launch.get('budgetILS'),
                        'flashy_contacts': d.get('flashy', {}).get('contacts'),
                        'source_file': filename, 'sha256': hashlib.sha256(raw).hexdigest(),
                        'actions_executed': [], 'scope': 'saved_read_only_review',
                        'source_authenticated': False})
    return {'available': bool(entries), 'entries': sorted(entries, key=lambda x:x['date'], reverse=True),
            'independent_refresh': False, 'live_action_allowed': False,
            'next_step': 'להשלים התאמת הרשמות ייחודיות וכתובות יעד; תשובת תקציב אינה אישור לשינוי',
            'comparison': 'אין לחשב שינוי מול תמונת מצב חלקית או חלון זמן אחר'}
