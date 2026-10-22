"""Check ad copy and format against the owner's creative rules before upload.

Pure text checks. A clean result is not visual QA, measurement or approval;
masculine-voice copy and Advantage+ settings still need a human look.
"""
import re

from rulebook import cite, param

EMOJI = re.compile(r'[\U0001F000-\U0001FAFF☀-➿⬀-⯿️]')
SUPERLATIVE = re.compile(r'בסט\s*סלר|הנמכר\s+ביותר|הכי\s+(?:טוב|נמכר|מבוקש|יפה)|הטוב\s+ביותר|מספר\s+1\b|#1\b|'
                         r'\bbest[ -]?sell(?:er|ing)\b|\bbest\s+in\b|\bnumber\s+one\b', re.I)
MATERIAL = re.compile(r'נירוסטה|פלדת\s+אל[\s-]?חלד|פלדה\s+כירורגית|\bstainless(?:\s+steel)?\b|\b316l\b', re.I)
OFFER = re.compile(r'\d+\s*%|הנחה|מבצע|1\+1|\bsale\b|\boff\b|במתנה|חינם', re.I)
FREE_SHIPPING = re.compile(r'משלוח\s+חינם\s+(?:מעל|בקנייה\s+מעל|בהזמנה\s+מעל)\s*₪?\s*(\d[\d,]*)\s*₪?')
RETURNS = re.compile(r'החזר(?:ות|ה)\s+(?:חינם\s+)?(?:עד|תוך)\s*(\d+)\s*(?:יום|ימים)')
TYPO = re.compile(r'\bSING\s+UP\b', re.I)


def _texts(row, plural, singular):
    values = row.get(plural)
    if values is None:
        values = [row.get(singular)] if row.get(singular) is not None else []
    return [str(v) for v in values if isinstance(v, str) and v.strip()]


def check_creatives(rows, rulebook):
    result = {'status': 'unavailable', 'issues': [], 'manual_checks': [], 'live_action_allowed': False}
    if rulebook.get('status') != 'available':
        return result
    issues = []

    def add(row, rule, field, text, matched=None, severity='error'):
        issues.append({'asset_id': row.get('asset_id') or row.get('id'), 'name': row.get('name'),
                       'field': field, 'rule_id': rule, 'severity': severity, 'text': text,
                       'matched_text': matched, 'rule': cite(rulebook, rule)})

    need_texts = param(rulebook, 'full_copy_set', 'texts')
    need_headlines = param(rulebook, 'full_copy_set', 'headlines')
    free_from = param(rulebook, 'store_facts', 'free_shipping_from')
    return_days = param(rulebook, 'store_facts', 'returns_days')
    for row in rows:
        primary = _texts(row, 'primary_texts', 'primary_text')
        headlines = _texts(row, 'headlines', 'headline')
        descriptions = _texts(row, 'descriptions', 'description')
        for headline in headlines:
            match = EMOJI.search(headline)
            if match:
                add(row, 'headline_no_emoji', 'headline', 'אסור אימוג׳י בכותרת.', match.group(0))
        for field, values in (('primary_text', primary), ('headline', headlines), ('description', descriptions)):
            for value in values:
                for rule, pattern, text in (
                        ('no_superlatives', SUPERLATIVE, 'סופרלטיב/טענה בלי הוכחה ("בסט סלר", "הכי נמכר" וכו׳).'),
                        ('no_material_emphasis', MATERIAL, 'לא מבליטים את החומר (למשל "נירוסטה" נשמע זול).'),
                        ('known_asset_typo', TYPO, 'שגיאת כתיב ידועה: SING UP → SIGN UP.')):
                    match = pattern.search(value)
                    if match:
                        add(row, rule, field, text, match.group(0))
                match = FREE_SHIPPING.search(value)
                if match and int(match.group(1).replace(',', '')) != free_from:
                    add(row, 'store_facts', field, f'סף משלוח חינם בקופי לא תואם לעובדה בחנות (מעל ₪{free_from}).',
                        match.group(0))
                match = RETURNS.search(value)
                if match and int(match.group(1)) != return_days:
                    add(row, 'store_facts', field, f'ימי החזרה בקופי לא תואמים לעובדה בחנות ({return_days} יום).',
                        match.group(0))
        if any(OFFER.search(t) for t in primary) and headlines and not any(OFFER.search(h) for h in headlines):
            add(row, 'offer_in_headline', 'headline', 'ההצעה מופיעה בטקסט הראשי אבל לא בכותרת. שפת ההצעה צריכה להיות בכותרת.',
                severity='warning')
        cta = row.get('call_to_action')
        if cta is not None and cta != param(rulebook, 'cta_shop_now', 'api_value'):
            add(row, 'cta_shop_now', 'call_to_action', 'ה-CTA חייב להיות "קנה עכשיו" (SHOP_NOW).', str(cta))
        if str(row.get('format') or '').lower() in ('carousel', 'קרוסלה'):
            add(row, 'no_carousel', 'format', 'קרוסלות ברשימה השחורה.')
        if re.search(r'וריאציה|variation', str(row.get('name') or ''), re.I):
            add(row, 'no_variation_label', 'name', 'מודעות "וריאציה" (עריכה מחדש) נכשלות בעקביות.', severity='warning')
        if row.get('expects_full_copy_set', True):
            if len(primary) < need_texts:
                add(row, 'full_copy_set', 'primary_text', f'יש {len(primary)} טקסטים ראשיים — נדרשים {need_texts}.',
                    severity='warning')
            if len(headlines) < need_headlines:
                add(row, 'full_copy_set', 'headline', f'יש {len(headlines)} כותרות — נדרשות {need_headlines}.',
                    severity='warning')
    result.update(status='issues_found' if issues else 'passed_text_checks', issues=issues, manual_checks=[
        {'text': 'לבדוק שכל הקופי בלשון זכר (חריג: "מתנה לה").', 'rule': cite(rulebook, 'masculine_copy')},
        {'text': 'לכבות Text Improvements וכל שיפורי Advantage+ לפני פרסום (לא נקרא חזרה ב-API).',
         'rule': cite(rulebook, 'disable_text_improvements')},
        {'text': 'קריאייטיב חדש עולה לאד-סט חדש מתוארך בתוך הקמפיין הרץ.',
         'rule': cite(rulebook, 'new_creatives_new_dated_adset')}])
    return result
