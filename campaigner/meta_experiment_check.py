"""GET-only check: can this machine's Meta connection create a two-version test? Report in Hebrew.

Runs on the local machine later (never in the cloud). Only reads; it never creates, changes or deletes
anything, and never prints the credential. Every path is from a fixed allowlist.

    python meta_experiment_check.py --data-dir <campaigner .local folder>

What it reads (all GET): me/permissions, the ad account (currency, time zone, status, owning business),
the business node, the business's ad studies, and the account's ad studies (any test already running).
"""
from datetime import datetime, timezone
import re

ACCOUNT = '393059510164294'
NEEDED = ('ads_read', 'ads_management')
BUSINESS_SCOPE = 'business_management'
STUDY_FIELDS = 'id,name,type,start_time,end_time,canceled_time'


def _allowed(path):
    return bool(path in ('me/permissions', 'act_' + ACCOUNT, 'act_' + ACCOUNT + '/ad_studies')
                or re.fullmatch(r'\d{5,30}(/ad_studies)?', path))


def _end(value):
    try:
        return datetime.strptime(value, '%Y-%m-%dT%H:%M:%S%z')
    except (TypeError, ValueError):
        return None


def _error(error):
    details = getattr(error, 'details', None) or {}
    code = details.get('code')
    return 'מטא סירבה' + (f' (קוד {code})' if code is not None else '') if details else 'לא הצלחתי לקרוא'


def check(get, now=None, executor_app=None):
    """get(path, params) -> dict, GET only. Returns {'ready', 'items', 'missing', 'text'} in Hebrew.
    executor_app: optional check that the credential is the bound executor app's; when it fails nothing else is read."""
    now = now or datetime.now(timezone.utc)
    items, missing = [], []
    if executor_app is not None:
        try:
            executor_app()
        except Exception:
            text = 'בדיקה לפני ניסוי ראשון (קריאה בלבד, שום דבר לא השתנה):\n✗ החיבור לביצוע לא שייך לאפליקציית הביצוע שהוגדרה במחשב הזה, ולכן לא בדקתי יותר.'
            return {'ready': False, 'items': [{'check': 'אפליקציית הביצוע', 'ok': False, 'detail': text.splitlines()[-1][2:]}],
                    'missing': [text.splitlines()[-1][2:]], 'text': text, 'live_action_allowed': False}

    def read(path, params):
        if not _allowed(path):
            raise ValueError('path not allowed')
        return get(path, params)

    def item(name, ok, detail):
        items.append({'check': name, 'ok': ok, 'detail': detail})
        if ok is not True:
            missing.append(detail)

    business_scope = None
    try:
        granted = {p.get('permission') for p in read('me/permissions', {}).get('data') or [] if p.get('status') == 'granted'}
        lacking = [x for x in NEEDED if x not in granted]
        item('הרשאות פרסום', not lacking, 'יש הרשאה לקרוא ולנהל מודעות' if not lacking else
             'לחיבור חסרה הרשאה לנהל מודעות (' + ', '.join(lacking) + ')')
        business_scope = BUSINESS_SCOPE in granted
        item('הרשאת ניהול עסק', business_scope,
             'יש הרשאת ניהול עסק' if business_scope else
             'לחיבור אין הרשאת ניהול עסק (business_management). לפי המסמכים של מטא, ניסוי נוצר דרך העסק, ולכן כנראה צריך אותה. '
             'שים לב: החיבור לביצוע היום דוחה בכוונה טוקן עם הרשאות נוספות, אז הוספה שלה היא החלטה שלך ודורשת שינוי קטן בקוד')
    except Exception as error:
        item('הרשאות', None, 'לא הצלחתי לבדוק הרשאות: ' + _error(error))
    business_id = None
    try:
        account = read('act_' + ACCOUNT, {'fields': 'account_id,currency,timezone_name,account_status,business{id,name}'})
        ok = account.get('account_id') == ACCOUNT and account.get('currency') == 'ILS'
        item('חשבון המודעות', ok and account.get('account_status') == 1,
             f"החשבון פעיל, בשקלים, אזור זמן {account.get('timezone_name')}" if ok and account.get('account_status') == 1 else
             'החשבון לא פעיל או לא בשקלים, או שזה לא החשבון הנכון')
        business_id = str((account.get('business') or {}).get('id') or '') or None
        item('העסק שמחזיק את החשבון', bool(business_id),
             'נמצא העסק שמחזיק את החשבון' if business_id else 'לא רואים לאיזה עסק החשבון שייך; בלי זה אי אפשר ליצור ניסוי דרך העסק')
    except Exception as error:
        item('חשבון המודעות', None, 'לא הצלחתי לקרוא את חשבון המודעות: ' + _error(error))
    if business_id:
        try:
            read(business_id, {'fields': 'id,name'})
            item('גישה לעסק', True, 'יש גישת קריאה לעסק')
        except Exception as error:
            item('גישה לעסק', False, 'אין גישה לעסק: ' + _error(error))
        try:
            studies = read(business_id + '/ad_studies', {'fields': STUDY_FIELDS, 'limit': '25'}).get('data') or []
            item('ניסויים בעסק', True, f'אפשר לקרוא ניסויים של העסק ({len(studies)} נמצאו)')
        except Exception as error:
            item('ניסויים בעסק', False, 'אי אפשר לקרוא ניסויים של העסק: ' + _error(error) +
                 ('. זה מתאים לחוסר בהרשאת ניהול עסק' if business_scope is False else ''))
    try:
        studies = read('act_' + ACCOUNT + '/ad_studies', {'fields': STUDY_FIELDS, 'limit': '25'}).get('data') or []
        running = [s for s in studies if not s.get('canceled_time') and (_end(s.get('end_time')) or now) > now]
        item('ניסוי שכבר רץ בחשבון', not running,
             'אין ניסוי אחר שרץ בחשבון' if not running else f'כבר רץ ניסוי בחשבון ({len(running)}); מריצים אחד בכל פעם')
    except Exception as error:
        item('ניסויים בחשבון', None, 'לא הצלחתי לקרוא ניסויים בחשבון: ' + _error(error))
    ready = all(i['ok'] is True for i in items)
    lines = ['בדיקה לפני ניסוי ראשון (קריאה בלבד, שום דבר לא השתנה):'] + [('✓ ' if i['ok'] is True else '✗ ' if i['ok'] is False else '? ') + i['detail'] for i in items]
    lines.append('אפשר להכין ניסוי ראשון, אחרי אישור שלך.' if ready else 'עוד לא אפשר להכין ניסוי. מה חסר מופיע למעלה.')
    lines.append('גם כשהכול מוכן, הדבר היחיד שהבדיקה לא יכולה לדעת הוא אם מטא תקבל את הניסוי עצמו. את זה יודעים רק בניסוי הראשון, באישור שלך.')
    return {'ready': ready, 'items': items, 'missing': missing, 'text': '\n'.join(lines), 'live_action_allowed': False}


class GraphGet:
    """GET-only reader over CampaignTransport._request with the allowlist above."""

    def __init__(self, token, base=None):
        if base is None:
            from campaign_operations_transport import CampaignTransport
            base = CampaignTransport()
        self.base, self._token = base, token

    def __call__(self, path, params):
        if not _allowed(path):
            raise ValueError('path not allowed')
        return self.base._request(self._token, path, 'GET', params)


def main(argv=None):
    import argparse
    from pathlib import Path
    parser = argparse.ArgumentParser(description='GET-only Meta experiment readiness check')
    parser.add_argument('--data-dir', required=True)
    args = parser.parse_args(argv)
    # The stored write credential is decrypted here without its *scope* check on purpose: that check rejects a
    # token that also has business_management, which is exactly what this report must be able to say. Every other
    # guard of meta_write_connection.load stays: the token's format, and that it belongs to the separate executor
    # app bound on this computer (a GET). The token is only passed to GET requests and is never printed.
    from shopify_direct import crypt
    import meta_write_connection
    from meta_direct import graph
    folder = Path(args.data_dir)
    token = crypt((folder / 'meta-execution.dpapi').read_bytes(), True).decode()
    print(check(GraphGet(token), executor_app=lambda: _executor_app_ok(token, folder, graph, meta_write_connection._app_id))['text'])


def _executor_app_ok(token, folder, graph, bound_app):
    """True only for a well-formed token of the executor app bound on this computer; raises otherwise."""
    if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{30,4096}', token):
        raise ValueError('Invalid write credential')
    if graph(token, 'app', {'fields': 'id'}).get('id') != bound_app(folder):
        raise ValueError('Wrong application')
    return True


if __name__ == '__main__':
    main()
