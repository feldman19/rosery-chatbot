async function renderReviewHistory(pages){
  const box=el('section','','panel bottom-panel');
  const head=el('div','','panel-head');head.append(el('h2','יומן בדיקות'),el('span','תיעוד שנשמר'));box.append(head);
  pages.issues.insertBefore(box,pages.issues.children[1]||null);
  try{
    const response=await fetch('/api/reviews',{headers:{'X-Local-Session':token}});
    if(!response.ok)throw Error('Unavailable');const result=await response.json();
    if(!result.available){box.append(el('p','עדיין אין בדיקות שמורות זמינות','source-card'));return}
    for(const row of result.entries){
      const card=el('article','','source-card');
      card.append(el('h3','בדיקה מ־'+row.date+' · יום מלא עד '+row.period_end));
      card.append(el('p',`הוצאה: ${row.spend_ils??'לא זמין'} ₪ · הרשמות מיוחסות: ${row.registrations??'לא זמין'} · עלות לאירוע: ${row.cpl_ils??'לא זמין'} ₪`));
      card.append(el('p',`תקציב שנצפה: ${row.budget_ils??'לא זמין'} ₪ ביום · רשימת פלאשי: ${row.flashy_contacts??'לא זמין'} אנשי קשר מכל המקורות`,'muted'));
      card.append(el('p','לא בוצעו שינויים בפרסום בסבב זה'));
      const detail=el('details','');detail.append(el('summary','מקור הבדיקה'),el('p',row.source_file),el('p','דוח שמור; אינו רענון אוטומטי או אימות נרשם ייחודי'));card.append(detail);box.append(card);
    }
    box.append(el('p',result.next_step,'source-card'),el('p',result.comparison,'panel-foot'));
    const latest=result.entries[0];
    const notice=el('div',`בדיקה שמורה חדשה יותר זמינה ביומן: ${latest.date}. המדדים האחרים במסך עשויים להשתייך לבדיקה קודמת; יש להשוות רק חלונות תואמים.`,'notice');
    pages.overview.prepend(notice);
  }catch{box.append(el('p','לא ניתן לקרוא את יומן הבדיקות. אין להסיק שלא בוצעו בדיקות','source-card'))}
}
