function renderRegistrationWork(work,pages,show){
  const e=(tag,text,cls)=>el(tag,text,cls);
  const btn=(text,fn,cls='secondary')=>{const b=e('button',text,cls);b.onclick=fn;return b};
  const page=pages.issues;
  page.replaceChildren();const head=e('div','', 'page-heading');const title=e('div','');
  const h=e('h1','עבודת המערכת');h.id='heading-issues';h.tabIndex=-1;title.append(h,e('p','תוצאות הבדיקות שבוצעו, פערים שנותרו והחלטה אחת לבירור'));head.append(title);page.append(head);
  const top=e('div','', 'cards');for(const [value,label]of [[work.completed_checks,'בדיקות נתונים הושלמו'],[work.blocked_checks,'בדיקות חסומות'],[work.question&&!work.answer?1:0,'שאלות פתוחות אליך']]){const c=e('article','', 'metric');c.append(e('p',label,'metric-label'),e('p',String(value),'metric-value'));top.append(c)}page.append(top);
  const note=e('div',work.decision.summary+' · '+work.decision.reason,'notice');page.append(note);
  if(work.freshness==='stale')page.append(e('div','הראיות התיישנו. יש לאסוף נתונים מחדש לפני מענה או החלטה; טעינת העמוד אינה רענון מקורות.','notice'));
  const question=e('section','', 'panel bottom-panel');
  function renderQuestion(statusText){question.replaceChildren(e('div',work.answer?'תשובתך נשמרה בהקשר העסקי':'צריך תשובה ממך','panel-head'));
    const body=e('div','', 'source-card');
    if(!work.question){body.append(e('p','אין כרגע שאלה שמחייבת תשובה ממך'));question.append(body);return}
    body.append(e('h3',work.question.title),e('p',work.question.question),e('p',work.question.checked,'muted'));
    if(work.answer){body.append(e('p',work.answer.choice==='observed_budget_intended'?'נשמר: התקציב שנצפה הוא המסגרת המתוכננת':'נשמר: מסגרת התקציב דורשת בדיקה'));}
    const message=e('p',statusText||'שמירה כאן מעדכנת הקשר עסקי בלבד. היא אינה משנה תקציב ואינה אישור להפעלת מודעות.','muted');message.setAttribute('role','status');
    async function answer(choice){for(const b of body.querySelectorAll('button'))b.disabled=true;
      try{const response=await fetch('/api/registration/answer',{method:'POST',headers:{'X-Local-Session':token,'Content-Type':'application/json'},body:JSON.stringify({revision:work.revision,choice})});const saved=await response.json();if(!response.ok)throw Error(saved.error||'לא נשמר');work.answer={choice};top.children[2].querySelector('.metric-value').textContent='0';document.getElementById('issue-count').textContent=work.blocked_checks;renderQuestion(saved.note);}
      catch(error){message.textContent='התשובה לא נשמרה. נדרשת תמונת מצב תקינה ועדכנית לפני שמנסים שוב';for(const b of body.querySelectorAll('button'))b.disabled=false}}
    body.append(btn(work.answer?'עדכן: זו המסגרת המתוכננת':'זו המסגרת המתוכננת',()=>answer('observed_budget_intended')),
                btn('התקציב דורש בדיקה',()=>answer('budget_needs_review')),message);if(work.freshness==='stale'){for(const b of body.querySelectorAll('button'))b.disabled=true;message.textContent='הראיות התיישנו; נדרשת קריאה חדשה לפני שמירת תשובה'}question.append(body)}
  renderQuestion();page.append(question);
  const names={pass:'הושלם בהיקף הבדיקה',reviewed_history:'תיעוד קודם נסקר',stale:'נדרש רענון',missing:'חסרה ראיה',blocked:'חסום',fail:'נמצאה אי־התאמה'};
  for(const [title,checks]of [['טופל · בדיקות שבוצעו',work.checks.filter(c=>['pass','reviewed_history'].includes(c.state))],['חסום · צריך להשלים בדיקה',work.checks.filter(c=>!['pass','reviewed_history'].includes(c.state))]]){
    const panel=e('section','', 'panel bottom-panel');const ph=e('div','', 'panel-head');ph.append(e('h2',title));panel.append(ph);
    for(const check of checks){const row=e('article','', 'source-card');row.append(e('span',names[check.state]||check.state,'status-tag '+(check.state==='pass'?'green':'amber')),e('h3',check.title),e('p',check.summary));
      const detail=e('details','');detail.append(e('summary','איך הבדיקה תועדה'),e('p','בדיקת קריאה בלבד. מקור שנשמר בגרסת הבדיקה; חתימת הקובץ אינה הוכחת זהות המקור.'),e('pre',JSON.stringify(check.evidence_ref,null,2)));row.append(detail);panel.append(row)}page.append(panel)}
  const approval=e('section','', 'panel bottom-panel');const ap=e('div','', 'panel-head');ap.append(e('h2','ממתין לאישור'),e('span','0 פעולות'));approval.append(ap,e('p','לא הוכנה הצעת שינוי בפרסום: הנתונים עדיין אינם מצדיקים עצירה או הגדלת תקציב.','source-card'));page.append(approval);
  const ads=e('section','', 'panel bottom-panel');const ah=e('div','', 'panel-head');ah.append(e('h2','מה דיווחה כל מודעה'),e('span',pilotFormat.period(work.metrics.window.since+' to '+work.metrics.window.until)+(work.metrics.window.partial_last_day?' · היום האחרון חלקי':'')));ads.append(ah);
  const wrap=e('div','', 'table-wrap'),table=e('table',''),thead=e('thead',''),tr=e('tr','');
  for(const text of ['מודעה','הוצאה ₪','הרשמות מיוחסות','עלות לאירוע ₪']){const th=e('th',text);th.scope='col';tr.append(th)}thead.append(tr);table.append(thead);const tbody=e('tbody','');
  for(const ad of work.ads){const row=e('tr','');const name=e('td',ad.name);row.append(name,e('td',ad.spend_ils),e('td',ad.registrations??'לא זמין'),e('td',ad.attributed_cpl_ils??'—'));tbody.append(row)}table.append(tbody);wrap.append(table);ads.append(wrap,e('p','ייחוס אינו נרשם ייחודי מאומת. מדגם קטן, שינוי תקציב ויום חלקי אינם בסיס להכריז על מנצחת או על מפסידה.','panel-foot'));page.append(ads);
  page.append(e('div','תיעוד הבדיקות ההיסטוריות נאסף דרך הצ׳אט. דוחות Meta ו־Shopify נקראים כעת אוטומטית בנפרד; בדיקות ייחודיות לא מבוצעות מחדש ואין שינוי בפרסום.','notice'));
  document.querySelector('[data-view="issues"]').childNodes.forEach(n=>{if(n.nodeType===3)n.textContent='עבודת המערכת '});
  document.getElementById('issue-count').textContent=work.blocked_checks+(work.question&&!work.answer?1:0);
  const hero=pages.overview.querySelector('.next-action');if(hero){hero.replaceChildren(e('div','בוצע סבב בדיקות על העסק','eyebrow'),e('h2','המערכת בדקה. עכשיו יש תוצאות ופערים ממוקדים'),e('p',`${work.completed_checks} בדיקות נתונים הושלמו, תיעוד קודם נסקר, ו־${work.blocked_checks} בדיקות עדיין חסומות. לא הוצע שינוי בפרסום.`),btn('פתח את תוצאות העבודה ←',()=>show('issues'),'primary'),e('div','כעת ניתן גם להשיב על שאלת מסגרת התקציב','action-footer'));}
  const plan=pages.overview.querySelector('.mini-plan');if(plan){plan.replaceChildren();for(const text of [work.freshness==='stale'?'נדרש רענון קריאת המקורות':'✓ נתוני הקמפיין והאתר נקראו','תיעוד קודם של רשימת ההשקה נסקר','הבא: התאמת ייחודיות וכתובות יעד'])plan.append(e('p',text,'plan-row'));}
}
