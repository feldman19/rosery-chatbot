Reliability: a daily self-check so the owner can trust every number. New module `campaigner/self_check.py` + tests; keep edits to `server.py` / `daily_brief.py` minimal (another task edits `simple-web.html`, `management_report.py`, `rule_engine.py`, `meta_direct.py` on another branch — do not touch those). Do not commit; Hebrew report; GET-only toward Meta/Shopify; no rules edits; never read or print credentials.

1. Checks (each: ok / warn / fail, a one-line Hebrew reason, the numbers compared):
   - Meta numbers: yesterday's account spend, purchases and attributed revenue in the report (`windows['1']`) vs a direct account-level insights read for the same date (same attribution settings as the report). Tolerance: spend ±1%, purchases exact, revenue ±1%.
   - Shopify: yesterday's store orders and revenue in the report vs a direct ShopifyQL read for the same date.
   - Freshness: last successful Meta and Shopify reads < 30 minutes; the Meta report token and the write token still verify (use the existing `status()` helpers, which return only booleans).
   - Phone gateway reachable on its port (a TCP connect, no request).
   - Rulebook verified (hash binding) and the latest full test run not newer-broken (optional: skip if not available).
2. Run once at server start (after the first refresh) and every morning at 07:30 Israel time; store the result in the data folder (`self-check.json`, last 30 runs).
3. Surface: `/api/self-check` (owner session), a line at the top of the morning brief text ("בדיקה עצמית: הכל תקין" or the failing items), and a `status` field the screen can use later.
4. Tests with fakes for every check (match, mismatch, missing data = warn not fail, network error = warn).
Run the full suite (`cd campaigner && python -B -m unittest discover -p "test_*.py"`; baseline here 6 failures, 19 errors) and report.
