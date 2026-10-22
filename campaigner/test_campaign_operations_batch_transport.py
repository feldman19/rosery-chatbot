"""Mocked HTTP only; never loads credentials or calls Meta."""
import io,json,unittest,urllib.error
from unittest.mock import patch
from urllib.parse import urlencode,parse_qs
from campaign_operations_transport import CampaignTransport,ProviderUnknown,ProviderRejected,ACCOUNT,PARENT,ORIGIN
from connections import NoRedirect


def descriptor(edge='adsets',**extra):
    params={'name':'Approved synthetic','status':'PAUSED','campaign_id':PARENT,'bid_strategy':'LOWEST_COST_WITHOUT_CAP','destination_type':'WEBSITE','is_dynamic_creative':'false','end_time':'None'}
    if edge=='adcreatives':params={'name':'Approved creative','object_story_spec':json.dumps({'page_id':'170018166205299','link_data':{'message':'Approved text','link':'https://roseryjewelry.com/'}}),'url_tags':'utm_source=meta&ad_id={{ad.id}}'}
    if edge=='ads':params={'name':'Approved ad','status':'PAUSED','adset_id':'700001','creative':json.dumps({'creative_id':'700002'})}
    params.update(extra)
    return {'method':'POST','relative_url':'act_'+ACCOUNT+'/'+edge,'body':urlencode(params)}

class BatchHTTPTests(unittest.TestCase):
    def call(self,items,response=None,error=None):
        if response is None:response=[{'code':200,'body':'{"id":"700001"}'} for _ in items]
        with patch('campaign_operations_transport.urllib.request.build_opener') as build:
            opener=build.return_value
            if error:opener.open.side_effect=error
            else:opener.open.return_value.__enter__.return_value.read.return_value=json.dumps(response).encode()
            result=CampaignTransport().batch('synthetic-only',items)
            return result,build
    def test_real_wire_root_post_header_auth_and_bounded_no_redirect(self):
        result,build=self.call([descriptor()]);request=build.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url,ORIGIN);self.assertEqual(request.get_method(),'POST');self.assertEqual(request.get_header('Authorization'),'Bearer synthetic-only');self.assertNotIn('synthetic-only',request.full_url);self.assertIsInstance(build.call_args.args[0],NoRedirect);self.assertEqual(build.return_value.open.call_args.kwargs['timeout'],25)
        batch=json.loads(parse_qs(request.data.decode())['batch'][0]);params=parse_qs(batch[0]['body']);self.assertEqual(params['status'],['PAUSED']);self.assertEqual(params['is_dynamic_creative'],['false']);self.assertNotIn('end_time',params)
    def test_three_dependency_wave_endpoint_types_supported(self):
        for edge in ('adsets','adcreatives','ads'):
            with self.subTest(edge=edge):self.assertEqual(len(self.call([descriptor(edge)])[0]),1)
    def test_creative_url_tags_preserved_exactly(self):
        _,build=self.call([descriptor('adcreatives')]);wire=json.loads(parse_qs(build.return_value.open.call_args.args[0].data.decode())['batch'][0]);self.assertEqual(parse_qs(wire[0]['body'])['url_tags'],['utm_source=meta&ad_id={{ad.id}}'])
    def test_partial_per_item_errors_returned_not_promoted(self):
        response=[{'code':200,'body':'{"id":"700001"}'},{'code':400,'body':'{"error":{"code":100}}'}]
        result,build=self.call([descriptor(),descriptor('ads')],response);self.assertEqual(result,response);self.assertEqual(build.return_value.open.call_count,1)
    def test_maximum_50_and_no_invalid_network_call(self):
        self.assertEqual(len(self.call([descriptor() for _ in range(50)])[0]),50)
        with patch('campaign_operations_transport.urllib.request.build_opener') as build:
            for items in ([],[descriptor() for _ in range(51)]):
                with self.assertRaises(ValueError):CampaignTransport().batch('synthetic',items)
            build.assert_not_called()
    def test_external_paths_active_unknown_fields_credentials_rejected(self):
        bad=[]
        for path in ('https://example.com/ads','act_12345/adsets','700001','act_'+ACCOUNT+'/adimages','act_'+ACCOUNT+'/ads?access_token=x'):
            d=descriptor();d['relative_url']=path;bad.append(d)
        bad += [descriptor(status='ACTIVE'),descriptor(access_token='secret'),descriptor(is_dynamic_creative='true'),descriptor(bid_strategy='COST_CAP')]
        d=descriptor();d['headers']={};bad.append(d)
        with patch('campaign_operations_transport.urllib.request.build_opener') as build:
            for d in bad:
                with self.subTest(d=d),self.assertRaises(ValueError):CampaignTransport().batch('synthetic',[d])
            build.assert_not_called()
    def test_nested_active_and_duplicate_body_rejected(self):
        bad=descriptor('adcreatives',object_story_spec=json.dumps({'status':'ACTIVE'}))
        duplicate=descriptor();duplicate['body']+='&status=PAUSED'
        with patch('campaign_operations_transport.urllib.request.build_opener') as build:
            for d in (bad,duplicate):
                with self.assertRaises(ValueError):CampaignTransport().batch('synthetic',[d])
            build.assert_not_called()
    def test_timeout_and_redirect_unknown_no_retry_no_private_error(self):
        for error in (TimeoutError('secret url'),urllib.error.HTTPError(ORIGIN,302,'private',{},io.BytesIO(b'')),urllib.error.HTTPError(ORIGIN,503,'private',{},io.BytesIO(b'{}'))):
            with self.subTest(error=type(error).__name__),patch('campaign_operations_transport.urllib.request.build_opener') as build:
                build.return_value.open.side_effect=error
                with self.assertRaises(ProviderUnknown) as caught:CampaignTransport().batch('synthetic',[descriptor()])
                self.assertNotIn('private',str(caught.exception));self.assertEqual(build.return_value.open.call_count,1)
    def test_explicit_outer_rejection_is_not_unknown(self):
        error=urllib.error.HTTPError(ORIGIN,400,'private',{},io.BytesIO(b'{"error":{"message":"private"}}'))
        with self.assertRaises(ProviderRejected):self.call([descriptor()],error=error)
    def test_missing_items_and_invalid_json_unknown(self):
        for raw in (b'[]',b'null',b'not-json',b'X'*2097153):
            with patch('campaign_operations_transport.urllib.request.build_opener') as build:
                build.return_value.open.return_value.__enter__.return_value.read.return_value=raw
                with self.assertRaises(ProviderUnknown):CampaignTransport().batch('synthetic',[descriptor()])
                self.assertEqual(build.return_value.open.call_count,1)

if __name__=='__main__':unittest.main()
