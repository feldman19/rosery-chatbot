"""Trusted local-admin import of normalized reports, not an authenticated client API."""
import argparse
import json
from pathlib import Path
from evidence import SnapshotStore


def main():
    parser = argparse.ArgumentParser(description='Import normalized read-only evidence; no live connector')
    parser.add_argument('--db', default='.local/evidence.sqlite3')
    parser.add_argument('--tenant', required=True)
    parser.add_argument('--platform', required=True, choices=('meta', 'shopify'))
    parser.add_argument('--account', required=True, help='Trusted local binding, not inferred from report')
    parser.add_argument('file', type=Path)
    args = parser.parse_args()
    payload = json.loads(args.file.read_text(encoding='utf-8-sig'))
    if payload.get('account_id') != args.account or payload.get('platform') != args.platform:
        parser.error('Report identity does not match explicitly selected account/platform')
    path = Path(args.db)
    path.parent.mkdir(parents=True, exist_ok=True)
    store = SnapshotStore(str(path))
    store.bind_account(args.tenant, args.platform, args.account)
    result = store.ingest(args.tenant, payload)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
