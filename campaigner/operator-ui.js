/* One practical next action; provider writes are never implied by a card. */
function renderOperatorPanel(parent,report){
  const box=el('section','','live-panel');box.setAttribute('aria-label','עבודת הקמפיינר');
  box.style.cssText='border-inline-start:4px solid #7255d8;background:#fcfbff';
  box.append(el('h2',report?.headline||'מכין את בדיקת הקמפיינר…'),el('p',report?.summary||'בדיקת הגדרות, מסירה וקישורים מול הנתונים שנקראו.','live-note'));
  if(report){
    const cases=report.cases||[];
    if(cases.length){
      const item=cases[0],detail=el('details');detail.dataset.liveKey='operator-'+item.id;
      detail.append(el('summary',item.action_label+': '+item.title));
      if(item.type==='tracking_fix'){
        detail.append(el('p',item.explanation),el('p','הכנתי כתובת נפרדת לכל מודעה; התיקון עדיין לא הוחל.'));
        for(const ad of item.evidence.ads){const card=el('article','','live-panel');card.append(el('strong',ad.name.match(/PL\d+[A-Z]?/i)?.[0]||ad.name));
          const technical=el('details');technical.append(el('summary','הכתובת והשדות המדויקים לתיקון'),el('p','מודעת מקור: '+ad.ad_id));
          const before=el('p','כתובת קיימת: '+ad.url),after=el('p','כתובת מוצעת: '+ad.proposed_url);before.dir='ltr';after.dir='ltr';before.style.overflowWrap=after.style.overflowWrap='anywhere';technical.append(before,after);
          technical.append(el('p','URL parameters קיים: '+(ad.url_tags||'(ריק)')),el('p','URL parameters מוצע: '+(ad.proposed_url_tags||'(לרוקן את השדה)')),el('p','שדות הכתובת: '+ad.paths.join(' · ')),el('p','הסימון מזהה את מודעת המקור. אם נוצרת מודעה חלופית, חייבים לשמור שיוך למזהה החדש לפני הפעלה.','live-note'));card.append(technical);
          const link=el('a','פתח את המודעה ב־Meta');link.href='https://adsmanager.facebook.com/adsmanager/manage/ads/edit?act='+report.account_id+'&selected_ad_ids='+ad.ad_id;link.target='_blank';link.rel='noopener noreferrer';card.append(link);detail.append(card)}
        detail.append(el('p',item.next_step),el('p',item.risk,'live-note'),el('p','בדיקת סיום: '+item.check_after,'live-note'));
      }else{detail.append(el('p',item.task.explanation));const next=item.task.proposal?.inspect;if(next)detail.append(el('p','נדרשת בדיקה: '+next.join(' · ')));}
      detail.append(el('p','מוכן לבחינה בלבד · לא בוצע שינוי בפרסום','live-note'));box.append(detail);
      if(cases.length>1){const more=el('details');more.dataset.liveKey='operator-more';more.append(el('summary','עוד '+(cases.length-1)+' תיקי טיפול'));for(const other of cases.slice(1))more.append(el('p',other.title));box.append(more)}
    }else if(report.analysis_cases?.length){for(const item of report.analysis_cases)box.append(el('p',item.explanation||item.title||''));}
    const experiment=report.experiment;
    if(experiment?.question&&experiment.proposal){const exp=el('details');exp.dataset.liveKey='operator-experiment';exp.append(el('summary',experiment.blocked_by_tracking?'רעיון לניסוי תמונות — ממתין לתיקון המדידה':'בריף לניסוי תמונות — ממתין לבדיקות ואישור'),el('p',experiment.question));const p=experiment.proposal;if(experiment.blocked_by_tracking)exp.append(el('p','קודם משלימים את תיקון סימוני הקישורים ואת בדיקת שמירתם באתר. הבריף חסום להפעלה.'));exp.append(el('p','משנים רק תמונה; שומרים על אותו טקסט, דף נחיתה וקהל. לכל זרוע יהיה סימון כניסה נפרד.'),el('p','מודעת בסיס: '+p.baseline_ad_id+' · תמונה להשוואה מתוך מודעה: '+p.image_reference_ad_id));for(const text of p.copy_to_hold?.primary_texts||[])exp.append(el('p',text));exp.append(el('p','זו הכנת ניסוי בלבד. לפני הפעלה נדרשים אימות מדידה, בדיקת תצוגה ואישור מסגרת התקציב. לא נבחרה תמונה מנצחת ולא נוסף תקציב.','live-note'));box.append(exp)}
    box.append(el('p','מבוסס על קריאת Meta: '+(report.source_observed_at?new Date(report.source_observed_at).toLocaleString('he-IL'):'טרם אומתה')+' · '+report.scope,'live-note'));
  }
  parent.append(box);
}
