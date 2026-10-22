"""Read-only evidence projection. Budget configuration is never a spend forecast."""
ACCOUNT = '393059510164294'
SDK = 'https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/'


def budget_context(snapshot, ad_id):
    data = snapshot['sources']['meta']['data']
    active = data.get('active_ads', {})
    inventory = data.get('account_campaigns', {})
    if str(data.get('account_id')) != ACCOUNT or not active.get('verified') or not active.get('complete'):
        raise ValueError('Verified complete ROSERY active-ad source required')
    matches = [x for x in active.get('ads', []) if x.get('id') == ad_id]
    if len(matches) != 1:
        raise ValueError('Unique saved ad required')
    ad = matches[0]
    adset, parent = ad.get('adset', {}), ad.get('campaign', {})
    if not adset.get('id') or not parent.get('id'):
        raise ValueError('Saved parent identity required')
    campaigns = [x for x in inventory.get('campaigns', [])
                 if x.get('id') == parent['id'] and str(x.get('account_id')) == ACCOUNT]
    campaign = campaigns[0] if len(campaigns) == 1 and inventory.get('verified') else {}
    evidence = []

    def fields(entity, names, path, observed):
        for name in names:
            evidence.append({'field': name, 'present': name in entity,
                             'value': entity.get(name), 'path': path + '.' + name,
                             'observed_at': observed})

    base = 'sources.meta.data.active_ads.ads[id=' + ad_id + ']'
    at = active.get('checked_at')
    fields(ad, ['id', 'status', 'effective_status', 'ad_schedule_start_time',
                'ad_schedule_end_time'], base, at)
    fields(adset, ['id', 'status', 'daily_budget', 'lifetime_budget', 'daily_spend_cap',
                  'lifetime_spend_cap', 'start_time', 'end_time', 'budget_schedule_specs'],
           base + '.adset', at)
    fields(campaign, ['id', 'daily_budget', 'lifetime_budget', 'spend_cap',
                     'start_time', 'stop_time', 'budget_schedule_specs'],
           'sources.meta.data.account_campaigns.campaigns[id=' + parent['id'] + ']',
           inventory.get('checked_at'))
    others = [{'ad_id': x['id'], 'adset_id': x.get('adset', {}).get('id'),
               'status': x.get('status'), 'effective_status': x.get('effective_status'),
               'same_adset': x.get('adset', {}).get('id') == adset['id'],
               'path': 'sources.meta.data.active_ads.ads[id=' + x['id'] + ']',
               'observed_at': at}
              for x in active.get('ads', []) if x.get('id') != ad_id
              and x.get('campaign', {}).get('id') == parent['id']]
    return {
        'scope': {'account_id': ACCOUNT, 'entity_type': 'ad', 'entity_id': ad_id},
        'adset_id': adset['id'], 'campaign_id': parent['id'],
        'source_kind': 'saved_snapshot_not_live_readback', 'evidence': evidence,
        'other_observed_active_ads': others,
        'inventory_scope': 'active_ads_only_not_all_ads',
        'proposed_operation': {'entity_id': ad_id, 'status': 'PAUSED'},
        'explicit_parent_budget_mutation': False,
        'configured_budget_conclusion': 'A status-only ad operation requests no parent budget change; actual post-write settings are unverified.',
        'avoidable_spend_ils': None, 'forecast_ils': None,
        'redistribution': 'Other active ads exist in the saved inventory; this neither proves eligibility now nor predicts redistribution.',
        'blockers': [x['path'] for x in evidence if not x['present']],
        'limitations': [
            'Missing budget or cap is unknown, not zero or unlimited.',
            'Missing schedule does not mean an ongoing schedule.',
            'Daily budget is not asserted to be a hard daily spending cap.',
            'Historical ad spend is not a future saving.',
            'No current eligibility, budget sharing, pacing or account-level cap verified.',
            'Snapshot observation time and entity updated_time have different meanings.'],
        'semantic_sources': [SDK + name + '.py' for name in ('ad', 'adset', 'campaign')],
        'execution_allowed': False}
