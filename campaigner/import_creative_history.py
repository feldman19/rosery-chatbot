"""Explicit owner-local import of the existing ROSERY evidence export. No network."""
import argparse
import hashlib
import json
from pathlib import Path
from decision_memory import DecisionMemory


def load_export(directory):
    root = Path(directory).resolve(strict=True)
    names = ('analysis.json','normalized-ads.json','creative-groups.json','provenance.json')
    raw = {}
    for name in names:
        path = root/name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 30*1024*1024:
            raise ValueError('Bounded plain export files required')
        raw[name] = path.read_bytes()
    values = {name:json.loads(data) for name,data in raw.items()}
    analysis = values['analysis.json']; provenance=values['provenance.json']
    if hashlib.sha256(raw['analysis.json']).hexdigest() != provenance.get('analysis_sha256'):
        raise ValueError('Analysis provenance mismatch')
    checks = provenance.get('checks') or {}
    if not all(checks.get(k) is True for k in ('unique_ad_ids','pagination_complete_for_live_and_archive','account_purchases_match','account_revenue_match')):
        raise ValueError('Required reconciliation checks missing')
    # Validate source file hashes without following paths outside the receipt folder.
    for name, expected in provenance['sources'].items():
        path=(root/name).resolve(strict=True)
        if not path.is_relative_to(root) or path.is_symlink() or not path.is_file():
            raise ValueError('Source receipt outside export')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
            raise ValueError('Source receipt changed')
    ads = values['normalized-ads.json']
    if len(ads) != analysis['ads'] or len({r['id'] for r in ads}) != len(ads):
        raise ValueError('Export count or identities changed')
    # Recompute aggregation, retaining null conversion rows rather than filling zeros.
    from decimal import Decimal
    summed = {k:sum((Decimal(str(r[k])) for r in ads if r[k] is not None),Decimal(0)) for k in ('spend','purchases','revenue')}
    totals = analysis['account_totals']
    if abs(summed['spend']-Decimal(str(totals['spend']))) > Decimal('.01') or any(summed[k]!=Decimal(str(totals[k])) for k in ('purchases','revenue')):
        raise ValueError('Account reconciliation failed')
    visual={r['creative_id']:r for r in analysis['visual_observations']}
    for value in visual.values():
        path=(root/value['evidence_file']).resolve(strict=True)
        if not path.is_relative_to(root) or hashlib.sha256(path.read_bytes()).hexdigest()!=value['sha256']:
            raise ValueError('Visual evidence changed')
    rows=[]
    sanitized_names=0
    for ad in ads:
        obs=visual.get(ad['creative_id'])
        display={k:(ad[k] or '') for k in ('id','name','creative_id','campaign_name','format')}
        for key in ('name','campaign_name'):
            if any(ord(c)<32 for c in display[key]):
                display[key]=''.join(' ' if ord(c)<32 else c for c in display[key])
                sanitized_names+=1
        rows.append(display |
                    {k:ad[k] for k in ('spend','purchases','revenue')} |
                    {'visual_observation':obs['observation'] if obs else ''})
    return {'account_id':analysis['account_id'],'captured_at':analysis['retrieved_at'],'currency':analysis['currency'],
            'coverage':'data_maximum; exact insights start date not returned; archived/deleted included',
            'source_hashes':{name:hashlib.sha256(data).hexdigest() for name,data in raw.items()},
            'rows':rows,'limits':analysis['limits']+[
                'Host-imported and hash-checked export; connector receipts are evidence, not independent platform verification.',
                'Visual observations are prior assistant descriptions, not pixels supplied to this model or independent semantic review.',
                f'Control characters replaced with spaces in {sanitized_names} display labels; original export bytes retained and hashed.']}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',required=True)
    parser.add_argument('--database',required=True)
    parser.add_argument('--business',required=True)
    args=parser.parse_args()
    payload=load_export(args.source)
    memory=DecisionMemory(args.database)
    memory.bind(args.business,payload['account_id'])
    print(json.dumps(memory.ingest(args.business,payload['account_id'],payload)))


if __name__=='__main__':
    main()
