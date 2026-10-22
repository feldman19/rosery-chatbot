/* Mocked launch-intake browser flow. No network, providers or business writes. */
const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const launchHelpers=require('./launch-ui.js');
const source=fs.readFileSync(__dirname+'/launch-ui.js','utf8');
class Element{
  constructor(tag,text=''){this.tagName=tag.toUpperCase();this.textContent=text;this.children=[];this.parentNode=null;this.dataset={};this.style={};this.value='';this.hidden=false;this.disabled=false;this.attributes={}}
  append(...nodes){for(const node of nodes){if(node.parentNode)node.parentNode.children=node.parentNode.children.filter(x=>x!==node);node.parentNode=this;this.children.push(node)}}
  replaceChildren(...nodes){for(const node of this.children)node.parentNode=null;this.children=[];this.append(...nodes)}
  insertBefore(node,before){if(node.parentNode)node.parentNode.children=node.parentNode.children.filter(x=>x!==node);const index=this.children.indexOf(before);node.parentNode=this;if(index<0)this.children.push(node);else this.children.splice(index,0,node)}
  setAttribute(key,value){this.attributes[key]=value}
  focus(){this.focused=true}
}
function walk(root){return root.children.flatMap(node=>[node,...walk(node)])}
async function settle(){await new Promise(resolve=>setImmediate(resolve))}
function setup({mode='success',initialRequest=null,driveConfigured=false,authorizationUrl=null,materials=null}={}){
  const calls=[],timers=[],parent=new Element('section');parent.append(new Element('div','heading'));
  let report={request:initialRequest,materials,stages:[],gaps:[],campaign_candidates:[],sources:[],live_action_allowed:false},resolveSave=null;
  const context={URL,Intl,Date,JSON,WeakMap,Number,Object,Error,console,token:'mock-session',
    document:{head:new Element('head'),hidden:false,addEventListener(){}},setInterval:fn=>timers.push(fn),
    el:(tag,text='',cls)=>{const node=new Element(tag,text);node.className=cls||'';return node},
    fetch:async(url,options)=>{const body=options.body?JSON.parse(options.body):null;calls.push({url,method:options.method||'GET',body});
      if(url==='/api/launch')return {ok:true,status:200,json:async()=>structuredClone(report)};
      if(url==='/api/drive/oauth/status')return {ok:true,status:200,json:async()=>({client_configured:true,connection:{configured:driveConfigured}})};
      if(url==='/api/drive/oauth/begin')return {ok:true,status:200,json:async()=>({authorization_url:authorizationUrl})};
      if(url==='/api/launch/materials/read')return {ok:true,status:200,json:async()=>({started:true})};
      assert.equal(url,'/api/launch/request');assert.equal(options.method,'POST');assert.deepEqual(Object.keys(body).sort(),['brief','drive_urls']);
      if(mode==='reject')return {ok:false,status:400,json:async()=>({error:'invalid'})};
      if(mode==='deferred')await new Promise(resolve=>{resolveSave=resolve});
      report={request:{id:'launch-one',revision:1,...body,brief:launchHelpers.launchNormalizeBrief(body.brief),drive_urls:launchHelpers.launchDriveUrls(body.drive_urls.join('\n')),created_at:'2026-10-17T12:00:00Z',saved_at:'2026-10-17T12:00:00Z',goal:'sales',execution_approval:false},
        work_item:{id:'work-one',state:'preparation_required',execution_available:false},
        stages:[{id:'assets',title:'חומרי ההשקה',state:body.drive_urls.length?'saved':'missing',detail:'קישורים נשמרו בלבד; תוכן הקבצים טרם נקרא'},
                {id:'measurement',title:'מדידה',state:'missing',detail:'בדיקת רכישה באתר טרם הושלמה'}],
        gaps:[{id:'budget',title:'תקציב להשקה',detail:'לא נמצא תקציב שאושר להשקה הזאת'}],
        campaign_candidates:[{id:'campaign-source',name:'קמפיין קיים',effective_status:'PAUSED',observed_at:'2026-10-17T11:59:00Z',is_recommendation:false}],
        sources:[{source:'Meta',verified:true,detail:'נקראו הגדרות קמפיינים',observed_at:'2026-10-17T11:59:00Z'}],live_action_allowed:false};
      if(mode==='unknown')throw Error('mock network loss after save');
      return {ok:true,status:200,json:async()=>({saved:true,request:structuredClone(report.request),work_item:report.work_item})};
    }};
  vm.createContext(context);vm.runInContext(source,context);context.parent=parent;vm.runInContext('renderLaunchIntake(parent)',context);
  const find=text=>walk(parent).find(node=>node.tagName==='BUTTON'&&node.textContent===text);
  const field=id=>walk(parent).find(node=>node.id===id);
  return {parent,context,calls,timers,find,field,resolveSave:()=>resolveSave?.(),report:()=>structuredClone(report)};
}
(async()=>{
  const helper=require('./launch-ui.js');
  assert.deepEqual(helper.launchDriveUrls('https://drive.google.com/drive/folders/aaaaaaaaaaaa\nhttps://drive.google.com/file/d/bbbbbbbbbbbb/view'),['https://drive.google.com/drive/folders/aaaaaaaaaaaa','https://drive.google.com/file/d/bbbbbbbbbbbb/view']);
  assert.equal(helper.launchDriveUrls('https://drive.google.com/drive/folders/aaaaaaaaaaaa\nhttps://drive.google.com/drive/folders/aaaaaaaaaaaa').length,1);
  for(const url of ['https://docs.google.com/document/d/bbbbbbbbbbbb','https://drive.google.com/drive/folders/short','https://drive.google.com/arbitrary','javascript:alert(1)','http://drive.google.com/file/a','https://drive.google.com.evil.invalid/a','https://user@drive.google.com/a','https://example.invalid'])assert.throws(()=>helper.launchDriveUrls(url));

  const env=setup();await settle();assert.equal(env.field('launch-intake-brief').value,'פתיחת האתר לקולקציה החדשה והמשך קמפיין המכירות');assert.equal(env.calls.filter(x=>x.method==='POST').length,0);assert.equal(walk(env.parent).find(node=>node.tagName==='DETAILS').open,true);
  env.field('launch-intake-drive').value='https://drive.google.com/drive/folders/aaaaaaaaaaaassets';env.field('launch-intake-drive').oninput();await env.find('הכן תוכנית השקה').onclick();
  assert.equal(env.calls.filter(x=>x.method==='POST').length,1);assert.equal(env.report().request.execution_approval,false);
  assert(walk(env.parent).some(node=>node.textContent==='בקשת העבודה נשמרה.'));
  assert(walk(env.parent).some(node=>node.textContent==='הקישורים שנשמרו · הקבצים עדיין לא נקראו'));
  assert(walk(env.parent).some(node=>node.textContent==='מדידה'));
  assert.equal(walk(env.parent).filter(node=>node.tagName==='BUTTON').length,3);
  const form=walk(env.parent).find(node=>node.tagName==='DETAILS'&&node.children.some(child=>child.textContent==='עדכן בקשה וחומרים'));assert.equal(form.open,false);const plan=walk(env.parent).find(node=>node.dataset.launchPlan);assert.equal(plan.open,false);const stage=walk(env.parent).find(node=>node.textContent==='מדידה');assert.equal(walk(plan).includes(stage),true);
  assert.equal(walk(env.parent).some(node=>node.tagName==='INPUT'),false);
  assert.equal(env.calls.some(x=>/execute|publish|activate|meta-execution/.test(x.url)),false);

  const canonical=setup();await settle();canonical.field('launch-intake-brief').value='פתיחת\u202e האתר';canonical.field('launch-intake-brief').oninput();canonical.field('launch-intake-drive').value='https://drive.google.com/drive/folders/aaaaaaaaaaaa?resourcekey=a%20b';canonical.field('launch-intake-drive').oninput();await canonical.find('הכן תוכנית השקה').onclick();assert.equal(canonical.find('הכן תוכנית השקה').disabled,false);assert.equal(canonical.report().request.brief,'פתיחת האתר');assert(canonical.report().request.drive_urls[0].endsWith('resourcekey=a+b'));assert.equal(launchHelpers.launchSameRequest(canonical.report().request,{brief:'פתיחת\u202e האתר',drive_urls:['https://drive.google.com/drive/folders/aaaaaaaaaaaa?resourcekey=a%20b']}),true);

  const invalid=setup();await settle();invalid.field('launch-intake-drive').value='https://unrelated.invalid';await invalid.find('הכן תוכנית השקה').onclick();assert.equal(invalid.calls.filter(x=>x.method==='POST').length,0);

  const retained=setup();await settle();const original=retained.field('launch-intake-brief');original.value='מטרה שכתב העובד';original.oninput();retained.parent.replaceChildren(new Element('div','new heading'));vm.runInContext('renderLaunchIntake(parent)',retained.context);assert.equal(retained.field('launch-intake-brief'),original);assert.equal(original.value,'מטרה שכתב העובד');assert.equal(retained.calls.length,2);

  const deferred=setup({mode:'deferred'});await settle();const sending=deferred.find('הכן תוכנית השקה').onclick();await deferred.find('שומר בקשת עבודה…').onclick();assert.equal(deferred.calls.filter(x=>x.method==='POST').length,1);deferred.resolveSave();await sending;assert.equal(deferred.calls.filter(x=>x.method==='POST').length,1);

  const unknown=setup({mode:'unknown'});await settle();await unknown.find('הכן תוכנית השקה').onclick();assert.equal(unknown.find('הכן תוכנית השקה').disabled,true);await unknown.find('הכן תוכנית השקה').onclick();assert.equal(unknown.calls.filter(x=>x.method==='POST').length,1);await unknown.find('רענן את מצב התוכנית').onclick();assert.equal(unknown.find('הכן תוכנית השקה').disabled,false);assert(walk(unknown.parent).some(node=>node.textContent.includes('נמצאה בקשת עבודה שמורה')));

  const rejected=setup({mode:'reject'});await settle();await rejected.find('הכן תוכנית השקה').onclick();assert.equal(rejected.find('הכן תוכנית השקה').disabled,false);
  const req={id:'one',revision:1,brief:'השקת קולקציה',drive_urls:['https://drive.google.com/drive/folders/aaaaaaaaaaaa']};
  const safeUrl='https://accounts.google.com/o/oauth2/v2/auth?client_id=163075886816-kbpjqhk2gjaduimqlie6qpv2mh2r46sq.apps.googleusercontent.com&scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fdrive.readonly';
  const oauth=setup({initialRequest:req,authorizationUrl:safeUrl});await settle();assert.equal(oauth.calls.some(x=>x.method==='POST'),false);await oauth.find('חבר Google Drive לקריאה בלבד').onclick();assert(walk(oauth.parent).some(x=>x.tagName==='A'&&x.textContent==='פתח את Google ואשר קריאה בלבד'&&x.rel==='noopener noreferrer'));assert.equal(oauth.calls.filter(x=>x.url==='/api/drive/oauth/begin').length,1);
  const malicious=setup({initialRequest:req,authorizationUrl:'https://evil.invalid/auth'});await settle();await malicious.find('חבר Google Drive לקריאה בלבד').onclick();assert.equal(walk(malicious.parent).some(x=>x.tagName==='A'&&x.href?.startsWith('https://evil.invalid')),false);
  const connected=setup({initialRequest:req,driveConfigured:true,materials:{metadata_verified:true,content_read_count:3,creative_reviewed:false}});await settle();assert(walk(connected.parent).some(x=>x.textContent==='נקרא תוכן של 3 קבצים · בדיקת הקריאייטיב טרם הושלמה'));assert.equal(connected.calls.some(x=>x.url==='/api/launch/materials/read'),false);await connected.find('קרא שוב את החומרים').onclick();assert.equal(connected.calls.filter(x=>x.url==='/api/launch/materials/read').length,1);
  console.log('PASS: launch goal prefill; Drive validation; exact save-only payload; actual stages; no activation; retained draft on redraw; double-click dedup; ambiguous-save reconciliation; rejected request recovery.');
})().catch(error=>{console.error(error);process.exitCode=1});
