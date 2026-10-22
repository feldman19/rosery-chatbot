"""Owner-written ROSERY rulebook, bound to exact source text.

Each rule must quote its source verbatim; a changed or missing source file
disables the whole rulebook (fail closed). Rules produce proposals only and
never grant execution authority.
"""
import hashlib
import json
from pathlib import Path

ACCOUNT = '393059510164294'
DIRECTORY = Path(__file__).resolve().parent / 'rules'
USES = {'engine', 'creative_check', 'review', 'context'}


def load_rulebook(directory=None, *, business_id='rosery', account_id=ACCOUNT):
    directory = Path(directory or DIRECTORY)
    result = {'status': 'unavailable', 'rules': {}, 'live_action_allowed': False}
    if business_id != 'rosery' or account_id != ACCOUNT:
        return {**result, 'reason': 'tenant_mismatch'}
    try:
        book = json.loads((directory / 'rosery-rulebook.json').read_text(encoding='utf-8'))
        if (book.get('business_id'), book.get('account_id'), book.get('version')) != ('rosery', ACCOUNT, 1):
            raise ValueError('rulebook_scope_mismatch')
        texts = {}
        for name, metadata in book['sources'].items():
            if Path(name).name != name:
                raise ValueError('invalid_source_path')
            path = directory / name
            if path.is_symlink() or path.stat().st_size > 1_000_000:
                raise ValueError('invalid_source')
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != metadata['sha256']:
                raise ValueError('source_changed:' + name)
            texts[name] = raw.decode('utf-8-sig')
        rules = {}
        for rule in book['rules']:
            if rule['id'] in rules or rule['use'] not in USES or rule['source'] not in texts:
                raise ValueError('invalid_rule:' + str(rule.get('id')))
            if not rule['source_excerpt'] or rule['source_excerpt'] not in texts[rule['source']]:
                raise ValueError('unsupported_rule:' + rule['id'])
            rules[rule['id']] = {**rule, 'params': dict(rule.get('params') or {})}
        return {**result, 'status': 'available', 'business_id': business_id, 'account_id': account_id,
                'version': book['version'], 'owner': book.get('owner'), 'rules': rules,
                'sources': {name: dict(meta) for name, meta in book['sources'].items()},
                'source_ref': 'campaigner/rules/rosery-rulebook.json'}
    except (OSError, ValueError, KeyError, TypeError, UnicodeDecodeError) as error:
        return {**result, 'reason': 'missing_changed_or_invalid_rulebook', 'detail': str(error)[:200]}


def param(rulebook, rule_id, key):
    """Exact parameter lookup; a missing rule or parameter is an error, never a default."""
    return rulebook['rules'][rule_id]['params'][key]


def cite(rulebook, rule_id):
    rule = rulebook['rules'][rule_id]
    return {'rule_id': rule_id, 'source': rule['source'], 'quote': rule['source_excerpt']}
