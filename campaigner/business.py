"""Which business this process serves: its ad account, campaigns, store, money, time zone, language and rules.

Goal (owner, 2026-10-24): the system can later serve other shop owners. Today every module had ROSERY's ids
written in it; they now read them from here. With ROSERY's config every value is exactly what it was
(test_business.py checks each one against the old literal), so ROSERY's behaviour does not change.

One business per process: each business runs its own server with its own data folder, and the business is
chosen when the process starts, before modules read their constants:
- the environment variable CAMPAIGNER_BUSINESS_FILE names a business.json (see load()), or
- nothing is set: ROSERY, built in below.
The server refuses a data folder whose business.json names a different business (see check_data_folder()).
Nothing here reads credentials or calls Meta or Shopify.
"""
import json
import os
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parent
FILE_NAME = 'business.json'
ENV = 'CAMPAIGNER_BUSINESS_FILE'
LANGUAGES = ('he', 'en')


@dataclass(frozen=True)
class Business:
    id: str                       # short tenant id, also the decision-memory and rulebook scope
    name: str                     # brand name as shown and as given to the copy/vision models
    description: str              # one line for model prompts, in English ("Israeli jewelry and apparel brand")
    meta_account: str             # ad account id, digits only, without "act_"
    meta_app_id: str | None       # the owner's Meta app (read connection); None until connected
    meta_sales_campaign: str | None   # campaign the write path and launches work in ("PARENT")
    meta_launch_campaign: str | None  # campaign the read report and registration flow follow ("LAUNCH")
    shop: str | None              # Shopify domain, xxx.myshopify.com; None when the business has no Shopify store
    currency: str                 # ISO 4217, the ad account's and the store's currency
    timezone: str                 # IANA zone of the store's day
    language: str                 # owner-facing language
    country: str                  # ISO 3166 alpha-2, for targeting defaults
    rules_dir: str                # folder with the rulebook sources and the rulebook file
    rulebook_file: str            # file name inside rules_dir
    approval_tenant: str          # tenant id the exact-approval bridge binds to

    @property
    def rules_path(self):
        path = Path(self.rules_dir)
        return path if path.is_absolute() else ROOT / path

    def public(self):
        """What a screen or a report may show (no secrets live here at all)."""
        return asdict(self)


ROSERY = Business(
    id='rosery', name='ROSERY', description='Israeli jewelry and apparel brand',
    meta_account='393059510164294', meta_app_id='1101281589359409',
    meta_sales_campaign='120246379886090730', meta_launch_campaign='120253309360290730',
    shop='21cc82-3.myshopify.com', currency='ILS', timezone='Asia/Jerusalem', language='he', country='IL',
    rules_dir='rules', rulebook_file='rosery-rulebook.json', approval_tenant='rosery-local')


def validate(value):
    """A Business from a business.json dict, or ValueError naming the first wrong field (Hebrew, for the owner)."""
    if not isinstance(value, dict):
        raise ValueError('קובץ העסק אינו תקין')
    fields = set(Business.__dataclass_fields__)
    unknown = set(value) - fields
    if unknown:
        raise ValueError('שדות לא מוכרים בקובץ העסק: ' + ', '.join(sorted(unknown)))
    defaults = {'meta_app_id': None, 'meta_sales_campaign': None, 'meta_launch_campaign': None, 'shop': None,
                'description': 'online shop', 'rules_dir': 'rules', 'rulebook_file': 'rulebook.json'}
    data = {**defaults, **value}
    data.setdefault('approval_tenant', str(data.get('id', '')) + '-local')
    missing = fields - set(data)
    if missing:
        raise ValueError('חסרים שדות בקובץ העסק: ' + ', '.join(sorted(missing)))
    checks = [
        ('id', lambda v: isinstance(v, str) and re.fullmatch(r'[a-z][a-z0-9-]{1,39}', v)),
        ('name', lambda v: isinstance(v, str) and 0 < len(v.strip()) <= 80),
        ('description', lambda v: isinstance(v, str) and 0 < len(v) <= 200),
        ('meta_account', lambda v: isinstance(v, str) and re.fullmatch(r'\d{5,20}', v)),
        ('meta_app_id', lambda v: v is None or (isinstance(v, str) and re.fullmatch(r'\d{5,20}', v))),
        ('meta_sales_campaign', lambda v: v is None or (isinstance(v, str) and re.fullmatch(r'\d{5,25}', v))),
        ('meta_launch_campaign', lambda v: v is None or (isinstance(v, str) and re.fullmatch(r'\d{5,25}', v))),
        ('shop', lambda v: v is None or (isinstance(v, str) and re.fullmatch(r'[a-z0-9][a-z0-9-]*\.myshopify\.com', v))),
        ('currency', lambda v: isinstance(v, str) and re.fullmatch(r'[A-Z]{3}', v)),
        ('timezone', _zone),
        ('language', lambda v: v in LANGUAGES),
        ('country', lambda v: isinstance(v, str) and re.fullmatch(r'[A-Z]{2}', v)),
        ('rules_dir', lambda v: isinstance(v, str) and v and '..' not in Path(v).parts),
        ('rulebook_file', lambda v: isinstance(v, str) and Path(v).name == v and v.endswith('.json')),
        ('approval_tenant', lambda v: isinstance(v, str) and re.fullmatch(r'[a-z][a-z0-9-]{1,49}', v)),
    ]
    for field, ok in checks:
        if not ok(data[field]):
            raise ValueError('ערך לא תקין בקובץ העסק: ' + field)
    return Business(**data)


def _zone(value):
    try:
        return isinstance(value, str) and bool(ZoneInfo(value))
    except (ZoneInfoNotFoundError, ValueError):
        return False


def load(path):
    """The business described by a business.json. A relative rules_dir is relative to that file's folder."""
    path = Path(path)
    business = validate(json.loads(path.read_text(encoding='utf-8')))
    if not Path(business.rules_dir).is_absolute():
        business = replace(business, rules_dir=str((path.parent / business.rules_dir).resolve()))
    return business


def active():
    """The business of this process: CAMPAIGNER_BUSINESS_FILE if set, else ROSERY."""
    configured = os.environ.get(ENV)
    return load(configured) if configured else ROSERY


def check_data_folder(data_dir, business):
    """Refuse a data folder that belongs to another business (its business.json names another id/account).
    A folder without business.json is the original ROSERY layout and is accepted for ROSERY only."""
    path = Path(data_dir) / FILE_NAME
    if not path.exists():
        if business.id != ROSERY.id:
            raise ValueError('בתיקיית הנתונים אין קובץ עסק; אי אפשר להפעיל עליה עסק אחר מ-ROSERY')
        return
    stored = load(path)
    if (stored.id, stored.meta_account) != (business.id, business.meta_account):
        raise ValueError('תיקיית הנתונים שייכת לעסק אחר (' + stored.name + ')')


def write(data_dir, business):
    """business.json for a new business, written atomically into its data folder."""
    folder = Path(data_dir)
    folder.mkdir(parents=True, exist_ok=True)
    pending = folder / (FILE_NAME + '.pending')
    pending.write_text(json.dumps(asdict(business), ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(pending, folder / FILE_NAME)


ACTIVE = active()
