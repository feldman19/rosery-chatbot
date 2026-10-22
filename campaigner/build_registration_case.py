"""Trusted local collector step; no credentials/network/remote mutations."""
from pathlib import Path
import hashlib
import json
import shutil
from datetime import datetime, timezone
from registration import registration_report, ACCOUNT, CAMPAIGN

root=Path(__file__).resolve().parent
names=['.local/registration-meta.json','.local/registration-site.json',
       '../outputs/rosery-launch-registration-tracking-2026-10-16.json',
       '../outputs/rosery-registration-scale-recheck-2026-10-16.json']
files={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in names}
manifest={'tenant':'rosery','account_id':ACCOUNT,'campaign_id':CAMPAIGN,'files':files}
revision=hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()
history=root/'.local'/'reg-history'/revision[:16]
history.mkdir(parents=True,exist_ok=True)
for index,name in enumerate(names):
    dest=history/(str(index)+'.json')
    if not dest.exists():shutil.copyfile(root/name,dest)
path=root/'.local'/'registration-manifest.json'
candidate=root/'.local'/'registration-candidate.json'
candidate.write_text(json.dumps(manifest,indent=2),encoding='utf-8')
report=registration_report(root,manifest_override=candidate)
candidate.replace(path)
assessment=datetime.now(timezone.utc).strftime('%H%M%S%f')
(history/('review-'+assessment+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'revision':revision,'checks':len(report['checks']),'completed':report['completed_checks'],'state':report['state']}))
