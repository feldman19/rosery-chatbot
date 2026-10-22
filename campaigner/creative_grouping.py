"""Advisory grouping schema; membership is explicit, never fuzzy text matching."""
import re
from batch_limits import MAX_ADSETS,MAX_ADS


def validate_proposed_adsets(groups,rows):
    if not isinstance(groups,list) or not 0<=len(groups)<=MAX_ADSETS:
        raise ValueError('Proposed adsets must contain one to three groups')
    concepts={}
    for row in rows:
        key=row.get('concept_key')
        if not isinstance(key,str) or not 1<=len(key)<=100:raise ValueError('Invalid concept identity')
        concepts.setdefault(key,[]).append(row)
    paired=set()
    for key,items in concepts.items():
        if len(items)==2 and {x.get('format') for x in items}=={'feed','story'}:
            a,b=items
            if a.get('paired_asset_id')==b.get('asset_id') and b.get('paired_asset_id')==a.get('asset_id'):paired.add(key)
    covered=set();keys=set();result=[]
    for group in groups:
        if not isinstance(group,dict) or set(group)!={'key','label','rationale','concept_keys'}:raise ValueError('Invalid proposed adset schema')
        key=group['key']
        if not isinstance(key,str) or not re.fullmatch('[A-Za-z0-9_-]{1,80}',key) or key in keys:raise ValueError('Invalid or duplicate proposed adset key')
        for field in ('label','rationale'):
            value=group[field]
            if not isinstance(value,str) or not value.strip() or len(value)>1000:raise ValueError('Proposed adset explanation required')
        members=group['concept_keys']
        if not isinstance(members,list) or not 1<=len(members)<=MAX_ADS:raise ValueError('Explicit concept membership required')
        for member in members:
            if not isinstance(member,str) or member not in paired or member in covered:raise ValueError('Unknown, unpaired or duplicated concept membership')
            covered.add(member)
        keys.add(key);result.append({**group,'concept_keys':list(members)})
    if covered!=paired:raise ValueError('Proposed adsets do not cover every paired concept')
    return result
