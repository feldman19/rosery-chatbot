# Multi-business: map, config, onboarding (Claude, 2026-10-24)

Goal (owner): the system can later be sold to other shop owners. This round: map every ROSERY hard-code, thread a
business config through so ROSERY behaves exactly as before, and a no-live-call onboarding skeleton.
Branch `claude/multi-business` (from `claude/project-understanding-hg3b34` at a4138c5).

## 1. The map (what was hard-coded, where, and what happened to it)

Line numbers are from a4138c5, before the change. "Threaded" = now read from `business.ACTIVE`.

### Meta ad account `393059510164294` — 30 files
Threaded: `action_recommendations.py:16`, `business_context.py:16`, `campaign_operations_transport.py:19`,
`change_history_evidence.py:7`, `connections.py:22`, `creative_drift.py:18`, `decision_history.py:11`,
`execution_preflight.py:11`, `imported_business_method.py:10`, `management_report.py:18`, `meta_batch.py:14`,
`meta_direct.py:13`, `meta_execution.py:25`, `mobile_web.py:111`, `operations_config.py:11`, `owner_action_proposals.py:13`,
`owner_action_results.py:44` (Ads Manager link), `pause_budget_context.py:2`, `pause_evaluation.py:13`, `pilot.py:18`,
`registration.py:10`, `registration_records.py:30`, `reviews.py:17`, `rulebook.py:11`, `server.py:157,552,730,733,736`
(decision memory), `shopify_orders.py:34`, `visual_workspace.py:4`, `weekly_creative_context.py:9`.
Kept (ROSERY one-off history): `create_gift_test_20261019.py:27`, `verify_gift_test_20261019.py:20`.
Every other module imports `ACCOUNT` from these (`meta_direct`, `rulebook`, `management_report`, …), so it follows.

### Campaigns — 9 files
Two ROSERY campaigns, now two config fields:
- `meta_launch_campaign` = `120253309360290730` (the read report's `LAUNCH`, the registration case, `meta_execution.CAMPAIGN`):
  `meta_direct.py:14`, `meta_execution.py:26`, `registration.py:11`, `pilot.py:44,46`, `reviews.py:26` — threaded.
- `meta_sales_campaign` = `120246379886090730` (the write path's `PARENT`, `operations_config.CAMPAIGN`):
  `campaign_operations_transport.py:20`, `meta_batch.py:14`, `meta_direct.py:15` (`SALES_RESTART`), `operations_config.py:12` — threaded.
- Kept: `launch_proposal_schema.py:188` (a synthetic contract fixture).

### Meta app `1101281589359409` — `meta_direct.py:12`, threaded (`meta_app_id`).

### Shopify store `21cc82-3.myshopify.com` — 10 files
Threaded: `business_context.py:16`, `connections.py:8` (the source of `SHOP` for `shopify_direct`, `shopify_orders`,
`decisions`, `automation_service`, `live_workplan`), `economics_evidence.py:8,42`, `economics_observation.py:4`,
`management_report.py:18`, `order_economics.py:68`, `pilot.py:18`, `registration_records.py:29`, `reviews.py:17`.
Kept: `build_rosery_economics_profile.py:7` (one-off ROSERY build script).

### Tenant id `'rosery'` / `'rosery-local'` — 12 files
Threaded (`id`, `approval_tenant`): `action_recommendations.py:107`, `automation_service.py:28`, `business_context.py:16,18,21`
(also the profile path `businesses/rosery/profile.json` → `businesses/<id>/profile.json`), `exact_approval_bridge.py:68,72`,
`imported_business_method.py:22,27,63`, `pilot.py:15`, `registration.py:39,49,50,110`, `registration_records.py:28`,
`rulebook.py:16,19,23`, `server.py` (decision memory), `shopify_orders.py:33`. Kept: `build_registration_case.py:14`.

### Currency `'ILS'` — 22 files
Threaded (`currency`): `automation_service.py:25,26,28`, `campaign_operations.py:177,184,189,199,227,549`, `decision_history.py:22,38`,
`decision_loop.py:81`, `decisions.py:29,68`, `economics_evidence.py:10,31,42,60`, `economics_observation.py:15,16`,
`exact_approval_bridge.py:69,72,81,90`, `launch_workflow.py:101,114`, `management_report.py:389,390`, `meta_write_connection.py:41`,
`operations_config.py:43`, `pause_evaluation.py:83`, `pause_recommendation_adapter.py:29`, `registration.py:23`,
`registration_records.py:52`, `strategy_desk.py:105,162`.
Kept: `server.py:242` (the seeded sandbox entity), `sandbox_runner.py:30`, `strategy.py:187` (sandbox), `build_reviewed_launch.py:43`,
`build_rosery_economics_profile.py:7` (one-off scripts).

### Time zone `Asia/Jerusalem` — 3 files
Threaded: `true_profit.py:18` (`STORE_TZ`), `outcome_tracking.py:480`. Kept: `evidence/__init__.py:46` (a general allow-list
next to `UTC`; any valid zone is accepted after it).

### Rulebook — 6 files
`rulebook.py:12,22,46`: folder, file name and `source_ref` now come from `rules_dir` / `rulebook_file`; a new business has its
own rules folder inside its data folder. `account_history.py:254`, `copy_variants.py:59,132`, `creative_drift.py:123`,
`launch_proposal_schema.py:95`, `launch_strategy.py:40` only call `load_rulebook()` and follow it.

### Brand strings given to models — 3 files
Threaded: `copy_variants.py:72` and `server.py:773` (`name`, `description`).
Not yet: `creative_vision.py:166` ("ROSERY clothing and jewelry", the vision prompt).

### Data folder layout — 13 files
One business = one data folder. `server.py:151` defaults to `campaigner/.local` (ROSERY's original layout, no `business.json`);
`make_server` now refuses a folder whose `business.json` names another business. `run_owner.py` keeps its own settings in
`campaigner/.local`. The rest are one-off scripts with their own folders (`audit_*`, `build_*`, `c11_canary.py`,
`run_approved_c11_postpublish.py`, `serve_isolated_preparation_proof.py`, `mobile_*`).

### Still ROSERY-specific after this round (phase 2)
- Owner-facing Hebrew that names ROSERY or assumes its catalogue: `simple-web.html` (owned by another session), `creative_vision.py:166`,
  `creative_rules_check.py:85` ("לשון זכר" is written in the check text; the rule itself now states the business's form),
  `strategy_desk.py:105` message text, `business_context.py` error text "תואם לרוסרי".
- The registration case (`registration.py:50`: site `roseryjewelry.com`, Flashy list `40428`; `flashy_direct.py:14` account `11126`):
  a ROSERY launch campaign integration, not general.
- `load_rosery_dossier()` keeps its name (many callers); it now loads `businesses/<id>/profile.json`.
- A new business's profile is written to its data folder (`profile.json`); `business_context` still reads `campaigner/businesses/<id>/`.
  Phase 2: read the profile from the data folder.
- Real connectors for onboarding (read-only Meta accounts/campaigns, Shopify identity) on the business's computer.

## 2. Business config (`campaigner/business.py`)
- `Business` (frozen): `id, name, description, meta_account, meta_app_id, meta_sales_campaign, meta_launch_campaign, shop,
  currency, timezone, language, country, rules_dir, rulebook_file, approval_tenant`.
- `ROSERY` is built in with today's exact values. `ACTIVE = active()`: the file named by `CAMPAIGNER_BUSINESS_FILE`, else ROSERY.
- One business per process, chosen at start, before modules read their constants (module constants keep their names, so
  every import and test patch works as before).
- `validate()` checks every field (digits-only ids, `xxx.myshopify.com`, ISO currency, a real IANA zone, `he`/`en`, a rules folder
  inside the business folder) and explains a wrong field in Hebrew.
- `check_data_folder()`: a folder with another business's `business.json` is refused; a folder without one is ROSERY's original layout.
- Tests (`test_business.py`): ROSERY's values equal the old literals; every threaded module constant still has ROSERY's value;
  the rulebook still loads; a source scan finds no ROSERY literal outside `business.py` and the listed one-off/sandbox files;
  a process started for another business uses its ids and an empty rules folder fails closed; the server refuses a foreign folder.

## 3. Onboarding (`onboarding.py`, `onboarding_server.py`, `onboarding.html` — all new)

### The flow, as the new owner sees it
1. **פרטי העסק** — שם העסק, מה הוא מוכר במשפט אחד, מטבע, שעון, שפת המסך.
2. **חיבור לפייסבוק ואינסטגרם (מטא)** — החיבור נעשה במחשב של העסק, במסך החיבורים הקיים. המערכת רק קוראת.
3. **בחירת חשבון המודעות** — מתוך מה שהחיבור רואה. אם המטבע של החשבון שונה ממטבע העסק, עוצרים ומסבירים (אין המרת מטבע).
4. **בחירת קמפיין המכירות** — או "אין עדיין".
5. **חיבור החנות (שופיפיי)** — כתובת `name.myshopify.com`, נבדקת מול החיבור; או "אין לי חנות שופיפיי".
6. **כמה שאלות על העסק** — הזמנה ממוצעת, רווח גולמי באחוזים, איך פונים ללקוח (זכר / נקבה / רבים), עובדות חנות שמותר לכתוב
   (מילה במילה), משלוח חינם מעל כמה, כמה ימים להחזרה, מה אסור לכתוב אף פעם.
7. **בדיקה אחרונה לפני שמתחילים** — המערכת מראה איך כל מספר חושב ("כל ₪1 צריך להחזיר לפחות 1.67 כדי לא להפסיד, כי הרווח
   הגולמי 60%"), מציעה ספים, וכל אחד מהם אפשר לשנות. כל הכללים מוצגים במילים פשוטות.
8. **אישור** — נכתבים רק בתיקיית העסק החדש: `business.json`, תיקיית `rules/` משלו (ארבעה קבצי מקור + ספר כללים חתום באותו פורמט
   ש-`rulebook.py` בודק) ו-`profile.json`.

### Decisions
- **Same rule ids as ROSERY.** Modules read rules by id (`param`, `cite`, `rules['rescue_signals']`, the creative checks), so a new
  rulebook has every id ROSERY's has (a test compares the sets), with generic Hebrew and the business's numbers. `masculine_copy`
  keeps its id for compatibility; its sentence and `params.form` state the chosen form. `owner_forbidden` is added when the owner
  lists things never to write.
- **Proposed thresholds follow ROSERY's proportions** to its own economics: stop below 1.84 × the break-even return (1/margin),
  hold up to 1.30 × that, winner above 1.61 ×; spend amounts follow the order value (₪200 of a ₪315 order). With ROSERY's numbers
  this gives back ROSERY's 2.3 / 3.0 / 3.7 / 2.6 / ₪200 / ₪35 / ₪150 / ₪300 (tested). They are shown as proposals with the
  arithmetic, never as facts, and every one can be changed before approval; they must rise (stop < hold < winner).
- **No live calls.** Connections are an injected `connectors` object; the default `LocalOnlyConnectors` connects nothing and every
  step that needs a connection answers "צריך לחבר במחשב של העסק". The real connectors (read-only: ad accounts, campaigns, the
  shop's domain and currency) are written on the business's computer against the existing read paths.
- **Never over an existing business.** Onboarding refuses a non-empty folder that has no `onboarding.json` (ROSERY's folder).
- **A separate local server** (`onboarding_server.py`, 127.0.0.1, session header + same-origin like `server.py`), so `server.py` and
  `simple-web.html` are untouched. Run: `python -B onboarding_server.py --data-dir <new empty folder>`.
- Tests (`test_onboarding.py`): order of steps, Hebrew refusals, currency mismatch, not-connected, review arithmetic and overrides,
  ROSERY proportions, approval writes only inside the new folder and the real `load_rulebook` accepts the result in a process started
  for that business, ROSERY's `rules/` byte-for-byte unchanged, the whole flow over HTTP with session/origin checks, the screen's script parses.
