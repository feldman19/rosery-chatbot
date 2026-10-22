/* Business-facing task summary. Presentation only, never publication authority. */
function ownerOverviewModel(work,report){
 const available=report?.available===true&&Array.isArray(report.proposal?.ads)&&report.proposal.ads.length>0;
 const p=available?report.proposal:null;
 const reviewed=available&&typeof p.prepared_at==='string'&&(work?.completed||[]).some(row=>row.id==='launch_copy_review'&&row.state==='completed'&&row.proposal_id===p.id&&row.proposal_prepared_at===p.prepared_at&&row.request_id===p.request_id&&row.request_revision===p.request_revision);
 const total=p?.budget?.type==='lifetime'&&Number.isInteger(p.budget.proposed_total_minor)&&p.budget.proposed_total_minor>=0?p.budget.proposed_total_minor:null;
 return {available,count:p?.ads.length||0,title:p?.adset_plan?.objective==='OUTCOME_SALES'?'להכין קמפיין מכירות להשקת הקולקציה':'הכנת הפרסום של ROSERY',
  phase:available?'המודעות הוכנו. בדיקות המוכנות להפעלה טרם הושלמו.':'אין כרגע הצעה מאומתת להצגה.',reviewed,
  launchAt:typeof p?.launch_at==='string'?p.launch_at:null,noPromotion:p?.promotion===false,totalMinor:total,currency:p?.budget?.currency||'ILS',
  gaps:available?(p.gaps||[]).map(g=>typeof g==='string'?g:g?.detail||g?.title).filter(x=>typeof x==='string'):[],
  steps:[{title:available?'נבחרו '+p.ads.length+' מודעות עם פיד וסטורי':'בחירת מודעות מהחומרים',done:available},
   {title:'תיקון וביקורת של הקופי',done:reviewed},
   {title:'בדיקת דף המכירה, המלאי ומדידת הרכישות',done:false},
   {title:'אישור הפרסום וההפעלה',done:false}],
  ownerNext:available?'פתח את המודעות. אם יש משהו לשנות בתמונה או בטקסט, שלח הערה עם מספר המודעה.':'המשך ההכנה דורש הצעה ומקורות תקינים. אין מודעות שאפשר לבדוק כרגע.'};
}
function renderOwnerOverview(parent,work,report,onAction){
 const m=ownerOverviewModel(work,report),signature=JSON.stringify(m);if(parent.dataset.ownerOverview===signature)return;parent.dataset.ownerOverview=signature;
 const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n};
 if(!document.getElementById('owner-overview-style')){const style=el('style',`.ot-card{background:#fff;border:1px solid #e1deee;border-radius:18px;padding:22px;color:#24213b}.ot-card *{box-sizing:border-box;min-width:0}.ot-card .ot-eyebrow{color:#6b55bd;font-size:12px;font-weight:700}.ot-card h2{font-size:25px;line-height:1.35;margin:9px 0 12px}.ot-card p{font-size:15px;line-height:1.7;margin:9px 0}.ot-card small{display:block;font-size:12px;color:#676276;line-height:1.7}.ot-card .ot-phase{background:#f1edff;border-radius:12px;padding:13px;font-weight:650}.ot-card ol{list-style:none;padding:0;margin:20px 0;display:grid;gap:11px}.ot-card li{display:flex;gap:10px;align-items:flex-start;font-size:14px;line-height:1.55}.ot-card .ot-number{background:#eeedf3;color:#716b81;border-radius:50%;min-width:25px;height:25px;text-align:center;line-height:25px;font-weight:700}.ot-card .ot-done{background:#e4f2eb;color:#236c46}.ot-card .ot-action{background:#f7f6fb;border-radius:12px;padding:14px}.ot-card button{width:100%;min-height:48px;border-radius:11px;border:0;padding:12px 16px;background:#6047dc;color:#fff;font:inherit;font-size:15px;font-weight:700;cursor:pointer;margin-top:12px}.ot-card button.ot-secondary{background:#f0edf9;color:#4c3d7c}.ot-card button:disabled{opacity:.5;cursor:default}.ot-card details{border-top:1px solid #ece9f2;margin-top:18px;padding-top:8px}.ot-card summary{min-height:44px;padding:10px 0;font-weight:650;cursor:pointer;font-size:14px}.ot-card :focus-visible{outline:3px solid #a79ae2;outline-offset:3px}@media(max-width:480px){.ot-card{padding:18px}.ot-card h2{font-size:23px}}`);style.id='owner-overview-style';document.head.append(style)}
 parent.replaceChildren();const card=el('section',undefined,'ot-card');card.dir='rtl';card.append(el('small','המשימה הנוכחית','ot-eyebrow'),el('h2',m.title),el('p',m.phase,'ot-phase'));
 if(m.launchAt&&Number.isFinite(Date.parse(m.launchAt)))card.append(el('small','השקה מתוכננת: '+new Date(m.launchAt).toLocaleString('he-IL',{timeZone:'Asia/Jerusalem',day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'})+(m.noPromotion?' · ללא מבצע':'')));
 if(m.totalMinor!==null){let amount;try{amount=new Intl.NumberFormat('he-IL',{style:'currency',currency:m.currency,maximumFractionDigits:0}).format(m.totalMinor/100)}catch{amount=String(m.totalMinor/100)}card.append(el('small','תקציב כולל מוצע: '+amount+' · לא אושר'))}
 const list=el('ol');for(const [i,step]of m.steps.entries()){const row=el('li');row.append(el('span',step.done?'✓':String(i+1),'ot-number'+(step.done?' ot-done':'')),el('span',step.title+(step.done?' · הושלם':' · טרם הושלם')));list.append(row)}card.append(list);
 const action=el('div',undefined,'ot-action');action.append(el('strong','מה עושים עכשיו?'),el('p',m.ownerNext));card.append(action);
 const ads=el('button',m.available?'צפה ב־'+m.count+' המודעות':'המודעות עדיין אינן זמינות');ads.type='button';ads.disabled=!m.available;ads.onclick=()=>onAction?.('ads');const note=el('button','שלח הערה על ההצעה','ot-secondary');note.type='button';note.onclick=()=>onAction?.('notes');card.append(ads,note);
 const detail=el('details');detail.append(el('summary','מה עדיין צריך להשלים לפני פרסום?'));if(m.gaps.length)for(const gap of m.gaps)detail.append(el('p',gap));else detail.append(el('p','אין כאן הוכחת מוכנות לאתר ולמדידה. נדרשות בדיקות נפרדות לפני פרסום.'));detail.append(el('small','כאן אפשר לעיין בהצעה ולשמור הערות. הטיפול בהערות ובדיקות שדורשות סוכן עדיין נמשך בצ׳אט; הוא אינו מתבצע אוטומטית ברקע.'));card.append(detail);parent.append(card);
}
if(typeof module!=='undefined'&&module.exports)module.exports={ownerOverviewModel};
