# Evidence before recommendations

This layer supplies four explicit stages: historical evidence, current evidence,
recommendation requirements, and linked decision/outcome observations. It does not
train model weights, execute ads, prove causality or supply a model with image pixels.

`DecisionMemory` is reusable across business/account pairs. A Meta account can be
bound to one business. All reads and journal records are scoped to that binding.
The current manager HTTP host still binds ROSERY only; a multi-business product,
identity/authorization system and business onboarding are not implemented here.

Historical snapshots are append-only, content-addressed, and checked on retrieval.
Imports preserve missing metrics, explicit coverage limits and provenance. The
ROSERY importer checks the existing source hashes, image hashes, pagination checks,
unique ad identities and purchase/value/spend reconciliation. It replaces control
characters in display labels with spaces, records the count, and retains source
bytes through the original export; source verification is not a new live API read.

Retrieval includes the selected ad, lexical matches, volume, observed efficiency,
lower efficiency and missing-data examples. This is a bounded evidence sample,
not semantic understanding or proof that audiences, time windows, offers or attribution
are comparable. Small-sample high ROAS stays explicitly labeled. No arbitrary
purchase threshold establishes a winner or an approved economic target.

Current data comes from the existing LiveReports snapshot. Foreign, partial,
restored, expired, failed or malformed sources cannot be labeled fresh. Reported
purchases/value remain null if fields are missing. A current snapshot is not a
conversion integrity audit, profitability or a causal explanation.

Owner-local routes retain the existing session/origin checks:

- GET `/api/decision-memory`: four-stage evidence projection.
- POST `/api/decision-memory/query`: exact `question`, `ad_id` fields; no model call.
- POST `/api/decision-memory/decision`: local declared hypothesis, evidence_refs,
  action, evaluation_plan. No approval or external execution.
- POST `/api/decision-memory/observation`: decision_id, evidence_refs, design, note.
  Uses the existing append-only LearningLedger; no automatic causal promotion.

The owner UI exposes a "מהעבר עד לתוצאה" panel. The existing Claude advisory chat,
its provider, its sharing scope and execution approval gates are unchanged. Full
performance history is not automatically sent to Anthropic. No new credentials,
provider, refresh scheduler or campaign mutation is introduced.

Remaining acceptance: connect the bounded evidence bundle to the authorized model
route, evaluate answers on held-out real periods/cases, independently review
source-grounded conclusions, attach verified execution receipts and collect linked
real outcomes. Offline safety tests and importing real records do not satisfy this.
Observed improvements must remain separate from evidence of causal business impact.
