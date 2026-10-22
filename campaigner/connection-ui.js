function renderConnections(pages){
  const panel=el('section','','panel bottom-panel'),head=el('div','','panel-head');head.append(el('h2','חיבור עצמאי למקורות'));panel.append(head);
  panel.append(el('p','כאן אפשר לקרוא דוחות ישירות מ־Meta ומ־Shopify. חיבורי הצ׳אט נפרדים מחיבורים אלה. קריאה אוטומטית כל חמש דקות כשהשרת פועל; אין שינוי בפרסום.','source-card'));
  const button=el('button','בדוק חיבור ישיר','secondary'),status=el('div','','source-card');status.setAttribute('role','status');panel.append(button,status);pages.sources.insertBefore(panel,pages.sources.children[1]||null);
  const labels={missing_credential:'חסר חיבור ישיר',credential_rejected:'ההרשאה נדחתה — נדרש חידוש',network_error:'הגישה לרשת נכשלה',provider_error:'שגיאת ספק',identity_verified:'זהות החנות אומתה',requires_configuration:'נדרשת הגדרת החיבור',identity_or_permission_not_verified:'זהות או הרשאה לא אומתו',invalid_response:'תגובה לא תקינה'};
  button.onclick=async()=>{button.disabled=true;status.textContent='בודק חיבור לקריאה בלבד…';try{
    const response=await fetch('/api/connections/check',{method:'POST',headers:{'X-Local-Session':token,'Content-Type':'application/json'},body:'{}'});
    if(!response.ok)throw Error();const data=await response.json();status.replaceChildren(el('p','Shopify: '+(data.shopify.verified?'זהות החנות אומתה':labels[data.shopify.state]||'לא אומת')),el('p','Meta: '+(data.meta.verified?'האפליקציה, החשבון והרשאת הקריאה אומתו':labels[data.meta.state]||'לא אומת')),el('p','בדיקה מ־'+new Date(data.checked_at).toLocaleString('he-IL')));
    if(data.flashy)status.append(el('p','Flashy: '+(data.flashy.verified?'זהות החשבון אומתה · חיבור רשומות ההרשמה עדיין לא הושלם':'החיבור טרם אומת')));
    if(data.shopify.verified)status.append(el('p','בדיקה זו מאמתת זהות והרשאה בלבד. לקריאת נתונים עדכניים לחץ על כפתור הדוח של המקור המבוקש'));
  }catch{status.textContent='הבדיקה לא הושלמה. לא ניתן להסיק שהחיבור תקין'}finally{button.disabled=false}};
  const setup=el('details',''),summary=el('summary','הגדרת אפליקציית הדוחות של ROSERY');setup.append(summary);
  const id=el('input',''),secret=el('input','');id.value='2c94dca57695507f715c151b553e70ba';id.readOnly=true;id.setAttribute('aria-label','Shopify client ID');secret.type='password';secret.autocomplete='off';secret.setAttribute('aria-label','Shopify client secret');
  const save=el('button','אמת ושמור חיבור מוצפן','secondary'),read=el('button','קרא דוח Shopify עדכני','primary');setup.append(id,secret,save,el('p','המפתח נשמר מוצפן למשתמש Windows הנוכחי בלבד. לא יופיע בדוחות או בחבילת המקור.','muted'));panel.append(setup,read);
  async function post(path,body){const r=await fetch(path,{method:'POST',headers:{'X-Local-Session':token,'Content-Type':'application/json'},body:JSON.stringify(body)});const d=await r.json();if(!r.ok)throw Error(d.error||'הפעולה לא הושלמה');return d}
  const flashySetup=el('details');flashySetup.append(el('summary','חיבור רשימת ההשקה מפלאשי'));
  const flashyKey=el('input');flashyKey.type='password';flashyKey.autocomplete='off';flashyKey.setAttribute('aria-label','Flashy API key');
  const flashySave=el('button','אמת ושמור חיבור Flashy מוצפן','secondary');
  flashySetup.append(flashyKey,flashySave,el('p','מפתח רגיל עשוי לאפשר שינויים אצל פלאשי. מתאם המוצר מוגבל ל־GET; חיבור הרשומות עצמו טרם אומת.','muted'));panel.append(flashySetup);
  flashySave.onclick=async()=>{flashySave.disabled=true;try{await post('/api/connections/flashy/setup',{api_key:flashyKey.value});status.textContent='זהות חשבון פלאשי אומתה והמפתח נשמר מוצפן. קריאת רשימת ההרשמה עדיין דורשת השלמה.'}catch(e){status.textContent=e.message}finally{flashyKey.value='';flashySave.disabled=false}};
  const writerSetup=el('details');writerSetup.append(el('summary','חיבור ביצוע Meta נפרד — למי שמקים את המערכת'));
  const writerToken=el('input');writerToken.type='password';writerToken.autocomplete='off';writerToken.setAttribute('aria-label','Meta executor access token');const writerSave=el('button','אמת ושמור טוקן ביצוע מוצפן','secondary');
  writerSetup.append(el('p','נדרשים אישור הרשאות ואפליקציית Executor נפרדת שנקשרה במארח. אין להחליף את טוקן הדוחות. שמירת הטוקן אינה אישור לשינוי בפרסום.','muted'),writerToken,writerSave);panel.append(writerSetup);
  writerSave.onclick=async()=>{writerSave.disabled=true;try{await post('/api/connections/meta-executor/setup',{access_token:writerToken.value});status.textContent='טוקן הביצוע אומת ונשמר מוצפן בנפרד. לא אושר ולא בוצע שינוי בקמפיין.'}catch(e){status.textContent=e.message}finally{writerToken.value='';writerSave.disabled=false}};
  const metaSetup=el('details','');metaSetup.append(el('summary','חיבור דוחות Meta של ROSERY'));
  const metaToken=el('input','');metaToken.type='password';metaToken.autocomplete='off';metaToken.setAttribute('aria-label','Meta access token');
  const metaSave=el('button','אמת ושמור חיבור Meta מוצפן','secondary'),metaRead=el('button','קרא דוח Meta עדכני','primary');
  metaSetup.append(metaToken,metaSave,el('p','ads_read בלבד · חשבון 393059510164294 · אין חידוש טוקן אוטומטי','muted'));panel.append(metaSetup,metaRead);
  metaSave.onclick=async()=>{metaSave.disabled=true;try{await post('/api/connections/meta/setup',{access_token:metaToken.value});status.textContent='חיבור Meta נשמר מוצפן; האפליקציה, הרשאת הקריאה וחשבון ROSERY אומתו'}catch(e){status.textContent=e.message}finally{metaToken.value='';metaSave.disabled=false}};
  metaRead.onclick=async()=>{metaRead.disabled=true;status.textContent='קורא דוח Meta ישירות…';try{const d=await post('/api/connections/meta/report',{});status.replaceChildren(el('h3','דוח Meta נקרא ישירות'),el('p',d.currency+' · '+d.timezone+' · '+new Date(d.checked_at).toLocaleString('he-IL')),el('p','נתונים יומיים לפי מודעה ל־7 ימים לפי Meta. תוצאות מיוחסות אינן מספר נרשמים ייחודיים מאומתים'));
    if(!d.complete)status.append(el('p','הדוח חלקי — הגענו למגבלת עמודים'));
    const table=el('table',''),head=el('tr','');for(const label of ['יום','מודעה','הוצאה','חשיפות','קליקים לקישור','אירועים מיוחסים'])head.append(el('th',label));table.append(head);
    for(const r of d.rows){const tr=el('tr','');for(const value of [r.date_start,r.ad_name,r.spend,r.impressions,r.inline_link_clicks,(r.actions||[]).map(x=>x.action_type+': '+x.value).join(' | ')])tr.append(el('td',String(value??'לא זמין')));table.append(tr)}
    const wrap=el('div','','table-wrap');wrap.append(table);status.append(wrap);if(!d.rows.length)status.append(el('p','לא חזרו שורות בחלון הזה; אין להסיק מכך שהטוקן שגוי'));
  }catch(e){status.textContent=e.message}finally{metaRead.disabled=false}};
  save.onclick=async()=>{save.disabled=true;try{await post('/api/connections/shopify/setup',{client_id:id.value,client_secret:secret.value});secret.value='';status.textContent='החיבור נשמר מוצפן וזהות חנות ROSERY אומתה. כעת אפשר לבדוק קריאת דוח'}catch(e){secret.value='';status.textContent=e.message}finally{save.disabled=false}};
  read.onclick=async()=>{read.disabled=true;status.textContent='קורא דוח ישירות מ־Shopify…';try{const d=await post('/api/connections/shopify/report',{});status.replaceChildren(el('h3','דוח Shopify נקרא ישירות בהצלחה'),el('p',d.since+' עד '+d.until+' · '+d.currency),el('p','כל ערוצי המכירה; אינו ייחוס למודעות'));
    const names={day:'יום',orders:'הזמנות',net_sales:'מכירות נטו',shipping_charges:'הכנסות משלוח',sales_reversals:'ביטולי מכירות',total_sales:'סך מכירות'};
    const table=el('table',''),header=el('tr','');for(const c of d.table.columns){const th=el('th',names[c.name]||c.name);th.scope='col';header.append(th)}table.append(header);for(const row of d.table.rows){const tr=el('tr','');for(const c of d.table.columns)tr.append(el('td',String(row[c.name]??'לא זמין')));table.append(tr)}const wrap=el('div','','table-wrap');wrap.append(table);status.append(wrap);
  }catch(e){status.textContent=e.message}finally{read.disabled=false}};
}

