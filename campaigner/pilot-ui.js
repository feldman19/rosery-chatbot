/* All imported strings use textContent. No ad/account mutations in this view. */
(async function bootPilot(){
  const main=document.querySelector('main');
  let r;
  try{r=await api('pilot')}catch(error){
    const b=el('section','', 'panel');b.append(el('h2','לא הצלחנו לטעון את תמונת המצב'),el('p','הנתונים אינם זמינים כרגע. לא בוצע שום שינוי בפרסום.','muted'));
    const retry=el('button','נסה לטעון שוב','secondary');retry.onclick=()=>location.reload();b.append(retry);main.replaceChildren(b);return;
  }
  if(!r.available){main.replaceChildren(el('section','עדיין לא נאספו נתונים לפיילוט ROSERY','panel'));return}
  if(!document.getElementById('workspace')){
    const b=el('section','', 'panel');b.append(el('h2','ROSERY · פיילוט בתצפית'),el('p','הנתונים נשמרו לקריאה בלבד. אין פרסום אוטומטי מתוך המערכת.','muted'));
    if(!r.observations)b.append(el('p',r.summary),el('p',r.note,'muted'));
    else{const a=el('a','פתח את מרכז הפיילוט');a.href='/';b.append(a)}
    main.prepend(b);return;
  }
  let work=null,workError=false;try{work=await api('registration')}catch{workError=true}
  const obs=r.observations||[];
  const campaign=obs.find(o=>o.source_id==='meta_campaign');const c=campaign?.data||{};
  const issues=r.exceptions||[];
  const dateLabel=pilotFormat.period(campaign?.query);
  const capture=new Date(r.captured_at).toLocaleString('he-IL',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit',timeZone:'Asia/Jerusalem'});
  const money=pilotFormat.money;
  const labels={overview:'סקירה כללית',campaigns:'קמפיינים',issues:'לטיפול',business:'תיק העסק',sources:'נתונים וחיבורים'};
  const priority=issues.find(i=>i.id==='registration-proof')||issues[0];
  const issueCopy={
    'registration-proof':{title:'מדידת הרשמות דורשת אימות',detail:'יש קמפיין פעיל, אך עדיין אין בפיילוט נתון הרשמה שאפשר לסמוך עליו',impact:'בלי מדידה מאומתת לא ניתן לחשב עלות להרשמה או להמליץ על סקייל',steps:['לוודא שהרשמה מוצלחת נשמרת במערכת האתר','לבדוק שאירוע הרשמה נשלח רק לאחר הצלחה','להשוות נרשמים ייחודיים לאירועים ולבדוק כפילויות'],owner:'בדיקה טכנית',state:'טרם אומת'},
    economics:{title:'להשלים את תמונת הרווחיות',detail:'עלות רכישה רווחית עדיין לא חושבה מכל העלויות',impact:'מחזור ו־ROAS לבדם אינם אומרים כמה נשאר לעסק',steps:['לבדוק עלויות מוצר, משלוח ועמלות במקורות הזמינים','ליישב מע״מ, החזרות ועלויות משתנות','להציג מה מאומת ומה אומדן לפני קביעת יעד'],owner:'בדיקת נתונים',state:'חסרים נתונים'},
    'launch-phase':{title:'לאמת את מצב ההשקה',detail:'מועד פתיחת המכירה והיעד הנוכחי עדיין לא אומתו',impact:'שלב ההשקה קובע אם המטרה היא הרשמה או רכישה',steps:['לבדוק את דף היעד הנוכחי באתר','לוודא שהטופס או המכירה זמינים לקהל','לאמת מועד השקה והצעה עדכנית'],owner:'בדיקת העסק',state:'ממתין לבדיקה'},
    refresh:{title:'תמונת המצב התיישנה',detail:'הנתונים המוצגים הם מהקריאה האחרונה שנשמרה',impact:'לפני כל החלטה צריך לקרוא את המקורות מחדש',steps:['לבצע קריאה חדשה דרך החיבורים המורשים','לבדוק חלון דיווח, מטבע ושינויים מאז הקריאה','לשמור תמונת מצב חדשה; רענון עמוד לבדו אינו מושך נתונים מהפלטפורמות'],owner:'בדיקת חיבורים',state:'נדרש רענון'}
  };
  function info(issue){return issueCopy[issue.id]||{title:issue.title,detail:issue.reason,impact:issue.next_step,steps:[issue.next_step],owner:'בדיקה',state:'פתוח'}}
  function button(text,cls,fn){const b=el('button',text,cls);b.onclick=fn;return b}
  const dialog=document.getElementById('issue-dialog');
  document.querySelector('.close-dialog').onclick=()=>dialog.close();
  dialog.addEventListener('click',e=>{if(e.target===dialog)dialog.close()});
  function openIssue(issue){const v=info(issue);document.getElementById('dialog-title').textContent=v.title;const body=document.getElementById('dialog-content');body.replaceChildren(el('span',v.state,'status-tag amber'),el('h3','למה זה חשוב'),el('p',v.impact),el('h3','מה צריך לבדוק'));
    const list=el('ol','', 'checklist');for(const step of v.steps)list.append(el('li',step));body.append(list,el('h3','מי מטפל ומה הסטטוס'),el('p',v.owner+' · לא התחילה בדיקה אוטומטית. המסך מציג את תוכנית הבדיקה בלבד.'));
    body.append(button('צפה במקורות הנתונים','secondary',()=>{dialog.close();show('sources')}));dialog.showModal();}
  const pages={};main.replaceChildren();
  function page(key,subtitle){const p=el('section','', 'page');p.id='view-'+key;p.hidden=key!=='overview';p.setAttribute('aria-labelledby','heading-'+key);const head=el('div','', 'page-heading');const title=el('div','');const h=el('h1',labels[key]);h.id='heading-'+key;h.tabIndex=-1;title.append(h,el('p',subtitle));head.append(title,el('span',dateLabel,'period'));p.append(head);main.append(p);pages[key]=p;return p}
  function show(key,focus=true){for(const [k,p]of Object.entries(pages))p.hidden=k!==key;for(const b of document.querySelectorAll('[data-view]')){if(b.dataset.view===key)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current')}if(focus){document.getElementById('heading-'+key).focus({preventScroll:true});window.scrollTo({top:0,behavior:'instant'})}}
  for(const b of document.querySelectorAll('[data-view]'))b.onclick=()=>show(b.dataset.view);
  document.getElementById('issue-count').textContent=issues.length;
  function panel(title,extra){const p=el('section','', 'panel');const h=el('div','', 'panel-head');h.append(el('h2',title));if(extra)h.append(el('span',extra));p.append(h);return p}
  function campaignTable(){const wrap=el('div','', 'table-wrap');if(!campaign){wrap.append(el('p','לא נמצא קמפיין בתמונת המצב שנשמרה','empty'));return wrap;}const t=el('table','');const cap=el('caption','קמפיין ההרשמה שנבדק · מדדים ל־'+dateLabel);cap.style.cssText='text-align:right;padding:12px 21px;color:#697087;font-size:12px';t.append(cap);const tr=el('tr','');for(const title of ['קמפיין','מצב בקריאה האחרונה','תקציב יומי','הוצאה בתקופה','הרשמות מיוחסות']){const th=el('th',title);th.scope='col';tr.append(th)}const thead=el('thead','');thead.append(tr);t.append(thead);const row=el('tr','');const name=el('td','');name.append(el('strong',pilotFormat.campaignName(c)),el('small','הרשמות להשקת הקולקציה · Meta'));row.append(name);const status=el('td','');status.append(el('span',c.effective_status==='ACTIVE'?'פעיל':c.effective_status==='PAUSED'?'מושהה':'לא זמין','status-tag '+(c.effective_status==='ACTIVE'?'green':'')));row.append(status,el('td',money(c.daily_budget)),el('td',money(c.amount_spent)),el('td',c.omni_complete_registration??'לא זמין'));const tbody=el('tbody','');tbody.append(row);t.append(tbody);wrap.append(t);return wrap}
  const overview=page('overview','תמונת המצב של העסק והצעד הבא שכדאי לבדוק');
  const snapshot=el('div','', 'snapshot');snapshot.append(el('span','תמונת מצב מ־'+capture+' · '+(r.freshness==='stale_import'?'נדרשת קריאה עדכנית':'נתונים שמורים, לא חיבור חי')));snapshot.append(button('פרטי החיבורים','link-button',()=>show('sources')));overview.append(snapshot);
  const cards=el('div','', 'cards');
  for(const [label,value,note,warn,icon]of [['הוצאה בקמפיין ההרשמה',money(c.amount_spent),'Meta · '+dateLabel,false,'₪'],['כניסות לדף',c.landing_page_view??'—','בקמפיין ההרשמה · לא מבקרים ייחודיים',false,'↗'],['הרשמות מיוחסות',c.omni_complete_registration??'לא זמין',c.omni_complete_registration==null?'חסר נתון · אין להסיק אפס הרשמות':'דיווח Meta · שמירה ייחודית טרם אומתה',c.omni_complete_registration==null,'✓']]){const card=el('article','', 'metric');const top=el('div','', 'metric-label');top.append(el('span',label),el('span',icon,'metric-icon'));card.append(top,el('p',String(value),'metric-value'+(warn?' warn':'')),el('p',note,'metric-note'));cards.append(card)}overview.append(cards);
  const grid=el('div','', 'work-grid');const action=panel('הצעד הבא','תוכנית לבדיקה');const hero=el('div','', 'next-action');hero.append(el('div','מומלץ להתחיל כאן','eyebrow'),el('h2',priority?.id==='registration-proof'?'לפני שמגדילים תקציב, מאמתים הרשמות':priority?info(priority).title:'אין בדיקות פתוחות'),el('p',priority?info(priority).impact:'לא הוצע שינוי בפרסום'));
  if(priority)hero.append(button('מה צריך לבדוק? ←','primary',()=>openIssue(priority)));hero.append(el('div','אין שינוי תקציב או פרסום בלחיצה הזו','action-footer'));action.append(hero);grid.append(action);
  const plan=panel('מסלול הפיילוט','ROSERY');const steps=el('div','', 'mini-plan');for(const [num,title,sub,active]of [['✓','תמונת מצב נאספה',(obs.some(o=>o.source_id==='meta_account')&&obs.some(o=>o.source_id==='shopify_sales')?'נתוני Meta ו־Shopify נשמרו':'נשמרו נתונים חלקיים; ראו מקורות'),false],['2','אימות מדידה ורווחיות','השלב הנוכחי · נדרשות בדיקות',true],['3','הצעת שינוי לאישור','לאחר השלמת הבדיקות',false]]){const row=el('div','', 'plan-row');const txt=el('div','');txt.append(el('strong',title),el('small',sub));row.append(el('span',num,'step'+(active?' current':'')),txt);steps.append(row)}plan.append(steps);grid.append(plan);overview.append(grid);
  const campaigns=panel('הקמפיין שנבדק',campaign?'1 מתוך החשבון · לא סקירה מלאה':'אין נתון קמפיין');campaigns.classList.add('bottom-panel');campaigns.append(campaignTable());const foot=el('div','', 'panel-foot');foot.append(button('צפה בקמפיין ובמגבלות הנתונים ←','link-button',()=>show('campaigns')));campaigns.append(foot);overview.append(campaigns);
  const cp=page('campaigns','נתוני קמפיין ההרשמה שנקרא בפיילוט; יתר החשבון לא מוצג כאן');const cps=panel('קמפיין הרשמה','Meta');cps.append(campaignTable());cp.append(cps,el('div','הסטטוס והתקציב הם נכון לקריאה האחרונה. המדדים מתייחסים לתקופה המוצגת. אין כאן הוכחה לשמירת נרשמים או לרווחיות.','notice'));
  const ip=page('issues','בדיקות פתוחות לפני המלצה לשינוי · אין כרגע בקשת אישור לביצוע');const queue=panel('תור הבדיקות',issues.length+' פתוחות');
  for(const issue of issues){const v=info(issue);const row=el('article','', 'issue-row');const txt=el('div','', 'issue-text');txt.append(el('strong',v.title),el('p',v.detail),el('span',v.owner+' · '+v.state,'muted'));row.append(el('span',issue.priority==='high'?'!':'○','issue-icon'),txt,button('פרטי בדיקה','issue-open',()=>openIssue(issue)));queue.append(row)}if(!issues.length)queue.append(el('p','אין בדיקות פתוחות בתמונת המצב שנשמרה','empty'));ip.append(queue);
  const bp=page('business','מה המערכת יודעת על ROSERY ועל מה המידע נשען');const business=panel('פרופיל העסק','מידע לעבודה, לא הגדרות לפרסום');const factNames={business_type:'אופי העסק',goal:'מטרת העסק',launch_budget:'תקציב ההרשמה שאושר',packaging:'אריזה',operations:'שלב הפעילות'};const confidence={declared:'הצהרת בעל העסק',observed:'תועד בשיחה ובייבוא נתונים',historical:'מידע היסטורי · נדרשת בדיקה',needs_reconciliation:'נדרש יישוב עדכני'};
  for(const f of r.facts||[]){const row=el('div','', 'fact');row.append(el('strong',factNames[f.key]||'מידע על העסק'),el('p',f.value),el('span',confidence[f.confidence]||'טרם אומת','muted'));business.append(row)}bp.append(business);
  const sp=page('sources','מקור הנתונים, מועד הקריאה ומה עדיין אינו מחובר');const sourcePanel=panel('הנתונים שנאספו','קריאה בלבד');
  const sourceNames={meta_account:['Meta · חשבון הפרסום','תמונת חשבון; אינה תוצאת קמפיין ההרשמה בלבד'],meta_campaign:['Meta · קמפיין ההרשמה','הגדרות קמפיין ומדדים לתקופה שנבחרה'],shopify_sales:['Shopify · מכירות החנות','כל הערוצים שנכללו בשאילתה; אין לייחס מכירות אלה לקמפיין ההרשמה']};
  for(const o of obs){const item=el('article','', 'source-card');const [name,description]=sourceNames[o.source_id]||['מקור נתונים','ייבוא מקומי'];item.append(el('h3',name),el('p',description),el('p','נקרא ב־'+capture+' · לא מרוענן עצמאית'));const details=el('details','');details.append(el('summary','הצג נתוני מקור ופרטים טכניים'),el('pre',JSON.stringify({query:o.query,scope:o.scope,data:o.data},null,2)));item.append(details);sourcePanel.append(item)}sp.append(sourcePanel,el('div','This section contains saved chat imports. The direct Shopify report above is a separate live read. Meta and scheduled collection are not connected yet.','notice'));
  const oldNotice=sp.querySelector('.notice');if(oldNotice)oldNotice.textContent='הנתונים בחלק זה הם ייבוא שמור מהצ׳אט. דוחות Meta ו־Shopify בכפתורים למעלה הם קריאות נפרדות ועדכניות בעת לחיצה. הסקירה הראשית מציגה את הקריאה האוטומטית; הנתונים ההיסטוריים בחלק זה נשמרים בנפרד.';
  const limits=el('details','');limits.append(el('summary','מגבלות תמונת המצב המיובאת'));const ul=el('ul','');for(const t of r.limitations||[])ul.append(el('li',t));limits.append(ul);sp.append(limits);
  if(work?.available)renderRegistrationWork(work,pages,show);
  await renderReviewHistory(pages);
  renderConnections(pages);
  if(workError)pages.overview.append(el('div','סבב הבדיקות אינו זמין או שהראיות השתנו. נתוני הסקירה אינם הוכחה שהבדיקות הושלמו.','notice'));
  renderLiveWorkspace(pages);
  renderDecisionDesk(pages);
  renderMetaExecutionPanel(pages.issues);
  show('overview',false);
})().catch(()=>{const main=document.querySelector('main');main.replaceChildren(el('h1','לא ניתן להציג את הפיילוט'),el('p','טעינת הנתונים לא הושלמה. אין בסיס לפעולה בפרסום.','muted'));const retry=el('button','טען את המסך מחדש','secondary');retry.onclick=()=>location.reload();main.append(retry)});
