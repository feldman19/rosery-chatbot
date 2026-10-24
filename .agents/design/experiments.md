# Experiment engine (design, Claude, 2026-10-23)

Goal (owner): an engine that learns, per business, what works and why, through tests run on the ad account,
without concluding from noise; the accumulated, proven per-business knowledge is the moat.

## What an experiment is
- One question, one variable: "do customer-review videos sell more per ₪ than product videos?" Everything
  else equal: same campaign, same copied targeting (gender rule), same daily budget per arm, same dates.
- Arms A and B (two at first). Each arm = one new PAUSED ad set with 3–5 ads of its kind (rule 5–8 ads per
  ad set is respected by pairing arms with enough creatives; the engine refuses a test it cannot fill).
- Primary metric: purchases per ₪1,000 spend and revenue per ₪ (Meta attribution, view-through included,
  as everywhere else). Secondary: cost per checkout and per cart (to read early direction only).
- Budget and length set before start: e.g. ₪100/day per arm, 10–21 days; total cost shown up front.

## Clean comparison
1. Prefer Meta's own A/B test (split test / ad study) so audiences do not overlap and Meta randomizes.
   Verify the endpoint and fields against Meta's documentation before building; if the account or API cannot
   create it, fall back to two ad sets and say on screen that audiences may overlap (weaker evidence).
2. Same start day for both arms, whole weeks (Friday/Saturday in each arm), holiday windows flagged.
3. No mid-test edits. A change to either arm ends the test as "interrupted".

## When it decides (evidence gate, same spirit as the journal)
- Model purchases per ₪ as a Poisson rate per arm with a weak Gamma prior; compute P(B better than A) and the
  likely lift range (90% interval).
- Verdict only when each arm has ≥ ₪600 spend and ≥ 5 purchases, AND P ≥ 0.95 (or ≤ 0.05): "B better" / "A
  better". If the max length is reached without that: "לא הוכרע" with the interval, never a winner.
- Early stop only for harm: an arm with ≥ ₪400 and 0 checkouts while the other has ≥ 3 purchases.
- Results go to the hypothesis journal as evidence (one experiment = one case), and to the per-business
  "מה עובד אצלך" page, with dates, cost, interval and caveats.

## Where ideas come from
- Open hypotheses in the journal ("testing"/inconclusive), account-history trait groups that look different
  but lack data (e.g. testimonial vs product), the owner's own ideas typed in plain Hebrew, and outside
  research (context only). The engine ranks by: expected value, cost to learn, and whether creatives exist.
- At most one running experiment per account at first (budget sanity, cleaner reads).

## Owner flow (plain Hebrew, two approvals)
1. Card "ניסוי מוצע": the question, why it matters, arms, cost (₪/day and total), length, what we will
   learn, what it will NOT prove.
2. "אשר והכן במצב מושהה" → the existing PAUSED write path builds both arms (manifest + digest approval).
3. "הפעל ניסוי" → a separate exact approval activates both arms together (same minute).
4. Daily status line: spend per arm, purchases, current P, days left; "עצור ניסוי" any time.
5. Result card with the verdict and one suggested next step (e.g. "להעביר את התקציב לסוג B").

## Later (with onboarding of more businesses)
- Cross-business priors: results of similar businesses (category, price, audience) become the starting prior
  for a new business, then its own data takes over. Anonymized, opt-in, never one business's data shown to
  another. This network effect is the strongest moat.

## Build order
1. `experiments.py`: model (plan, arms, state machine planned→prepared→running→decided/interrupted),
   Poisson–Gamma decision, ranking of candidates; pure, fully tested with synthetic data.
2. Server routes + card UI (after chat threads land, to avoid conflicts in server.py/simple-web.html).
3. Meta split-test creation through the PAUSED write path, after reading Meta's docs; live canary only with
   the owner's explicit approval.

## Meta split test: what the docs say (checked 2026-10-23)
Sources: developers.facebook.com/docs/marketing-api/guides/split-testing/ and /reference/ad-study/.
- Created with `POST /{business_id}/ad_studies` (also `/{user_id}/ad_studies`); an `act_…/ad_studies`
  path is not documented. Fields: `name`, `start_time`, `end_time` (Unix seconds), `type=SPLIT_TEST`,
  `cells` [{`name`, `treatment_percentage` ≥ 10, sum ≤ 100, `adsets`: […]}]. Meta divides the audience with
  no overlap. After start: no change to start/percentages, no removal of ad sets; `end_time` can be extended.
- Not stated: whether both ad sets may sit in one campaign, duration limits, the exact scope, CBO/Advantage+
  support, and the `results` shape for SPLIT_TEST. Our write token has only `ads_read` + `ads_management`;
  a business-level endpoint probably needs `business_management` too, which the owner would have to grant.
- So step 3 starts with a read-only probe and one live canary only with the owner's approval; until then the
  fallback (two ad sets, overlap possible, said on screen) is the safe default.

## Step 3 (2026-10-24): a controlled test in Meta — research and build

### How the research was done (read 2026-10-24)
developers.facebook.com and facebook.com are blocked from the cloud sandbox (proxy refuses the connection), so the
pages could not be opened directly. What was used instead, strongest first:
1. **Meta's official Python Business SDK, v26.0.2 (API v26.0)**, which is generated from the Graph API spec:
   `adobjects/adstudy.py`, `adstudycell.py`, `adstudyobjective.py`, `business.py`, `user.py`, `adaccount.py`,
   `adset.py`, `campaign.py` — https://github.com/facebook/facebook-python-business-sdk/tree/main/facebook_business/adobjects
2. **Search-engine extracts of Meta's developer pages** (wording is the search engine's summary, not a verbatim copy):
   - Split testing guide: https://developers.facebook.com/docs/marketing-api/guides/split-testing (also
     https://developers.facebook.com/documentation/ads-commerce/marketing-api/guides/split-testing)
   - Ad Study reference: https://developers.facebook.com/docs/marketing-api/reference/ad-study
   - Business ad_studies edge: https://developers.secure.facebook.com/docs/marketing-api/reference/business/ad_studies
   - Old v2.8 guide (limits): https://developers.facebook.com/docs/marketing-api/guides/split-testing/v2.8
   - Permissions: https://developers.facebook.com/docs/permissions and
     https://developers.facebook.com/documentation/development/create-an-app/marketing-api-use-cases.md
3. Third-party write-ups (context only, never proof): Jon Loomer on Creative Testing
   (https://www.jonloomer.com/meta-creative-testing/), Seer Interactive on A/B duration
   (https://www.seerinteractive.com/blog/beginners-guide-facebook-ab-testing/), and one open-source PR that builds the
   same call (https://github.com/ElAmir-Mansour/instagram-dm-automation/pull/248).
The local machine should open the four Meta pages once before the first live test and fix anything below that differs.

### What is confirmed
- **Create**: `POST /{business_id}/ad_studies` (SDK `Business.create_ad_study`; also `User.create_ad_study`). The ad
  account, campaign and ad set have **GET** `ad_studies` only — there is no account-level create. (SDK, guide)
- **Create parameters** (SDK param types): `name`, `description`, `type`, `start_time` and `end_time` (int, Unix
  seconds), `cells` (list<Object>), `objectives`, `confidence_level` (float), `creative_test_config` (map),
  `observation_end_time`, `cooldown_start_time`, `client_business`, `viewers`.
- **Type enum in the SDK**: BACKEND_AB_TESTING, CONTINUOUS_LIFT_CONFIG, CREATIVE_SPEND_ENFORCEMENT, GEO_LIFT, LIFT,
  PORTFOLIO_OPTIMIZER, **SPLIT_TEST**, VERSION_CONTROL.
- **Cell (read) fields**: `id`, `name`, `treatment_percentage`, `control_percentage`, `ad_entities_count`, `ad_ids`.
- **Study (read) fields**: `start_time`, `end_time`, `canceled_time`, `results_first_available_date`,
  `confidence_level`, `objectives`, `cells`, `type`, … ; edges `/cells`, `/objectives`, `/instances`, `/checkpoint`;
  a study can be updated (POST) and deleted (DELETE).
- **Objectives** carry `results` and `last_updated_results`, type enum incl. SALES / CONVERSIONS.
- **Guide (extract)**: Meta divides the audience so the groups do not overlap; one variable per test; ad set cells
  with `treatment_percentage` 50/50 is the documented example; the same with campaigns. Earlier read (2026-10-23):
  each cell ≥ 10%, sum ≤ 100; after start no change to start time or percentages; `end_time` can be extended.
- **Creative test (extract)**: "Creative tests require `SPLIT_TEST_V2` and a `creative_test_config`" with either
  `daily_budget` or `lifetime_budget_percentage`, 2–5 cells.
- `confidence_level` is "used in power calculations and final study report" (extract of the Ad Study reference).

### What is NOT verified (kept in one constant, `META` in `campaigner/meta_experiment.py`)
- The key that puts ad sets into a cell at creation (`adsets`) is from the guide's example only; the SDK types
  `cells` as list<Object> with no inner schema.
- `SPLIT_TEST_V2` is not in the v26 SDK enum, and its cell shape (ads per cell?) is not documented in what we read.
  → Not built. It would also clash with the owner's rules: one ad per cell vs 5–8 ads per ad set
  (`ads_per_adset`), and a test-level budget vs separate ad set budgets (`no_asc_cbo`).
- **Permissions**: not stated for `ad_studies`. `business_management` is "read and write with the Business Manager
  API", and the create call is on the business node, so it is probably needed. Our write token has only
  `ads_read` + `ads_management`, and `meta_write_connection.verify` **rejects a token with any extra scope**. Adding
  `business_management` is an owner decision plus a small code change there. `/{user_id}/ad_studies` might avoid it
  — unverified.
- **Limits**: max 30 days and "at least 7 recommended" come from third-party guides only (`META_MAX_DAYS = 30`).
  Old v2.8 guide: 100 concurrent studies per advertiser, 150 cells, 100 ad entities per cell. No official minimum
  budget found.
- **Winner/confidence**: the shape of `objectives[].results` for a split test is unknown. Third-party guides
  disagree on Meta's own winner bar (65% to 90%). So `meta_verdict()` returns None until a live readback shows
  the shape, and a Meta verdict counts only at ≥ 95% and with the engine's minimum sample; otherwise the engine's
  Poisson–Gamma gate decides. A disagreement between the two is "לא הוכרע".
- Whether Meta accepts two ad sets in the same campaign for SPLIT_TEST (likely yes, unverified).
- Datetime readback format (`2026-11-01T00:00:00+0200`) is assumed from Graph conventions.

### What was built (fake transport only; nothing calls Meta in the cloud)
- `meta_experiment.py` (pure): `build_spec` checks the plan against the rulebook (5–8 ads per version, ≥ 7 days,
  whole weeks, ≤ 30 days, enough budget to reach ₪600 per version; warns when budget is below 5 × breakeven CPA),
  `study_payload` (exact body), `approval_preview` (plain Hebrew: now → after, ₪ per day and total, dates),
  `daily_rows` (insights → daily rows, unknown stays unknown), `interpret` (answer + next step).
- `meta_experiment_actions.py`: `ExperimentActions` with the owner_actions pattern — preview → single-use token
  (10 min) → re-read → write → readback → receipt; steps `create` (study over two PAUSED ad sets, no spend),
  `activate` (both on together, separate approval), `stop` (pause both), `cancel` (delete before start = undo of
  create); a partial activation is reported, never retried. `status()` is GET only. `MetaStudyTransport` is the
  real adapter (fixed paths); not exercised.
- `meta_experiment_check.py`: GET-only readiness check with a path allowlist; Hebrew report of what is missing.
- `experiments.py` hooks: `Plan.meta_study_id`, and `prepared → interrupted` (cancel before start).
- Not yet wired into `server.py` / the screen (owned by other sessions); routes come after the first live canary.

### First live test (local, only after the owner approves)
1. Run `python meta_experiment_check.py --data-dir <.local>` and read the Hebrew report.
2. Owner decides on `business_management`; if yes, widen the scope check in `meta_write_connection.py`.
3. Prepare two PAUSED ad sets through the existing PAUSED write path; then "create" → check the readback by hand in
   Ads Manager (Experiments) → fix any field in `META` → "activate" with the owner's separate approval.
