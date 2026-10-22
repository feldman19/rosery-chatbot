"""Bounded Meta batch planning/parsing, with injected transport only.

No credentials, HTTP client, approval or advertising execution integration exists here.
Host must persist export_state BEFORE sending a batch (checkpoint callback), then
read back every accepted write before calling it verified. A batch is not atomic.
Official SDK wire shape: FacebookAdsApiBatch.add/execute in
https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/api.py
This module deliberately does NOT implement SDK's retry-batch convenience.
"""
from copy import deepcopy
import hashlib,json,re
from urllib.parse import urlencode

ACCOUNT='393059510164294';PARENT='120246379886090730';MAX_BATCH=50
CREATION={'adsets':{'name','status','campaign_id','targeting','optimization_goal','billing_event','promoted_object','start_time','end_time','daily_budget','lifetime_budget'},'adcreatives':{'name','object_story_spec'},'ads':{'name','status','adset_id','creative','tracking_specs'}}
class BatchRejected(ValueError):pass

def _json(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)
def _id(value):return isinstance(value,str) and bool(re.fullmatch(r'[0-9]{5,30}',value))
def _safe(value):
    if isinstance(value,dict):
        for k,v in value.items():
            if not isinstance(k,str) or re.search(r'token|secret|authorization|password|asset_feed_spec',k,re.I):raise BatchRejected('Unsupported credential or creative field')
            if k=='status' and v!='PAUSED':raise BatchRejected('Only PAUSED writes allowed')
            _safe(v)
    elif isinstance(value,list):
        for x in value:_safe(x)
    elif not isinstance(value,(str,int,float,bool,type(None))):raise BatchRejected('JSON values required')
    if isinstance(value,str) and ('{result=' in value or value=='ACTIVE'):raise BatchRejected('Provider references and activation unsupported')

def plan_batches(items,owned_ids=()):
    if not isinstance(items,list) or not 1<=len(items)<=200:raise BatchRejected('One to 200 bounded items required')
    owned=set(owned_ids)
    if any(not _id(x) for x in owned):raise BatchRejected('Host-owned numeric IDs required')
    byid={};clean=[]
    for original in items:
        if not isinstance(original,dict) or set(original)!={'id','method','relative_url','params','depends_on'}:raise BatchRejected('Exact item fields required')
        row=deepcopy(original);identity=row['id'];path=row['relative_url'];params=row['params'];deps=row['depends_on']
        if not isinstance(identity,str) or not re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}',identity) or identity in byid:raise BatchRejected('Unique item ID required')
        if not isinstance(deps,list) or len(deps)>50 or any(not isinstance(x,str) for x in deps) or len(set(deps))!=len(deps) or identity in deps:raise BatchRejected('Invalid dependencies')
        if not isinstance(params,dict) or len(_json(params))>65536:raise BatchRejected('Bounded parameters required')
        _safe(params)
        if row['method']=='POST' and isinstance(path,str) and path.startswith('act_'+ACCOUNT+'/'):
            edge=path.split('/')[-1]
            if path!='act_'+ACCOUNT+'/'+edge or edge not in CREATION or not set(params)<=CREATION[edge]:raise BatchRejected('Unsupported creation endpoint or field')
            if edge in ('adsets','ads') and params.get('status')!='PAUSED':raise BatchRejected('Paused creation required')
            if edge=='adsets' and params.get('campaign_id')!=PARENT:raise BatchRejected('Fixed parent required')
            if edge=='adcreatives' and not isinstance(params.get('object_story_spec'),dict):raise BatchRejected('Standard creative required')
        elif _id(path) and path in owned:
            if row['method']=='GET':
                if set(params)!={'fields'} or not isinstance(params['fields'],str) or not re.fullmatch('[a-z_,]{1,500}',params['fields']):raise BatchRejected('Read fields required')
            elif row['method']=='POST':
                if params!={'status':'PAUSED'}:raise BatchRejected('Only owned-node pause supported')
            else:raise BatchRejected('Unsupported method')
        else:raise BatchRejected('External or unowned endpoint rejected')
        byid[identity]=row;clean.append(row)
    def refs(value):
        if isinstance(value,dict):
            if '$ref' in value:
                if set(value)!={'$ref','field'} or value['field']!='id' or value['$ref'] not in byid:raise BatchRejected('Only known ID references supported')
                return {value['$ref']}
            return set().union(*(refs(v) for v in value.values())) if value else set()
        if isinstance(value,list):return set().union(*(refs(v) for v in value)) if value else set()
        return set()
    for row in clean:
        if any(dep not in byid for dep in row['depends_on']) or not refs(row['params'])<=set(row['depends_on']):raise BatchRejected('References require explicit known dependencies')
    remaining=[r['id'] for r in clean];done=set();waves=[]
    while remaining:
        ready=[x for x in remaining if set(byid[x]['depends_on'])<=done]
        if not ready:raise BatchRejected('Dependency cycle rejected')
        waves.append([ready[i:i+MAX_BATCH] for i in range(0,len(ready),MAX_BATCH)])
        done.update(ready);remaining=[x for x in remaining if x not in done]
    if len(_json(clean))>1048576:raise BatchRejected('Plan too large')
    return {'items':clean,'waves':waves,'owned_ids':sorted(owned),'fingerprint':hashlib.sha256(_json({'items':clean,'owned_ids':sorted(owned)}).encode()).hexdigest(),'max_batch':MAX_BATCH,'requires_host_approval':True,'readback_required':True}

def parse_batch_results(items,response):
    if not isinstance(items,list) or len(items)>MAX_BATCH:raise BatchRejected('At most 50 response items required')
    outcomes=[]
    if not isinstance(response,list) or len(response)!=len(items):response=[None]*len(items)
    for item,row in zip(items,response):
        result={'id':item['id'],'state':'unknown','readback_required':item['method']=='POST'}
        if isinstance(row,dict) and type(row.get('code')) is int:
            body=row.get('body')
            try:
                if isinstance(body,str) and len(body)<=65536:body=json.loads(body)
            except ValueError:body=None
            if isinstance(body,dict):
                if 400<=row['code']<500 and isinstance(body.get('error'),dict):result.update(state='rejected',error='Provider rejected this item')
                elif 200<=row['code']<300 and not body.get('error'):
                    if item['method']=='GET' or _id(body.get('id')) or body.get('success') is True:
                        result['state']='accepted';result['provider_id']=body.get('id') if _id(body.get('id')) else None
        outcomes.append(result)
    return outcomes

class BatchSession:
    """Injected send(batch) receives only relative request descriptors. No auto retry.

    checkpoint(exported_state) MUST durably save state before transport. A restored
    pending state after a crash is conservatively unknown and cannot be resent.
    """
    def __init__(self,plan,state=None):
        canonical=plan_batches(plan.get('items'),plan.get('owned_ids',()))
        if canonical!=plan:raise BatchRejected('Validated canonical batch plan required')
        self.plan=deepcopy(canonical);self.items={x['id']:x for x in plan['items']};self.outcomes={x:{'id':x,'state':'pending'} for x in self.items}
        if state is not None:
            if state.get('fingerprint')!=plan['fingerprint'] or set(state.get('outcomes',{}))!=set(self.items):raise BatchRejected('State belongs to another plan')
            self.outcomes=deepcopy(state['outcomes'])
            if any(x.get('state') not in ('pending','accepted','rejected','blocked','unknown') for x in self.outcomes.values()):raise BatchRejected('Invalid checkpoint')
    def export_state(self):return {'fingerprint':self.plan['fingerprint'],'outcomes':deepcopy(self.outcomes)}
    def confirm_readback(self,identity,provider_id):
        """Host calls only AFTER its separate exact provider readback succeeds.

        This method performs no verification itself; it records that host result.
        Do not expose it as a browser-authored verification endpoint.
        """
        result=self.outcomes.get(identity)
        if not result or result.get('state')!='accepted' or not _id(provider_id) or result.get('provider_id')!=provider_id:raise BatchRejected('Exact accepted ID readback required')
        result['readback_verified']=True

    def run_next(self,send,checkpoint):
        if any(x['state']=='unknown' for x in self.outcomes.values()):return {'state':'unknown','items':[],'retry_allowed':False}
        changed=True
        while changed:
            changed=False
            for identity,row in self.items.items():
                if self.outcomes[identity]['state']=='pending' and any(self.outcomes[d]['state'] in ('rejected','blocked') for d in row['depends_on']):
                    self.outcomes[identity]={'id':identity,'state':'blocked'};changed=True
        ready=[x for x,r in self.items.items() if self.outcomes[x]['state']=='pending' and all(self.outcomes[d]['state']=='accepted' and (not self.outcomes[d].get('readback_required') or self.outcomes[d].get('readback_verified') is True) for d in r['depends_on'])][:MAX_BATCH]
        if not ready:return {'state':'awaiting_readback' if any(x['state']=='pending' for x in self.outcomes.values()) else 'finished','items':[],'retry_allowed':False}
        def resolve(value):
            if isinstance(value,dict):
                if '$ref' in value:
                    resolved=self.outcomes[value['$ref']].get('provider_id')
                    if not _id(resolved):raise BatchRejected('Dependency has no verified response ID')
                    return resolved
                return {k:resolve(v) for k,v in value.items()}
            if isinstance(value,list):return [resolve(x) for x in value]
            return value
        batch=[]
        for identity in ready:
            row=self.items[identity];params=resolve(row['params']);wire={'method':row['method'],'relative_url':row['relative_url']}
            values={k:_json(v) if isinstance(v,(dict,list,bool)) else str(v) for k,v in params.items()}
            if row['method']=='GET':wire['relative_url']+='?'+urlencode(values)
            else:wire['body']=urlencode(values)
            batch.append(wire)
        for identity in ready:self.outcomes[identity]={'id':identity,'state':'unknown'}
        checkpoint(self.export_state())
        try:response=send(batch)
        except Exception:return {'state':'unknown','items':deepcopy([self.outcomes[x] for x in ready]),'retry_allowed':False}
        outcomes=parse_batch_results([self.items[x] for x in ready],response)
        for result in outcomes:self.outcomes[result['id']]=result
        checkpoint(self.export_state())
        return {'state':'unknown' if any(x['state']=='unknown' for x in outcomes) else 'completed_wave','items':outcomes,'retry_allowed':False}
