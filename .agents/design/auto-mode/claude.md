Permission for the file write wasn't granted, so here's the full design — it still needs a `Write` approval to land at `.agents/design/auto-mode/claude.md` per the project's brainstorming convention; let me know if you want me to retry that.

---

## Key finding first

`exact_approval_bridge.py`/`meta_execution.py` are already a working exact-approval executor (prepare → approve → execute, with readback, ledger, kill switch), but today they're hardwired to **one fixed campaign entity** (`ACCOUNT`/`CAMPAIGN` constants in `meta_execution.py`) and aren't wired into `simple-web.html` at all. The owner's real pause proposals from `rule_engine.py`/`today_plan.py` are **ad-level**, and today the owner executes them by hand in Ads Manager and taps "ביצעתי" — `outcome_tracking.py` only observes afterward. So "use the existing exact-approval path" for live mode requires generalizing the bridge/executor from one fixed campaign to an arbitrary ad entity. That generalization, not the policy layer itself, is the largest piece of new execution-surface work here.

## Approach

Keep manual mode exactly as it is. Add a **standing policy** as a new object that sits *above* `ExactApprovalBridge`, not inside it: a signed envelope the owner approves once (allowed actions, daily caps, spend ceiling, expiry), checked on every automatic run before anything is prepared. Shadow mode runs the same eligibility/fence check but stops before `prepare()` and only records what would have happened. Live mode runs the identical path through to `prepare → approve → execute`, except the "approve" step is signed by the policy instead of a manual click.

## Policy fits the exact-approval model like this

- New table `policies` in a new `auto_policy.py`, same envelope pattern as `exact_approval_bridge.py` (HMAC-signed, `canonical()` JSON, one signing key).
- A policy record: `{allowed_actions:['pause_ad'], max_changes_per_day, max_budget_change_per_day:0, daily_spend_ceiling, rulebook_digest, mode:'shadow'|'live', created_at, expires_at, generation}`.
- `generation` is read from the **same control row** `ExactApprovalBridge` already maintains. `stop()` already bumps that generation and revokes every pending manual approval; a policy whose `generation` no longer matches is dead too — the existing kill switch becomes the policy kill switch for free, no new mechanism needed.
- Expiry: cap at 30 days like the brief's "approve once inside a fence," forcing re-approval; also invalidate if `rulebook_digest` (hash of `rulebook.py` params) changes, so a rulebook edit silently can't widen what a stale policy allows.
- A live auto-approval is NOT the owner's manual nonce. The ledger entry records `approved_by:'policy:<id>'` vs `approved_by:'owner'` so results/outcome views can always say which pause was automatic.

## Data flow

1. Owner opens the app → fresh report pulled, `rulebook` status checked (same gates `today_plan`/`rule_engine` already enforce: `fresh`, `rulebook available`, 3-day window complete).
2. `rule_engine.evaluate_report` runs as today; proposals with `kind=='pause_ad'`, `ready_for_approval=True`, and no warnings are **auto-eligible**.
3. `auto_policy.run(policy, eligible_proposals, counters)` checks: action in `allowed_actions`; changes-today < `max_changes_per_day`; projected spend impact < `daily_spend_ceiling`; policy not expired/revoked.
4. **Shadow**: write a `shadow_action` row (candidate before/after, reason, timestamp) next to the owner's real actions in `outcome_tracking.py`'s store — no call to `transport`, `loader`, or `MetaExecutor` at all. A unit test asserts zero transport calls in shadow mode.
5. **Live**: call the generalized bridge: `prepare(candidate, binding_for_this_ad)` → self-approve with the policy digest → `execute()` (unchanged readback/ledger/rollback path) → log into the existing results log.
6. Important constraint: the hard rule bans execution workers, so there is no background cron. Automatic runs (shadow or live) only fire inside a request the owner's browser makes (e.g. on refresh) — this needs the owner's explicit sign-off, since it means "automatic" is bounded by how often the app is open, not 24/7.

## Files touched

- New: `auto_policy.py` (policy envelope, fence checks, counters).
- `outcome_tracking.py`: add a `shadow` source alongside `owner_marked`/`auto_detected`, reusing its before/after comparison — no causal claims, same as today.
- `exact_approval_bridge.py`, `meta_execution.py`: generalize from the fixed `CAMPAIGN` constant to an ad-entity binding (new code path; existing fixed-campaign path stays untouched for backward compatibility with current tests).
- `rule_engine.py`/`today_plan.py`: add an `auto_eligible` boolean per decision; no change to the rules themselves.
- `server.py`: small additions — `/api/auto-mode/status`, `/policy` (create/revoke); flagged here per the shared-file rule.
- `simple-web.html`: small addition, flagged per the shared-file rule.

## UI (Hebrew, in `simple-web.html`)

- A settings row per action type: "השהיית מודעות מפסידות" with a 3-way control: "ידני" / "צל (ממליץ בלי לבצע)" / "אוטומטי (בתוך גבולות)". Moving to automatic opens a fence form (עד כמה שינויים ביום, תקרת הוצאה יומית) and a one-time "אני מאשר" click that signs the policy. Always-visible red "לכבות הכל" button calling the existing `stop()`.
- In the "היום" tab, a shadow pause renders like a real item but tagged "המערכת הייתה משהה את זו — מצב צל" with no "ביצעתי" button.
- In outcome/results cards: a badge distinguishing `בוצע ידנית` vs `בוצע אוטומטית לפי המדיניות שאישרת`.

## Risks and containment

- **Wrong entity written**: contain via the same `state_matches`/readback/digest checks `meta_execution.py` already does; policy adds no new write primitive.
- **Policy outlives owner's intent**: 30-day hard expiry + rulebook-digest invalidation + instant kill switch tied to the shared generation.
- **Runaway pausing**: `max_changes_per_day`/`daily_spend_ceiling` counted from the ledger (source of truth), not in-memory, so a restart can't reset the count.
- **Manual/automatic confusion**: every record carries `approved_by`; shadow and live never share a code path past step 3 above.
- **Scope creep to budget changes**: `allowed_actions` is a closed set checked against a hardcoded floor in code, not just the owner's saved policy, so a bug can't silently allow `set_budget` before it's explicitly built.

## Tests

- Policy create/expire/revoke, including generation-based revocation via `stop()`.
- Fence enforcement: each of max-changes, spend-ceiling, stale-data, unverified-rulebook individually blocks a would-be automatic action.
- Shadow mode: mock transport/loader, assert zero calls, assert a shadow record is written with correct before/after.
- Live mode: existing `meta_execution`/`exact_approval_bridge` test patterns, extended to a non-fixed entity binding; confirm manual proposals for the same entity are unaffected.
- Outcome/results view renders `approved_by` correctly for both sources.

## What to ask the owner

1. Is "automatic" acceptable as "runs only when the app is opened/refreshed" (no background worker), or is a scheduled run required later?
2. Default shadow length — 14 days?
3. Who sets the daily caps and spend ceiling: the owner types numbers, or does the system suggest defaults from the rulebook's own spend gate?
4. Confirm first scope is exactly ad-level pause only (no adset pause, no scale) — matches the brief, flagging explicitly before any code is written.
