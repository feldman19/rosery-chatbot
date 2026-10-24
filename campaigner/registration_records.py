"""Pure ROSERY registration adapter, not a live Flashy integration.

normalize_registrations(rows, *, context, source_id, tenant_secret) accepts only
host-mapped {id, email, registered_at, ad_id?, linkage?}. The trusted host must
independently verify provider identity AND that registered_at means a completed
registration, not contact creation/list import/marketing consent. Construct the
cohort.HostContext only after that verification. A source ID absent from its
verified_source_ids cannot produce verified records. This adapter neither calls
a provider nor verifies registration persistence or measurement end to end.

Email is transiently stripped/casefolded and tenant-HMACed, using the exact same
email_strip_casefold_v1 contract as shopify_orders.py. No names, phones, URLs,
raw emails, or arbitrary fields are returned. Opaque provider IDs are retained;
HMACs and IDs remain pseudonymous sensitive data. Never log input rows.

Missing identity/date and invalid/future dates are reported separately by row
index. Repeated identical IDs deduplicate; any conflicting versions quarantine
the entire ID, including a valid version paired with an invalid/missing one.
No ad/linkage is inferred. A declared ad ID without explicit click evidence
retains linkage='unknown'; claimed click/view-through without an ad is unknown.
"""
from datetime import datetime
import re

from cohort import HostContext, LINKAGES, person_hmac
from business import ACTIVE as BUSINESS


TENANT = BUSINESS.id
SHOP = BUSINESS.shop
ACCOUNT = BUSINESS.meta_account
ALLOWED_FIELDS = frozenset({'id', 'email', 'registered_at', 'ad_id', 'linkage'})
OPAQUE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}')


def _time(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (TypeError, AttributeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def normalize_registrations(rows, *, context: HostContext, source_id: str, tenant_secret: bytes):
    """Return records accepted by cohort.reconcile_cohort plus coverage/limits.

    No date window is invented: coverage describes only supplied rows. Trusted
    context is a host capability contract, not user-supplied proof of provenance.
    Raw input must be discarded by the host after normalization.
    """
    if not isinstance(context, HostContext) or not isinstance(context.verified_source_ids, frozenset):
        raise ValueError('trusted HostContext is required')
    if (context.tenant, context.shop, context.account, context.currency) != (TENANT, SHOP, ACCOUNT, BUSINESS.currency):
        raise ValueError('registration adapter scope mismatch')
    cutoff = _time(context.as_of)
    if cutoff is None:
        raise ValueError('aware context as_of is required')
    if not isinstance(source_id, str) or not OPAQUE_ID.fullmatch(source_id):
        raise ValueError('opaque host source_id is required')
    if not isinstance(tenant_secret, bytes) or len(tenant_secret) < 32:
        raise ValueError('strong tenant secret is required')
    if not isinstance(rows, (list, tuple)):
        raise ValueError('explicit normalized row list is required')
    trusted = source_id in context.verified_source_ids
    groups, issues = {}, []
    for index, row in enumerate(rows):
        reasons = []
        if not isinstance(row, dict):
            issues.append({'row_index': index, 'reasons': ['unsupported_row_fields']})
            continue
        record_id = row.get('id')
        if isinstance(record_id, int) and not isinstance(record_id, bool):
            record_id = str(record_id)
        if not isinstance(record_id, str) or not OPAQUE_ID.fullmatch(record_id):
            issues.append({'row_index': index, 'reasons': ['missing_or_invalid_id']})
            continue
        if set(row) - ALLOWED_FIELDS:
            reasons.append('unsupported_row_fields')
        email = row.get('email')
        if not isinstance(email, str) or not email.strip():
            reasons.append('missing_identity')
        elif len(email) > 320 or not re.fullmatch(r'[^\s@]+@[^\s@]+', email.strip()):
            reasons.append('invalid_identity')
        registered = row.get('registered_at')
        timestamp = _time(registered)
        if registered is None or registered == '':
            reasons.append('missing_registration_date')
        elif timestamp is None:
            reasons.append('invalid_registration_date')
        elif timestamp > cutoff:
            reasons.append('future_registration_date')
        ad_id = row.get('ad_id')
        if isinstance(ad_id, int) and not isinstance(ad_id, bool):
            ad_id = str(ad_id)
        if ad_id == '':
            ad_id = None
        if ad_id is not None and (not isinstance(ad_id, str) or not OPAQUE_ID.fullmatch(ad_id)):
            reasons.append('invalid_ad_id')
        linkage = row.get('linkage') or 'unknown'
        if not isinstance(linkage, str) or linkage not in LINKAGES:
            reasons.append('invalid_linkage')
        if not trusted:
            reasons.append('source_not_host_verified')
        normalized = None
        if not reasons:
            normalized = {'id': record_id, 'event_id': source_id + ':' + record_id,
                          'tenant': TENANT, 'shop': SHOP, 'account': ACCOUNT, 'currency': context.currency,
                          'source_id': source_id, 'source_verified': True, 'as_of': context.as_of,
                          'person_hmac': person_hmac(TENANT, tenant_secret, email.strip().casefold()),
                          'registered_at': timestamp.isoformat(), 'ad_id': ad_id,
                          'linkage': linkage if ad_id else 'unknown',
                          'identity_method': 'email_strip_casefold_v1'}
        else:
            issues.append({'row_index': index, 'reasons': reasons})
        groups.setdefault(record_id, []).append((index, normalized, reasons))
    records, conflicts, duplicate_count = [], [], 0
    for record_id, group in groups.items():
        states = [item[1] if item[1] is not None else tuple(item[2]) for item in group]
        if any(state != states[0] for state in states[1:]):
            conflicts.append({'id': record_id, 'row_indices': [item[0] for item in group]})
            continue
        if group[0][1] is not None:
            records.append(group[0][1])
            duplicate_count += len(group) - 1
    dates = [row['registered_at'] for row in records]
    reason_counts = {}
    for issue in issues:
        for reason in issue['reasons']:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    return {'records': records, 'issues': issues, 'conflicts': conflicts,
            'duplicates_removed': duplicate_count,
            'coverage': {'input_rows': len(rows), 'accepted_events': len(records),
                         'unique_people': len({row['person_hmac'] for row in records}),
                         'reason_counts': reason_counts,
                         'first_supplied_registration': min(dates, key=_time) if dates else None,
                         'last_supplied_registration': max(dates, key=_time) if dates else None,
                         'provider_window_complete': False,
                         'all_supplied_rows_usable': not issues and not conflicts},
            'binding': {'tenant': TENANT, 'shop': SHOP, 'account': ACCOUNT,
                        'currency': context.currency, 'source_id': source_id, 'as_of': context.as_of,
                        'source_verified_by_host': trusted, 'adapter_live_verification': False},
            'limitations': ['Pure adapter; it does not verify Flashy or another provider.',
                           'Host must verify timestamp semantics mean completed registration.',
                           'Input rows do not prove full provider/date-window coverage.',
                           'Unknown attribution is retained; URL or ad evidence is never guessed.',
                           'HMAC matching is pseudonymous reconciliation, not causal attribution.']}
