"""Resolve an owner's explicit ROSERY destination without fetching or guessing."""
import re
from urllib.parse import urlsplit


def resolve_destination(goal):
    links = re.findall(r'https?://[^\s<>"\u05f4]+', goal or '')
    destinations = []
    for raw in links:
        raw = raw.rstrip('.,;!?)\u05f3')
        try:
            parsed = urlsplit(raw)
            host = (parsed.hostname or '').lower()
            port = parsed.port
        except ValueError:
            raise ValueError('קישור היעד בבקשה אינו תקין') from None
        if host in ('drive.google.com', 'docs.google.com'):
            continue
        if (parsed.scheme != 'https' or host != 'roseryjewelry.com'
                or parsed.username or parsed.password or port not in (None,443)
                or re.search(r'(token|secret|password|api.?key|code)=',parsed.query,re.I)):
            raise ValueError('קישור היעד אינו נתמך בחיבור ROSERY. יש לבחור כתובת HTTPS באתר roseryjewelry.com; לא נחליף אותה בדף הבית')
        if raw not in destinations:
            destinations.append(raw)
    if len(destinations)>1:
        raise ValueError('נמצאו כמה דפי יעד בבקשה. יש לבחור דף יעד אחד להכנה הזאת')
    return {'url': destinations[0] if destinations else 'https://roseryjewelry.com/',
            'source': 'owner_brief' if destinations else 'business_default',
            'verified_live': False}
