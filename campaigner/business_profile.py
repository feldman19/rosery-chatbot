"""Local owner settings. No provider access; one store per business data directory."""
from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import threading
import uuid

_LOCK = threading.RLock()
SOURCES = ('owner', 'owner approved a system suggestion')


def guidance():
    return json.loads(Path(__file__).with_name('enhancement_guidance.json').read_text(encoding='utf-8'))


# Owner decision 2026-10-23: "Relevant comments" stays on; every other Meta change is off until the owner allows it.
DEFAULT_ALLOWED = frozenset({'inline_comment'})


def feature_default(key):
    return 'allowed' if key in DEFAULT_ALLOWED else 'off'


def feature_keys():
    from creative_launch import ENHANCEMENTS_OFF
    return [*ENHANCEMENTS_OFF, 'inline_comment']


# key: (section, label, effect, default, type)
SETTINGS = {
    'banned_copy_words': ('brand', 'מילים וביטויים אסורים', 'קופי שמכיל ביטוי מהרשימה יידחה בבדיקת הטענות', [], 'list'),
    'tone_notes': ('brand', 'הערות סגנון', 'עוד לא בשימוש — יחובר בהמשך', '', 'text'),
    'excluded_products': ('products', 'מוצרים שלא לקדם', 'עוד לא בשימוש — יחובר בהמשך', [], 'list'),
    'max_daily_budget_minor': ('budget', 'תקציב יומי מרבי באגורות לאד סט', 'מגביל הצעות השקה והגדלת תקציב; ריק פירושו ללא מגבלה נוספת', None, 'budget'),
    'max_increase_percent': ('budget', 'אחוז הגדלה מרבי בצעד', 'מגביל הצעות להגדלת תקציב בנוסף לחוקי הבסיס', 20, 'percent'),
    'no_change_dates': ('special_dates', 'תאריכים ללא שינויים', 'עוד לא בשימוש — יחובר בהמשך', [], 'dates'),
}
SECTIONS = {'enhancements': 'שינויים אוטומטיים של מטא', 'donts': 'דברים שהמערכת לא תעשה',
            'brand': 'המותג', 'products': 'מוצרים', 'budget': 'תקציב', 'special_dates': 'ימים מיוחדים'}


def _validate(key, value):
    if key in feature_keys():
        if not isinstance(value, str) or value not in ('off', 'allowed'):
            raise ValueError('ערך הגדרה או בקשה לא תקינים')
        return
    if key not in SETTINGS:
        raise ValueError('ערך הגדרה או בקשה לא תקינים')
    kind = SETTINGS[key][4]
    if kind in ('list', 'dates'):
        if not isinstance(value, list) or len(value) > 200 or any(not isinstance(v, str) or not v.strip() or len(v) > 200 for v in value) or len(set(value)) != len(value):
            raise ValueError('ערך הגדרה או בקשה לא תקינים')
        if kind == 'dates':
            for v in value:
                if date.fromisoformat(v).isoformat() != v:
                    raise ValueError('ערך הגדרה או בקשה לא תקינים')
    elif kind == 'text':
        if not isinstance(value, str) or len(value) > 4000:
            raise ValueError('ערך הגדרה או בקשה לא תקינים')
    elif kind == 'budget':
        if value is not None and (type(value) is not int or not 1 <= value <= 200000):
            raise ValueError('ערך הגדרה או בקשה לא תקינים')
    elif type(value) not in (int, float) or not 0 < value <= 20:
        raise ValueError('ערך הגדרה או בקשה לא תקינים')


class BusinessProfile:
    def __init__(self, directory):
        self.path = Path(directory) / 'business-profile.json'

    def _load(self):
        if not self.path.exists():
            return {'schema_version': 1, 'version': 0, 'settings': {}, 'donts': [], 'history': []}
        value = json.loads(self.path.read_text(encoding='utf-8'))
        if value.get('schema_version') != 1:
            raise ValueError('ערך הגדרה או בקשה לא תקינים')
        return value

    def value(self, key):
        with _LOCK:
            row = self._load()['settings'].get(key)
            return deepcopy(row['value'] if row else (feature_default(key) if key in feature_keys() else SETTINGS[key][3]))

    def allowed(self, feature):
        return feature in feature_keys() and self.value(feature) == 'allowed'

    def binding(self):
        with _LOCK:
            state = self._load()
            return {'version': state['version'], 'sha256': hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()}

    def reasons(self):
        with _LOCK:
            state = self._load()
        return [{'feature': k, 'reason': 'לפי ההגדרות שלך מ-' + datetime.fromisoformat(v['updated_at']).strftime('%d/%m')}
                for k, v in state['settings'].items() if k in feature_keys() and v['value'] == 'allowed']

    def get(self):
        with _LOCK:
            state = self._load()
        sections = {k: {'key': k, 'label': label, 'settings': []} for k, label in SECTIONS.items()}
        research = guidance()
        for key in [*feature_keys(), *SETTINGS]:
            is_feature = key in feature_keys()
            info = research.get(key, {}) if is_feature else {}
            section, label, effect, default = ('enhancements', info.get('label', key), 'כבוי: ייכבה בהצעות ובפעולת כיבוי; מותר: נשאר לברירת המחדל של מטא', feature_default(key)) if is_feature else SETTINGS[key][:4]
            row = state['settings'].get(key, {})
            verdict = info.get('verdict', 'unknown')
            sections[section]['settings'].append({'key': key, 'label': label, 'sees': info.get('sees'), 'what_it_changes': effect,
                'value': deepcopy(row.get('value', default)), 'default': deepcopy(default), 'updated_at': row.get('updated_at'),
                'recommended': {'value': ('off' if verdict == 'harmful' else 'allowed' if verdict == 'safe' else None) if is_feature else None,
                                'reason': info.get('reason', ''), 'sources': info.get('sources', []), 'verdict': verdict}})
        sections['donts']['entries'] = state['donts']
        return {**state, 'sections': list(sections.values()), 'binding': self.binding(), 'allowed_reasons': self.reasons(), 'provider_writes': 0}

    def _save(self, state, what, old, new, source):
        stamp = datetime.now(timezone.utc).isoformat()
        state['version'] += 1
        state['history'].append({'at': stamp, 'what': what, 'old': deepcopy(old), 'new': deepcopy(new), 'source': source})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('w', encoding='utf-8') as stream:
                json.dump(state, stream, ensure_ascii=False, allow_nan=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def update(self, body):
        if not isinstance(body, dict) or set(body) - {'settings', 'source'} or not isinstance(body.get('settings'), dict) or not body['settings'] or body.get('source', 'owner') not in SOURCES:
            raise ValueError('ערך הגדרה או בקשה לא תקינים')
        for k, v in body['settings'].items():
            _validate(k, v)
        with _LOCK:
            state = self._load()
            old = {k: self.value(k) for k in body['settings']}
            stamp = datetime.now(timezone.utc).isoformat()
            for k, v in body['settings'].items():
                state['settings'][k] = {'value': deepcopy(v), 'updated_at': stamp}
            self._save(state, 'settings', old, body['settings'], body.get('source', 'owner'))
            return self.get()

    def dont(self, body):
        if not isinstance(body, dict):
            raise ValueError('ערך הגדרה או בקשה לא תקינים')
        with _LOCK:
            state = self._load()
            old = deepcopy(state['donts'])
            if set(body) == {'remove_id'} and isinstance(body['remove_id'], str):
                if not any(r['id'] == body['remove_id'] for r in old):
                    raise ValueError('ערך הגדרה או בקשה לא תקינים')
                state['donts'] = [r for r in old if r['id'] != body['remove_id']]
            else:
                if set(body) != {'kind', 'scope', 'entity_id', 'reason'} or body['scope'] not in ('ad', 'adset', 'everywhere') or any(not isinstance(body[k], str) for k in body):
                    raise ValueError('ערך הגדרה או בקשה לא תקינים')
                if not body['kind'].strip() or len(body['kind']) > 100 or not body['reason'].strip() or len(body['reason']) > 1000 or (body['scope'] == 'everywhere' and body['entity_id']) or (body['scope'] != 'everywhere' and (not body['entity_id'].isdigit() or len(body['entity_id']) > 40)):
                    raise ValueError('ערך הגדרה או בקשה לא תקינים')
                state['donts'].append({**body, 'id': uuid.uuid4().hex, 'date': datetime.now(timezone.utc).isoformat()})
            self._save(state, 'donts', old, state['donts'], 'owner')
            return self.get()

    def annotate(self, report):
        with _LOCK:
            rules = self._load()['donts']
        items = (((report.get('recommendations') or {}).get('today') or {}).get('items') or []) + ((report.get('creative_drift') or {}).get('items') or [])
        hidden = report.setdefault('dismissed_items', [])
        for item in items:
            kind = item.get('action') or ('disable_enhancements' if 'features' in item else item.get('kind'))
            for rule in rules:
                match = rule['scope'] == 'everywhere' or (rule['scope'] == 'ad' and rule['entity_id'] == (item.get('ad_id') or item.get('entity_id'))) or (rule['scope'] == 'adset' and rule['entity_id'] == (item.get('adset_id') or (item.get('entity_id') if kind == 'scale_adset_budget' or item.get('entity_type') == 'adset' else None)))
                if kind == rule['kind'] and match:
                    item.update(dismissed=True, hidden_reason='מוסתר לפי ההגדרה שלך', dont_id=rule['id'])
                    if item not in hidden:
                        hidden.append(item)
                    break
        proposals = ((report.get('recommendations') or {}).get('rule_engine') or {}).get('proposals') or []
        for proposal in proposals:
            if proposal.get('kind') == 'scale_adset_budget':
                try:
                    self.check_budget((proposal.get('after') or {}).get('daily_budget_minor'), (proposal.get('before') or {}).get('daily_budget_minor'))
                except ValueError:
                    proposal['ready_for_approval'] = False
                    proposal['blockers'] = list(set(proposal.get('blockers') or []) | {'business_profile_budget_limit'})
        return report

    def check_copy(self, texts):
        for word in self.value('banned_copy_words'):
            if any(word.casefold() in text.casefold() for text in texts):
                raise ValueError('הקופי מכיל ביטוי שאסרת: ' + word)

    def check_budget(self, after, before=None):
        maximum = self.value('max_daily_budget_minor')
        if type(after) is not int or (before is not None and type(before) is not int):
            raise ValueError('תקציב לא ידוע או לא תקין')
        if maximum is not None and after > maximum:
            raise ValueError('התקציב חורג מההגדרה שלך')
        if before is not None and after > round(before * (1 + self.value('max_increase_percent') / 100)):
            raise ValueError('ההגדלה חורגת מההגדרה שלך')


def creation_recipe(profile):
    from creative_launch import CREATION_RECIPE
    recipe = deepcopy(CREATION_RECIPE)
    recipe['creative_fields']['degrees_of_freedom_spec']['creative_features_spec'] = {f: {'enroll_status': 'OPT_OUT'} for f in feature_keys() if not profile.allowed(f)}
    # A default-allowed feature the owner keeps is set on explicitly, as before the profile existed.
    features = recipe['creative_fields']['degrees_of_freedom_spec']['creative_features_spec']
    features.update({f: {'enroll_status': 'OPT_IN'} for f in sorted(DEFAULT_ALLOWED) if profile.allowed(f)})
    return recipe


def validate_features(features):
    if not isinstance(features, dict) or any(k not in feature_keys() or v not in ({'enroll_status': 'OPT_OUT'}, *([{'enroll_status': 'OPT_IN'}] if k in DEFAULT_ALLOWED else [])) for k, v in features.items()):
        raise ValueError('ערך הגדרה או בקשה לא תקינים')
