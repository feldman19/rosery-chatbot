"""Audience evidence from included targeting; frequency is descriptive only."""
def classify_audience(targeting):
    if not isinstance(targeting, dict):
        return {'audience_type': 'unknown', 'audience_reason': 'הטירגוט לא נקרא'}
    if targeting.get('custom_audiences'):
        return {'audience_type': 'retargeting', 'audience_reason': 'הטירגוט כולל קהלים מותאמים'}
    if targeting.get('product_audience_specs'):
        return {'audience_type': 'retargeting', 'audience_reason': 'הטירגוט כולל קהל מוצרי קטלוג'}
    return {'audience_type': 'prospecting', 'audience_reason': 'קהל רחב, בלי קהלים מותאמים'}


BUYER_SHARE_MISSING = 'חסרים בקריאת Shopify: utm_content ברמת הזמנה התואם לשם מודעה או למזהה שלה, וסטטוס לקוח חדש/חוזר בזמן ההזמנה. לא ניתן לחשב שיעור לפי אד-סט.'
