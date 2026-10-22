/* Node-only mocked DOM/HTTP tests. Never contacts Meta or a local server. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/meta-execution-panel.js','utf8');
class Element{
  constructor(tag,text=''){this.tagName=tag.toUpperCase();this.textContent=text;this.children=[];this.dataset={};this.style={};this.value='';this.hidden=false;this.disabled=false;this.checked=false;this.attributes={};}
  append(...nodes){for(const node of nodes){this.children.push(node);if(this.tagName==='SELECT'&&!this.value)this.value=node.value;}}
  insertBefore(node,before){const index=this.children.indexOf(before);if(index<0)this.children.push(node);else this.children.splice(index,0,node);}
  replaceChildren(...nodes){this.children=[];this.append(...nodes)}
  setAttribute(key,value){this.attributes[key]=value}
  querySelector(selector){return walk(this).find(node=>selector==='[data-meta-execution-panel]'?Object.hasOwn(node.dataset,'metaExecutionPanel'):selector==='[data-expiry]'?Object.hasOwn(node.dataset,'expiry'):false)||null;}
}
function walk(root){return root.children.flatMap(node=>[node,...walk(node)])}
async function settle(){await new Promise(resolve=>setImmediate(resolve));}
function setup({persisted=null,mode='verified'}={}){
  let now=Date.parse('2026-10-17T12:00:00Z'),operation=persisted?structuredClone(persisted):null;
  let completeExecute=null;const calls=[],intervals=[],storage=new Map(persisted?[['rosery-meta-operation',persisted.operation_id]]:[]);
  const page=new Element('section');page.append(new Element('h1'),new Element('p'));
  const credential={configured:true,verified:true};
  const context={Element,Intl,JSON,Number,Object,Error,console,document:{hidden:false,head:new Element('head'),addEventListener(){}},
    Date:class extends Date{constructor(value){super(value===undefined?now:value)}static now(){return now}},
    el:(tag,text='',cls)=>{const node=new Element(tag,text);node.className=cls||'';return node},token:'mock-local-session',
    setInterval:fn=>intervals.push(fn),localStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},
    fetch:async(url,options)=>{const path=url.split('/').at(-1),body=options.body?JSON.parse(options.body):null;calls.push({path,body});let data;
      if(path==='status')data={account_id:'393059510164294',campaign_id:'120253309360290730',credential_state:credential,proposals:operation?[operation]:[]};
      else if(path==='preview'){operation={operation_id:'a'.repeat(32),digest:'b'.repeat(64),account_id:'393059510164294',campaign_id:'120253309360290730',status:'prepared',execution_ready:false,exact_action_approved:false,
        before:{status:'ACTIVE',budget_minor:6000,currency:'ILS',budget_type:'daily'},after:{status:body.action==='pause'?'PAUSED':'ACTIVE',budget_minor:body.budget_minor||6000,currency:'ILS',budget_type:'daily'},expires_at:new Date(now+600000).toISOString()};data=operation;}
      else if(path==='approve'){assert.deepEqual(Object.keys(body).sort(),['digest','operation_id']);operation={...operation,status:'approved',execution_ready:true,exact_action_approved:true};data=operation;}
      else if(path==='execute'){assert.deepEqual(Object.keys(body),['operation_id']);operation={...operation,status:mode==='verified'?'verified':'unknown',execution_ready:false,approval_consumed:true};if(mode==='network_unknown')throw Error('mock network loss');if(mode==='deferred')await new Promise(resolve=>{completeExecute=resolve});data={state:operation.status,operation_id:operation.operation_id};}
      else if(path==='reconcile'){operation={...operation,status:'verified',execution_ready:false,approval_consumed:true};data={state:'verified',operation_id:operation.operation_id};}
      else if(path==='stop'){operation=operation?{...operation,status:'revoked',execution_ready:false}:null;data={stopped:true};}
      else throw Error('Unexpected mock route: '+path);
      return {ok:true,status:200,json:async()=>structuredClone(data)};
    }};
  vm.createContext(context);vm.runInContext(source,context);context.page=page;vm.runInContext('renderMetaExecutionPanel(page)',context);
  const find=text=>walk(page).find(n=>n.tagName==='BUTTON'&&n.textContent===text);
  return {context,page,calls,find,intervals,resolveExecute:()=>completeExecute?.(),setNow:value=>{now=value},getNow:()=>now,getOperation:()=>structuredClone(operation),storage};
}
async function prepareAndApprove(env){await settle();await env.find('הצג לפני ואחרי').onclick();const approve=env.find('אשר רק את השינוי הזה');assert.equal(approve.disabled,true);const check=walk(env.page).find(n=>n.tagName==='INPUT'&&n.type==='checkbox');check.checked=true;check.onchange();assert.equal(approve.disabled,false);await approve.onclick();return env.getOperation();}
(async()=>{
  const helpers=require('./meta-execution-panel.js');
  assert.equal(helpers.metaExecutionBudgetMinor('60.01'),6001);
  for(const text of ['0','-1','1e3','1.234','1,000','Infinity',''])assert.throws(()=>helpers.metaExecutionBudgetMinor(text));
  const env=setup();await settle();assert.deepEqual(env.calls.map(x=>x.path),['status']);
  assert.equal(walk(env.page).some(n=>n.textContent==='שחרר חסימת ביצוע'),false);
  await prepareAndApprove(env);assert.equal(env.calls.filter(x=>x.path==='execute').length,0);
  const execute=env.find('בצע עכשיו ב־Meta');assert.equal(execute.disabled,false);const sending=execute.onclick();await execute.onclick();await sending;await settle();
  assert.equal(env.calls.filter(x=>x.path==='execute').length,1);assert.equal(execute.disabled,true);
  assert.equal(env.calls.some(x=>x.path==='kill'),false);

  const budget=setup();await settle();const select=walk(budget.page).find(n=>n.tagName==='SELECT');select.value='set_budget';select.onchange();const input=walk(budget.page).find(n=>n.id==='mx-budget');input.value='65.50';input.oninput();await budget.find('הצג לפני ואחרי').onclick();assert.equal(budget.calls.find(x=>x.path==='preview').body.budget_minor,6550);assert.equal(budget.calls.some(x=>x.path==='approve'),false);

  const expiring=setup();const approved=await prepareAndApprove(expiring);expiring.setNow(Date.parse(approved.expires_at)+1);for(const interval of expiring.intervals)interval();await settle();assert.equal(expiring.find('בצע עכשיו ב־Meta').disabled,true);await expiring.find('בצע עכשיו ב־Meta').onclick();assert.equal(expiring.calls.some(x=>x.path==='execute'),false);

  const unknown=setup({mode:'network_unknown'});await prepareAndApprove(unknown);await unknown.find('בצע עכשיו ב־Meta').onclick();await settle();assert.equal(unknown.find('בצע עכשיו ב־Meta').disabled,true);await unknown.find('בצע עכשיו ב־Meta').onclick();assert.equal(unknown.calls.filter(x=>x.path==='execute').length,1);
  const restore=setup({persisted:unknown.getOperation()});await settle();assert.equal(restore.find('בצע עכשיו ב־Meta').disabled,true);assert.equal(restore.find('בדוק מה נשמר ב־Meta').hidden,false);assert.equal(restore.calls.some(x=>x.path==='execute'),false);await restore.find('בדוק מה נשמר ב־Meta').onclick();assert.equal(restore.getOperation().status,'verified');assert.equal(restore.calls.filter(x=>x.path==='reconcile').length,1);

  const deferred=setup({mode:'deferred'});const original=await prepareAndApprove(deferred);const pending=deferred.find('בצע עכשיו ב־Meta').onclick();await settle();assert.equal(deferred.find('הכן פעולה חדשה').disabled,true);deferred.find('הכן פעולה חדשה').onclick();const deferredSelect=walk(deferred.page).find(n=>n.tagName==='SELECT');deferredSelect.value='set_budget';deferredSelect.onchange();deferred.resolveExecute();await pending;await settle();assert.equal(deferred.storage.get('rosery-meta-operation'),original.operation_id);assert.equal(deferred.getOperation().operation_id,original.operation_id);assert.equal(deferred.find('בדוק מה נשמר ב־Meta').hidden,false);await deferred.find('בדוק מה נשמר ב־Meta').onclick();assert.equal(deferred.calls.filter(x=>x.path==='execute').length,1);assert.equal(deferred.calls.find(x=>x.path==='reconcile').body.operation_id,original.operation_id);

  const stopped=setup();await prepareAndApprove(stopped);await stopped.find('בטל אישורים שטרם מומשו').onclick();assert.equal(stopped.find('בצע עכשיו ב־Meta').disabled,true);assert.equal(stopped.calls.filter(x=>x.path==='stop').length,1);assert.equal(stopped.calls.some(x=>x.path==='execute'),false);
  console.log('PASS: budget validation; no automatic writes; exact preview/approval/execute; double-click dedup; expiry; unknown no-resubmit; reload reconciliation; stop revocation.');
})().catch(error=>{console.error(error);process.exitCode=1});
