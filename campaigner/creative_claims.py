"""Bounded heuristic claim gate, not semantic truth verification.

No product-scoped verified-claim contract is supplied to this path yet. OCR and
model warnings are observations, never authorization to publish factual claims.
"""
import re
import unicodedata
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone

PATTERNS = {
    'material': r'(?:זהב\s*(?:מלא|אמיתי|טהור)|כסף\s*(?:אמיתי|טהור|סטרלינג|925)|(?:ציפוי|מצופה)\s*(?:זהב|כסף)|(?:טבעת|שרשרת|צמיד|תכשיט)(?:ים|ות)?\s+(?:מ)?(?:זהב|כסף)|\b(?:solid\s+gold|sterling\s+silver|gold[- ]plated|genuine\s+leather)\b|\b(?:כותנה|קשמיר|פשתן|משי|עור\s+אמיתי)\b|\d+\s*(?:קראט|karat|carat))',
    'production': r'(?:עבודת\s*יד|בעבודת\s*יד|ייצור\s*(?:ישראלי|מקומי)|מיוצר\s*בישראל|\bhandmade\b|\bhandcrafted\b)',
    'delivery': r'(?:משלוח(?:ים)?\s*(?:חינם|חינמי|מהיר|תוך|עד|ב[־-]?\s*\d)|אספקה\s*(?:תוך|עד)|מגיע(?:ים)?\s*(?:תוך|עד)\s*\d|\bfree\s+shipping\b|\b(?:delivery|shipping)\s+(?:within|in)\s*\d)',
    'offer': r'(?:\d+(?:\.\d+)?\s*%|הנחה|מבצע|\bdiscount\b|\bsale\b|\d+(?:\.\d+)?\s*(?:₪|שקל(?:ים)?|ש[״"\']?ח)|[₪$€]\s*\d)',
    'guarantee': r'(?:אחריות|החזר(?:ה|ות)?\s*(?:חינם|מלא|כספי)|עמיד(?:ים|ות)?\s*(?:במים|למים)|ללא\s*ניקל|היפואלרגני|\bwarranty\b|\bguarantee\b|\bwaterproof\b|\bhypoallergenic\b)',
    'social_proof': r'(?:\d[\d,.]*\s*(?:לקוחות|ביקורות|דירוגים|כוכבים|reviews|customers|stars)|(?:אלפי|מאות|מיליוני)\s*(?:לקוחות|ביקורות)|דירוג\s*\d)',
}
COPY_FIELDS = ('primary_text','headline','description')
FIELD_LABELS={'primary_text':'טקסט ראשי','headline':'כותרת','description':'תיאור','observed_product':'זיהוי המוצר'}


class CreativeClaimReviewRequired(ValueError):
    status = 'needs_fact_review'
    def __init__(self, issues):
        self.issues = issues
        first = issues[0]
        super().__init__('נדרש אימות עובדה: בקובץ ״'+str(first['source_name'])+'״, בשדה '+FIELD_LABELS[first['field']]+' ('+first['field']+'), הטענה ״'+first['claim']+'״ אינה מגובה במקור שסופק. אין קביעה שהיא שגויה. הטקסט לא שונה; טקסט בתמונה אינו אישור שההצעה בתוקף.')


def validate_creative_claims(rows, source_names=None):
    issues=[]
    for row in rows:
        for field in (*COPY_FIELDS,'observed_product'):
            text=row.get(field)
            if not isinstance(text,str):continue
            normalized=''.join(c for c in unicodedata.normalize('NFKC',text) if unicodedata.category(c) not in ('Cf','Mn'))
            for kind, pattern in PATTERNS.items():
                if field=='observed_product' and kind!='material':continue
                candidate=normalized
                if kind=='material':
                    # Color observations are allowed; never rewrite returned copy.
                    candidate=re.sub(r'(?:בגוון|בצבע|בגווני)\s+(?:זהב|כסף)(?:\s*[/־-]\s*(?:זהב|כסף|אפור))?|\b(?:gold|silver)[- ]colou?red\b','',candidate,flags=re.I)
                match=re.search(pattern,candidate,re.I)
                if match:issues.append({'asset_id':row.get('asset_id'),'source_name':(source_names or {}).get(row.get('asset_id')) or row.get('asset_id'),'field':field,'category':kind,'claim':match.group(0),'status':'needs_fact_review','coverage':'heuristic_not_semantic_guarantee'})
    if issues:raise CreativeClaimReviewRequired(issues)


def preserve_blocked_review(directory, value, sources, issues):
    """Immutable advisory evidence; never accepted as a validated draft receipt."""
    payload={'version':1,'status':'needs_fact_review','checked_at':datetime.now(timezone.utc).isoformat(),
             'automatic_reuse':False,'execution_authority':False,'issues':issues,
             'sources':[{key:s.get(key) for key in ('asset_id','sha256','name','format','review_sha256')} for s in sources],
             'review':value,'coverage':'heuristic_not_semantic_guarantee'}
    raw=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    if len(raw)>1024*1024:raise ValueError('Blocked review exceeds storage bound')
    identity=hashlib.sha256(raw).hexdigest()
    root=Path(directory)/'creative-blocked-reviews'
    if root.is_symlink():raise ValueError('Unsafe blocked review directory')
    root.mkdir(exist_ok=True)
    path=root/(identity+'.json')
    with path.open('xb') as stream:stream.write(raw)
    return identity
