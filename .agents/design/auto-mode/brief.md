# Brief: automatic mode next to manual mode (design only)

The owner wants to choose, per action type, between **manual** (today: every change waits for his exact approval)
and **automatic inside fences** (he approves a policy once; the system acts on its own inside it).

What the owner asked for:
- The end user gets both options and can switch any time.
- Start narrow: the first automatic action is pausing ads that clearly lose money by the owner's rulebook
  (`campaigner/rules/`, `rule_engine.py`, `today_plan.py`). Budget +20% only later.
- Fences: allowed actions, max changes per day, max budget change per day, a total daily spend ceiling,
  only when data is fresh and the rulebook is verified, and an instant kill switch.
- **Shadow mode first:** for N days the system only records what it would have done, next to what the owner
  actually did (`outcome_tracking.py`), so the owner can judge it before turning live mode on.
- Live mode later uses the existing exact-approval execution path (`exact_approval_bridge.py`,
  `campaign_operations.py`, `meta_execution.py`, `execution_preflight.py`): read-back after every action,
  drift detection (`creative_drift.py`), rollback, and an entry in the results log.
- A Meta write credential is configured on this machine, so live mode would change the real account.

Constraints: this is a design. Nothing in this work may write to Meta. The owner decides when live mode starts.

Write: approach; how a standing policy approval fits the existing exact-approval model (digest, expiry,
revocation); data flow; files touched; UI in `simple-web.html` (Hebrew); risks and how each is contained;
tests; what to ask the owner. Keep it under ~900 words.
