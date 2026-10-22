"""Pure candidate strategy for normalized evidence, never a provider executor.

Amounts are integer currency minor units. Required evidence contract:
verified costs/contribution_lower_minor, measurement/unique_registrations,
provenance/refs (sha256:...), maturity/attribution_complete, stock/available,
offer/current, cohort/subscribers+purchasers, windows/{1,3,7} full_days+spend_minor
+registrations, entity/status+budget_minor+budget_type+currency+last_changed_at.
Account days follow the declared IANA timezone, including DST calendar days.
Missing or unknown zones block rather than falling back silently.
The caller must supply trustworthy normalized evidence; booleans are assertions,
not independent verification. This module grants no permission or live authority.
"""
import math
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


REF = re.compile(r'^sha256:[0-9a-f]{64}$')


def _amount(value):
    return type(value) is int and value >= 0


def _time(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError('Timezone required')
    return parsed


def _wilson(successes, total):
    z = 1.959963984540054
    rate = successes / total
    denominator = 1 + z*z/total
    center = (rate + z*z/(2*total))/denominator
    radius = z*math.sqrt(rate*(1-rate)/total + z*z/(4*total*total))/denominator
    return max(0, center-radius), min(1, center+radius)


def evaluate(profile, evidence, policy, now=None):
    """Return a hypothetical, evidence-bound candidate; never execute or authorize."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('now requires timezone')
    entity = evidence.get('entity') or {}
    before = {key: entity.get(key) for key in ('status', 'budget_minor', 'currency', 'budget_type')}
    result = {'status': 'blocked', 'action': None, 'before': before, 'after': dict(before),
              'entity': {key: profile.get(key) for key in ('tenant_id','account_id','entity_id')},
              'evidence_refs': [], 'reasons': [], 'economics': None,
              'next_review_at': None, 'rollback_condition': 'No candidate change has been proposed',
              'assumed_scope': 'calculated candidate from caller-normalized evidence; not a live account audit',
              'live_action_allowed': False, 'permission_granted': False,
              'risk': 'Evidence may not transport to incremental traffic; candidates require exact human approval and provider readback'}
    missing = result['reasons']
    try:
        account_zone = ZoneInfo(profile.get('timezone'))
    except (ZoneInfoNotFoundError,TypeError,ValueError):
        missing.append('Missing or unknown account timezone')
        account_zone = timezone.utc  # Calculations continue only to collect blockers.
    closed_end = now.astimezone(account_zone).replace(hour=0,minute=0,second=0,microsecond=0)
    if any(not isinstance(profile.get(k), str) or not profile[k] for k in ('tenant_id','account_id','entity_id')):
        missing.append('Missing business and entity identity')
    if any(entity.get(k) != profile.get(k) for k in ('tenant_id','account_id','entity_id')):
        missing.append('Evidence entity identity does not match business profile')
    for gate, field in [('costs','verified'),('measurement','verified'),('provenance','verified'),('maturity','verified'),('stock','verified'),('offer','verified')]:
        if (evidence.get(gate) or {}).get(field) is not True:
            missing.append('Unverified '+gate)
    refs = (evidence.get('provenance') or {}).get('refs', [])
    if not isinstance(refs,list) or not refs or any(not isinstance(r,str) or not REF.fullmatch(r) for r in refs):
        missing.append('Missing immutable evidence references')
    else:
        result['evidence_refs'] = list(refs)
    try:
        observed = _time(evidence['observed_at'])
        if observed > now or now > _time(evidence['valid_until']):
            missing.append('Evidence expired or observed in the future')
        changed = _time(entity['last_changed_at'])
        if changed > now:
            missing.append('Entity change timestamp is in the future')
    except (ValueError, TypeError, KeyError):
        missing.append('Missing aware evidence validity or change timestamps')
    required = ('cap_minor','max_test_loss_minor','budget_step_minor','min_scale_registrations','review_after_hours','profit_margin','allowed_actions')
    if any(key not in policy for key in required):
        missing.append('Missing declared policy envelope')
    else:
        if any(not _amount(policy[k]) or policy[k] == 0 for k in required[:5]):
            missing.append('Invalid positive policy amount/count/time')
        if type(policy['profit_margin']) not in (int,float) or not 0 <= policy['profit_margin'] < 1:
            missing.append('Invalid declared profit margin')
        if not isinstance(policy['allowed_actions'],list) or any(a not in ('pause','set_budget') for a in policy['allowed_actions']):
            missing.append('Invalid permitted candidate actions')
    if not _amount(entity.get('budget_minor')) or entity.get('budget_type') != 'daily' or entity.get('status') not in ('ACTIVE','PAUSED'):
        missing.append('Missing supported current entity budget or status')
    if not isinstance(entity.get('currency'),str) or entity.get('currency') != profile.get('currency'):
        missing.append('Currency mismatch or missing currency')
    cost = (evidence.get('costs') or {}).get('contribution_lower_minor')
    cohort = evidence.get('cohort') or {}
    n, buyers = cohort.get('subscribers'), cohort.get('purchasers')
    if not _amount(cost) or not cost:
        missing.append('Missing positive verified contribution lower bound')
    if cohort.get('verified') is not True or type(n) is not int or type(buyers) is not int or n <= 0 or not 0 <= buyers <= n:
        missing.append('Missing verified subscriber purchase cohort')
    if (evidence.get('measurement') or {}).get('unique_registrations') is not True:
        missing.append('Unique registrations not reconciled')
    windows = evidence.get('windows') or {}
    for days in (1,3,7):
        window = windows.get(str(days)) or {}
        if window.get('full_days') is not True or not _amount(window.get('spend_minor')) or not _amount(window.get('registrations')):
            missing.append('Missing full-day window '+str(days))
        try:
            end = _time(window['ended_at'])
            start = _time(window['started_at'])
            local_start=start.astimezone(account_zone)
            local_end=end.astimezone(account_zone)
            if end != closed_end or any((local_end.hour,local_end.minute,local_end.second,local_end.microsecond)):
                missing.append('Windows must share the latest closed account-day midnight')
            if any((local_start.hour,local_start.minute,local_start.second,local_start.microsecond)):
                missing.append('Window start is not an account-day midnight')
            if local_start.date() != closed_end.date()-timedelta(days=days):
                missing.append('Window calendar days do not match declared full days')
            if start < changed:
                missing.append('Window includes data preceding current entity change')
            if end > now:
                missing.append('Window ends in future')
            if end > observed:
                missing.append('Window ends after evidence observation')
            if end <= changed:
                missing.append('Window predates current entity change')
        except (ValueError,TypeError,KeyError,UnboundLocalError):
            missing.append('Missing valid window end or change timestamp')
    if all(str(d) in windows and _amount(windows[str(d)].get('spend_minor')) and _amount(windows[str(d)].get('registrations')) for d in (1,3,7)):
        if any(windows[str(a)]['spend_minor'] > windows[str(b)]['spend_minor'] or windows[str(a)]['registrations'] > windows[str(b)]['registrations'] for a,b in ((1,3),(3,7))):
            missing.append('Nested window totals are inconsistent')
    maturity = evidence.get('maturity') or {}
    if maturity.get('attribution_complete') is not True:
        missing.append('Attribution has not matured')
    try:
        if _time(maturity['observed_at']) > now or now > _time(maturity['valid_until']) or _time(maturity['attributed_through']) < closed_end:
            missing.append('Maturity proof expired or does not cover the latest closed day')
    except (ValueError,TypeError,KeyError):
        missing.append('Missing dated maturity validity and coverage')
    if missing:
        return result
    result['next_review_at'] = (now + timedelta(hours=policy['review_after_hours'])).isoformat()
    low, high = _wilson(buyers,n)
    target = math.floor(cost * low * (1-policy['profit_margin']))
    result['economics'] = {'cohort_purchase_probability': buyers/n, 'wilson_95_interval': [low,high],
                           'contribution_lower_minor': cost, 'conservative_cpl_minor': target,
                           'method': 'contribution lower bound × Wilson95 cohort rate lower bound × declared retained margin',
                           'limitation': 'Binomial interval assumes comparable mature subscribers; not proof of causal lift'}
    result['status']='hold'
    if entity['status']!='ACTIVE':
        result['reasons']=['Entity is paused; no automatic reactivation candidate']
        return result
    if evidence['stock'].get('available') is not True or evidence['offer'].get('current') is not True:
        result['reasons']=['Stock or offer is not suitable; business review required']
        return result
    if evidence['maturity'].get('comparable_cohort') is not True:
        result['reasons']=['Historical subscriber cohort is not demonstrated comparable']
        return result
    seven=windows['7']
    if seven['registrations']==0 and seven['spend_minor'] >= policy['max_test_loss_minor'] and 'pause' in policy['allowed_actions']:
        result.update(status='propose',action='pause',after={**before,'status':'PAUSED'},
                      reasons=['Zero reconciled mature registrations and declared test-loss envelope reached'],
                      rollback_condition='Reassess only after corrected measurement or a new explicitly approved test; never reactivate automatically')
        return result
    if target <= 0:
        result['reasons']=['Conservative subscriber value does not support a positive acquisition cost']
        return result
    enough=all(windows[str(d)]['registrations'] >= policy['min_scale_registrations'] for d in (1,3,7))
    economical=all(windows[str(d)]['registrations'] and windows[str(d)]['spend_minor']/windows[str(d)]['registrations'] <= target for d in (1,3,7))
    after_budget=min(entity['budget_minor']+policy['budget_step_minor'],policy['cap_minor'])
    if enough and economical and after_budget>entity['budget_minor'] and 'set_budget' in policy['allowed_actions']:
        result.update(status='propose',action='set_budget',after={**before,'budget_minor':after_budget},
                      reasons=['All full-day windows meet the conservative economic CPL and declared observation minimum', 'Candidate increment remains within the declared envelope; this is not spending authorization'],
                      rollback_condition='At the next declared review, propose returning to prior budget if mature reconciled CPL exceeds the conservative bound; require current-state readback and exact approval')
    else:
        result['reasons']=['Evidence does not meet every declared scale criterion; retain current state']
    return result


def sample_case(now=None,timezone_name="UTC"):
    """Synthetic contract example, never inferred from ROSERY live data."""
    now=now or datetime(2026,10,17,12,tzinfo=timezone.utc)
    profile={'tenant_id':'sandbox-business','account_id':'sandbox-account','entity_id':'sandbox-launch','currency':'ILS','timezone':timezone_name}
    closed_end=now.astimezone(ZoneInfo(timezone_name)).replace(hour=0,minute=0,second=0,microsecond=0)
    policy={'cap_minor':10000,'max_test_loss_minor':5000,'budget_step_minor':1000,
            'min_scale_registrations':3,'review_after_hours':24,'profit_margin':0.2,
            'allowed_actions':['pause','set_budget']}
    evidence={key:{'verified':True} for key in ('costs','measurement','provenance','maturity','stock','offer')}
    evidence.update(observed_at=now.isoformat(),valid_until=(now+timedelta(minutes=15)).isoformat(),
        entity={**profile,'status':'ACTIVE','budget_minor':6000,'budget_type':'daily',
                'last_changed_at':(now-timedelta(days=9)).isoformat()},
        cohort={'verified':True,'subscribers':1000,'purchasers':200},
        windows={str(d):{'full_days':True,'spend_minor':1000*d,'registrations':10*d,
                        'started_at':(closed_end-timedelta(days=d)).isoformat(),
                        'ended_at':closed_end.isoformat()} for d in (1,3,7)})
    evidence['costs']['contribution_lower_minor']=15000
    evidence['measurement']['unique_registrations']=True
    evidence['provenance']['refs']=['sha256:'+'a'*64]
    evidence['maturity'].update(attribution_complete=True,comparable_cohort=True,observed_at=now.isoformat(),valid_until=(now+timedelta(minutes=15)).isoformat(),attributed_through=closed_end.isoformat())
    evidence['stock']['available']=True
    evidence['offer']['current']=True
    return {'profile':profile,'evidence':evidence,'policy':policy,'now':now}
