"""Bounded Meta v26 transport, no retries, redirects, credentials or logging.

Wire field evidence: Meta's official facebook-python-business-sdk adaccount.py
create_ad_set/create_ad_creative/create_ad/create_ad_image and
adcreativelinkdata.py/objectstoryspec.py. Multipart source upload follows SDK
allow_file_upload=True on /adimages. SDK presence does not prove account support.
https://github.com/facebook/facebook-python-business-sdk/tree/main/facebook_business/adobjects
"""
import json
from batch_limits import MAX_UPLOAD_IMAGE_BYTES
import re
import secrets
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode,parse_qsl
from connections import NoRedirect

ACCOUNT='393059510164294'
PARENT='120246379886090730'
ORIGIN='https://graph.facebook.com/v26.0/'

class ProviderRejected(ValueError):
    def __init__(self,message='Meta rejected operation',details=None):
        super().__init__(message)
        self.details=details or {}

def provider_rejection(result,token):
    error=result.get('error') or {}
    details={k:error[k] for k in ('code','error_subcode','type') if isinstance(error.get(k),(str,int))}
    for key in ('message','error_user_title','error_user_msg'):
        if isinstance(error.get(key),str):details[key]=error[key].replace(token,'[redacted]')[:1500]
    return ProviderRejected(details=details)

class ProviderUnknown(ValueError):
    pass

class CampaignTransport:
    def _node(self,node):
        if node=='act_'+ACCOUNT or isinstance(node,str) and re.fullmatch(r'[0-9]{5,30}',node):return node
        raise ValueError('Unsupported Meta node')

    def _request(self,token,path,method='GET',params=None,data=None,content_type=None):
        if not isinstance(token,str) or not token:raise ValueError('Execution credential missing')
        headers={'Authorization':'Bearer '+token,'Accept':'application/json'}
        if method=='GET':url=ORIGIN+path+('?' + urlencode(params or {}) if params else '')
        else:
            url=ORIGIN+path
            if data is None:
                value={k:json.dumps(v,separators=(',',':')) if isinstance(v,(dict,list,bool)) else str(v) for k,v in (params or {}).items()}
                data=urlencode(value).encode();content_type='application/x-www-form-urlencoded'
            headers['Content-Type']=content_type
        request=urllib.request.Request(url,data=data,headers=headers,method=method)
        try:
            with urllib.request.build_opener(NoRedirect()).open(request,timeout=25) as response:raw=response.read(2097153)
            if len(raw)>2097152:raise ValueError()
            result=json.loads(raw)
            if not isinstance(result,dict):raise ValueError()
            if result.get('error'):raise provider_rejection(result,token)
            return result
        except ProviderRejected:raise
        except urllib.error.HTTPError as error:
            # Only explicit 4xx Graph rejections are definite; all other POST
            # failures may have reached Meta and must never be blindly retried.
            if 400<=error.code<500:
                try:
                    result=json.loads(error.read(65537))
                    if isinstance(result,dict) and result.get('error'):raise provider_rejection(result,token) from None
                except ProviderRejected:raise
                except Exception:pass
            raise ProviderUnknown('Meta outcome unavailable') from None
        except Exception:raise ProviderUnknown('Meta outcome unavailable') from None

    def batch(self,token,descriptors):
        """Send one already-approved dependency wave; never retry partial/unknown.

        Wire descriptors follow Meta's official SDK FacebookAdsApiBatch.add/execute.
        This transport performs no approval, reference resolution or readback.
        Creation only; accepted item responses are NOT verified creation receipts.
        """
        from meta_batch import plan_batches,BatchRejected
        if not isinstance(token,str) or not token:raise ValueError('Execution credential missing')
        if not isinstance(descriptors,list) or not 1<=len(descriptors)<=50:raise ValueError('One to 50 batch items required')
        wire=[]
        nested={'targeting','promoted_object','object_story_spec','creative','tracking_specs','asset_feed_spec'}
        for index,item in enumerate(descriptors):
            if not isinstance(item,dict) or set(item)!={'method','relative_url','body'} or item['method']!='POST':raise ValueError('Only explicit paused creation descriptors supported')
            path=item['relative_url'];body=item['body']
            if path not in ('act_'+ACCOUNT+'/adsets','act_'+ACCOUNT+'/adcreatives','act_'+ACCOUNT+'/ads'):raise ValueError('Fixed account creation paths required')
            if not isinstance(body,str) or len(body)>65536:raise ValueError('Bounded form body required')
            try:pairs=parse_qsl(body,keep_blank_values=True,strict_parsing=True,max_num_fields=40,encoding='utf-8',errors='strict')
            except (ValueError,UnicodeError):raise ValueError('Invalid form body') from None
            if len({key for key,value in pairs})!=len(pairs):raise ValueError('Duplicate batch parameters rejected')
            params=dict(pairs)
            try:
                for key in nested&params.keys():
                    params[key]=json.loads(params[key],parse_constant=lambda value:(_ for _ in ()).throw(ValueError()))
                    if not isinstance(params[key],(dict,list)):raise ValueError()
            except (ValueError,TypeError):raise ValueError('Invalid structured batch field') from None
            extra={}
            if path.endswith('/adcreatives') and 'asset_feed_spec' in params:
                from canary_plan import validate_resolved_candidate
                validate_resolved_candidate(params)
                extra['asset_feed_spec']=params.pop('asset_feed_spec')
            if path.endswith('/adsets'):
                for key,allowed in [('bid_strategy',('LOWEST_COST_WITHOUT_CAP',)),('destination_type',('WEBSITE',)),('is_dynamic_creative',('false','False','0'))]:
                    if key in params:
                        value=params.pop(key)
                        if value not in allowed:raise ValueError('Unsupported adset batch setting')
                        extra[key]='false' if key=='is_dynamic_creative' else value
                if params.get('end_time') in ('None','null',''):params.pop('end_time',None)
            elif path.endswith('/adcreatives') and 'url_tags' in params:
                value=params.pop('url_tags')
                if len(value)>4000 or any(ord(c)<32 for c in value) or re.search(r'access_token|client_secret|authorization|password',value,re.I):raise ValueError('Invalid tracking parameters')
                extra['url_tags']=value
            try:plan_batches([{'id':'item'+str(index),'method':'POST','relative_url':path,'params':params,'depends_on':[]}])
            except (BatchRejected,ValueError,TypeError):raise ValueError('Unsafe batch item') from None
            # Re-encode validated values; never forward extra headers, names,
            # dependencies, arbitrary URLs, credential fields or raw form strings.
            params.update(extra)
            encoded={key:json.dumps(value,separators=(',',':')) if isinstance(value,(dict,list,bool)) else str(value) for key,value in params.items()}
            wire.append({'method':'POST','relative_url':path,'body':urlencode(encoded)})
        data=urlencode({'batch':json.dumps(wire,separators=(',',':'))}).encode()
        if len(data)>1048576:raise ValueError('Batch payload too large')
        request=urllib.request.Request(ORIGIN,data=data,headers={'Authorization':'Bearer '+token,'Accept':'application/json','Content-Type':'application/x-www-form-urlencoded'},method='POST')
        try:
            with urllib.request.build_opener(NoRedirect()).open(request,timeout=25) as response:raw=response.read(2097153)
            if len(raw)>2097152:raise ValueError()
            result=json.loads(raw)
            if isinstance(result,dict) and result.get('error'):raise ProviderRejected('Meta rejected batch')
            if not isinstance(result,list) or len(result)!=len(wire):raise ValueError()
            return result
        except ProviderRejected:raise
        except urllib.error.HTTPError as error:
            if 400<=error.code<500:
                try:
                    result=json.loads(error.read(65537))
                    if isinstance(result,dict) and result.get('error'):raise ProviderRejected('Meta rejected batch') from None
                except ProviderRejected:raise
                except Exception:pass
            raise ProviderUnknown('Meta batch outcome unavailable') from None
        except Exception:raise ProviderUnknown('Meta batch outcome unavailable') from None

    def read(self,token,node,fields):
        return self._request(token,self._node(node),params={'fields':fields})

    def create(self,token,edge,params):
        if edge not in ('adsets','adcreatives','ads'):raise ValueError('Unsupported creation edge')
        return self._request(token,'act_'+ACCOUNT+'/'+edge,'POST',params)

    def update(self,token,node,params):
        if not (set(params)=={'status'} and params['status'] in ('PAUSED','ACTIVE') or set(params) in ({'daily_budget'},{'lifetime_budget'}) and str(next(iter(params.values()))).isdigit()):raise ValueError('Unsupported mutation')
        return self._request(token,self._node(node),'POST',params)

    def swap_creative(self,token,ad_id,creative_id):
        """Owner-approved only (owner_actions): put an existing creative of this account on one ad."""
        if not isinstance(creative_id,str) or not re.fullmatch(r'[0-9]{5,30}',creative_id):raise ValueError('Unsupported mutation')
        return self._request(token,self._node(ad_id),'POST',{'creative':{'creative_id':creative_id}})

    def upload(self,token,raw,mime,name):
        if mime not in ('image/png','image/jpeg') or not isinstance(raw,bytes) or not 0<len(raw)<=MAX_UPLOAD_IMAGE_BYTES:raise ValueError('Bounded PNG/JPEG required')
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,120}',name):raise ValueError('Safe upload name required')
        boundary='----ROSERY'+secrets.token_hex(16)
        data=(f'--{boundary}\r\nContent-Disposition: form-data; name="filename"; filename="{name}"\r\nContent-Type: {mime}\r\n\r\n'.encode()+raw+f'\r\n--{boundary}--\r\n'.encode())
        return self._request(token,'act_'+ACCOUNT+'/adimages','POST',data=data,content_type='multipart/form-data; boundary='+boundary)

    def image_info(self,token,image_hash):
        if not isinstance(image_hash,str) or not re.fullmatch(r'[A-Za-z0-9]{8,128}',image_hash):raise ValueError('Invalid image hash')
        return self._request(token,'act_'+ACCOUNT+'/adimages',params={'hashes':json.dumps([image_hash]),'fields':'hash,name,account_id'})

    def inventory(self,token,parent):
        """Official Campaign.get_ad_sets/get_ads edges; never follow next URLs."""
        if parent!=PARENT:raise ValueError('Fixed campaign inventory only')
        deadline=time.monotonic()+60
        result={'complete':True,'adsets':[],'ads':[]}
        for edge,fields in [('adsets','id,account_id,campaign_id,name,status,effective_status,daily_budget,lifetime_budget'),('ads','id,account_id,campaign_id,adset_id,name,status,effective_status')]:
            cursor=None
            for page in range(10):
                if time.monotonic()>=deadline:raise ValueError('Inventory deadline exceeded')
                params={'fields':fields,'limit':100}
                if cursor:params['after']=cursor
                raw=self._request(token,parent+'/'+edge,params=params)
                if not isinstance(raw.get('data'),list) or len(raw['data'])>100:raise ValueError('Inventory page invalid')
                result[edge].extend(raw['data']);paging=raw.get('paging') or {}
                if not paging.get('next'):break
                new=(paging.get('cursors') or {}).get('after')
                if not isinstance(new,str) or not 1<=len(new)<=2048 or new==cursor:raise ValueError('Inventory cursor invalid')
                cursor=new
            else:raise ValueError('Inventory exceeds bounded coverage')
        return result
