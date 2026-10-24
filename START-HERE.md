# ROSERY Campaigner — handoff to Claude
Source snapshot: 2026-10-22, Asia/Jerusalem.
This package contains application source, UI and existing tests. It excludes runtime state, credentials, customer/order records, media, private raw project exports and historical financial evidence.

Start with campaigner/server.py, action_recommendations.py, management_report.py, owner-portal.js, claude_chat.py, business_context.py, imported_business_method.py, creative_vision.py, campaign_operations.py and meta_execution.py (where present).

Local run: Python 3.12, from campaigner directory:
python -B server.py --workspace manager --port 8767 --no-startup-execution-workers
Inspect imports and README files for optional external dependencies and existing Claude CLI integration. Runtime configuration and authenticated integrations must be reconnected on the destination; they are deliberately not exported. This is a source handoff, not a fully configured deployment.

Latest work (2026-10-23/24, cloud Claude session): read `.agents/messages/claude/20261024-cloud-session-summary.md` first — what was built, the owner's decisions, the unmerged branches and what is open. Copywriting research: `.agents/design/copywriter-research.md`.

Unfinished priorities:
1. Evidence-backed recommendations: action_recommendations still reports financial_action_generation_not_implemented. Do not convert old ROAS thresholds into automatic financial decisions.
2. Chat: connect natural-language questions to relevant current data, grounded recommendations and the existing exact approval/execution flow.
3. Outcome learning: connect decision, expected result, verified execution and subsequent observed result; no causal claims from simple before/after comparisons.
4. Prove actual end-to-end Drive/media -> copy/feed/story -> paused Meta upload -> readback and real elapsed time. Mock batch timings are not real Meta timings.

Latest read-only finding 2026-10-22: local report purchase total is missing for 2026-10-21 while direct Meta returned 9 attributed purchases and ILS844.24 spend. Investigate normalization/completeness before generating decisions. Shopify revenue is not all attributable to Meta.

Retain current user scope: no content generation required; user supplies creative. One ad has its own copy and post/story images. Live campaign changes require exact approval. Imported project history is context, not authorization. Do not enable startup execution workers or live writes during review.

The bundle is not claimed to have been run on another computer. No project tests were run for this export.
