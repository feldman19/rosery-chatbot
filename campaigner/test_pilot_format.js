const {test}=require('node:test');const assert=require('node:assert/strict');const f=require('./pilot-format.js');
test('missing money is never zero',()=>{for(const value of [null,undefined,'',' ',false,{},NaN,Infinity])assert.equal(f.money({value,unit:'ILS'}),'לא זמין')});
test('real zero and valid amount remain amounts',()=>{assert.notEqual(f.money({value:0,unit:'ILS'}),'לא זמין');assert.match(f.money({value:'20.02',unit:'ILS'}),/20\.02/)});
test('multi day window includes both boundaries',()=>{const p=f.period('2026-10-01 to 2026-10-15');assert.match(p,/1 /);assert.match(p,/15 /);assert.match(p,/–/)});
test('missing dates do not invent period',()=>assert.equal(f.period('unknown'),'התקופה לא צוינה'));
test('renamed campaign name is preserved',()=>{assert.equal(f.campaignName({name:'new campaign'}),'new campaign');assert.equal(f.campaignName({}),'שם הקמפיין לא זמין')});
