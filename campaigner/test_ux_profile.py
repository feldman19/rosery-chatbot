"""Offline UI behavior, using public profile fixtures and no provider calls."""
import json
import subprocess
import unittest

from test_card_component import DOM, HELPERS, functions


class ProfileUITests(unittest.TestCase):
    def run_js(self, body):
        extra = functions('profileDate', 'profileChanges', 'profileListInput',
                          'renderProfile', 'dontPayload', 'dismissChoice')
        harness = DOM.replace('api=async()=>{}',
                              'api=async(path,body)=>{posts.push({path,body});return businessProfile}')
        setup = """
let posts=[],businessProfile={sections:[]},profileDraft={};
const confirm=()=>true,closeSheet=()=>{},loadProfile=async()=>{};
const document={body:el('body')};
"""
        result = subprocess.run(['node'], input=HELPERS+'\n'+extra+'\n'+harness+'\n'+setup+body,
                                capture_output=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_profile_groups_changed_keys_and_save(self):
        from business_profile import BusinessProfile
        # Construct default public data without writing any local business files.
        from unittest.mock import patch
        with patch.object(BusinessProfile, '_load', return_value={
                'schema_version': 1, 'version': 0, 'settings': {}, 'donts': [], 'history': []}):
            fixture = BusinessProfile('unused').get()
        self.run_js('businessProfile='+json.dumps(fixture, ensure_ascii=True)+r""";
renderProfile();const root=$('profile-content');
for(const label of ['מומלץ לכבות','בטוח להשאיר','תלוי בך','לא ידוע','המותג','מוצרים','תקציב','ימים מיוחדים'])assert.ok(allText(root).includes(label));
const unknown=root.querySelectorAll('.profile-group').find(n=>n.tag==='details');assert.ok(unknown);assert.ok(!unknown.open);
assert.ok(root.querySelectorAll('a').every(a=>a.href.startsWith('https://')));
const toggle=root.querySelectorAll('button').find(n=>n.role==='switch');toggle.onclick();
const key=businessProfile.sections[0].settings.find(r=>r.label===toggle['aria-label'].split(' · ')[0]).key;
profileDraft.tone_notes='';
assert.deepEqual(Object.keys(profileChanges()),[key]);
(async()=>{await root.children.at(-1).onclick();assert.equal(posts[0].path,'/api/business-profile');assert.deepEqual(posts[0].body,{settings:{[key]:profileDraft[key]}})})().catch(e=>{console.error(e);process.exitCode=1});
""")

    def test_dont_again_posts_selected_scope_and_reason(self):
        self.run_js(r"""
assert.deepEqual(dontPayload({features:[],ad_id:'123'},'everywhere',''),{kind:'disable_enhancements',scope:'everywhere',entity_id:'',reason:'לא להציע את זה שוב'});
assert.equal(dontPayload({action:'SCALE',entity_id:'456'},'ad','סיבה').scope,'adset');
dismissChoice({action:'PAUSE',entity_id:'123'});
const panel=document.body.querySelector('.sheet-panel');
panel.querySelector('select').value='ad';panel.querySelector('input').value='סיבה';
(async()=>{await panel.querySelectorAll('button').find(b=>b.textContent==='לא להציע את זה שוב').onclick();assert.deepEqual(posts,[{path:'/api/business-profile/dont',body:{kind:'PAUSE',scope:'ad',entity_id:'123',reason:'סיבה'}}])})().catch(e=>{console.error(e);process.exitCode=1});
""")

    def test_catalog_and_multiple_media_use_existing_approval(self):
        self.run_js(r"""
for(const features of [[{feature:'standard_enhancements_catalog'}],[{feature:'music'},{feature:'image_animation'}]]){
const c=driftCard({ad_id:'123',name:'קטלוג',in_app:true,features});
const primary=c.querySelector('.primary');assert.equal(primary.tag,'button');assert.ok(primary.textContent.startsWith('לכבות'));primary.onclick();assert.equal(clicked,'drift:123');}
""")

    def test_missing_sales_is_unknown_and_window_is_not_mixed(self):
        self.run_js(r"""
assert.equal(salesLine(null),'לא ידוע');assert.equal(salesLine({status:'unavailable',store_orders:0}),'לא ידוע');
assert.ok(salesLine({status:'ready',store_orders:0,store_revenue:0,new_customer_percent:null}).includes('0 הזמנות'));
report={windows:{'14':{ads:[{id:'123',ad_sales:{status:'ready',store_orders:99}}]}}};assert.equal(salesFor({entity_id:'123'}),undefined);
const c=actionCard({action:'PAUSE',entity_id:'123',numbers:{}});
const tile=c.querySelectorAll('.key-tile').find(n=>allText(n).includes('ספירה נפרדת'));assert.ok(allText(tile).includes('לא ידוע'));assert.ok(allText(c).includes('UTM'));
""")


if __name__ == '__main__':
    unittest.main()
