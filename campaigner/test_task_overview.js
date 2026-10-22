const assert=require('node:assert/strict'),{ownerOverviewModel}=require('./task-overview');
const p={available:true,proposal:{id:'proposal',prepared_at:'now',request_id:'request',request_revision:1,ads:[{},{}],adset_plan:{objective:'OUTCOME_SALES'},launch_at:'2026-10-19T20:00:00+03:00',promotion:false,budget:{type:'lifetime',proposed_total_minor:30000,currency:'ILS'},gaps:[]}};
let m=ownerOverviewModel({},p);assert.equal(m.count,2);assert.equal(m.totalMinor,30000);assert.equal(m.steps[0].done,true);assert.equal(m.steps[1].done,false);assert.equal(m.steps[2].done,false);assert.equal(m.steps[3].done,false);
m=ownerOverviewModel({completed:[{id:'launch_copy_review',state:'completed',proposal_id:'proposal',proposal_prepared_at:'now',request_id:'request',request_revision:1}]},p);assert.equal(m.reviewed,true);assert.equal(m.steps[2].done,false);
assert.equal(ownerOverviewModel({completed:[{id:'launch_copy_review',state:'completed',proposal_id:'proposal',proposal_prepared_at:'old',request_id:'request',request_revision:1}]},p).reviewed,false);
m=ownerOverviewModel({preparation_loop:{job:{state:'stale'}}},{available:false,proposal:p.proposal});assert.equal(m.count,0);assert.equal(m.reviewed,false);assert.equal(m.totalMinor,null);
assert.equal(ownerOverviewModel({current_state:'הכול מעולה'},p).reviewed,false);
console.log('PASS: plain task model, actual ad count, scoped copy review, no readiness or approval inferred from missing gaps or prose.');
