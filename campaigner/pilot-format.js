const pilotFormat={
  money(amount){
    const v=amount?.value;
    if(v===null||v===undefined||!['string','number'].includes(typeof v)||typeof v==='string'&&!v.trim()||!Number.isFinite(Number(v)))return 'לא זמין';
    try{return new Intl.NumberFormat('he-IL',{style:'currency',currency:amount.unit||'ILS',maximumFractionDigits:2}).format(Number(v))}catch{return 'לא זמין'}
  },
  period(query){
    const dates=[...new Set((query||'').match(/\d{4}-\d{2}-\d{2}/g)||[])];
    if(!dates.length)return 'התקופה לא צוינה';
    const format=d=>new Date(d+'T12:00:00Z').toLocaleDateString('he-IL',{day:'numeric',month:'long',year:'numeric',timeZone:'Asia/Jerusalem'});
    if(dates.some(d=>!Number.isFinite(Date.parse(d+'T12:00:00Z'))))return 'התקופה לא תקינה';
    return dates.length===1?format(dates[0]):format(dates[0])+' – '+format(dates[dates.length-1]);
  },
  campaignName(campaign){return typeof campaign?.name==='string'&&campaign.name.trim()?campaign.name:'שם הקמפיין לא זמין'}
};
if(typeof module!=='undefined'&&module.exports)module.exports=pilotFormat;
