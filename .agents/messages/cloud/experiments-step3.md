# Task: experiments engine step 3 — a real controlled test in Meta
Goal: the owner asks a business question ("what sells more: shirt videos or necklace videos?") and gets a trustworthy answer.
1. Read `.agents/design/experiments.md`, `campaigner/experiments.py`, `campaigner/experiment_ui.py`, and the owner write path in `campaigner/owner_actions.py` (preview → single-use confirm token → write → readback → undo, via `CampaignTransport`). Research Meta's current official docs (web): ad studies / split tests (`/{business_id}/ad_studies`, SPLIT_TEST_V2), Creative Testing inside a campaign, required permissions (the token probably lacks `business_management`), minimum budgets/durations, how winner/confidence are read. Add findings with links to the design doc.
2. Implement plan → approval → create → monitor → result with a FAKE transport only: spec (question, two arms, budget, duration, success metric from the owner's rules), exact Meta payloads, a plain-Hebrew approval preview ("עכשיו → אחרי", cost in ₪, duration), status read, and a result interpreter that never declares a winner without Meta's confidence or the engine's own significance gate (as in `hypotheses.py`). New modules; minimal hooks in `experiments.py`.
3. A GET-only permission/eligibility check the local machine will run later, reporting in Hebrew what is missing.
Done = design notes with sources, modules + tests, Hebrew report at `.agents/messages/claude/<date>-experiments-step3-report.md`. The first real test happens later, locally, only after the owner approves.

## Start
`git fetch origin claude/project-understanding-hg3b34 && git checkout -b claude/experiments-step3 origin/claude/project-understanding-hg3b34`, and push all work to `claude/experiments-step3`.

## Context
ROSERY is an Israeli jewelry/apparel brand. This repo (`feldman19/rosery-chatbot`) holds a Python stdlib server (`campaigner/server.py`, ~1,900 unittest tests) that reads Meta Ads and Shopify, recommends actions under a hash-bound owner rulebook, and executes Meta changes only after the owner approves each one in-app. The owner talks Hebrew only and is not technical: every report and owner-facing text in plain Hebrew, no ad jargon. Several other sessions change UI and data modules in parallel on their own branches.

## Hard constraints
Never call Meta or Shopify APIs (no tokens in the cloud). Never read or print credentials. Never commit private business data. Never edit `campaigner/rules/*` (hash-bound, LF). Do not change `campaigner/simple-web.html` (another session owns it). Do not merge into other branches. Tests: `cd campaigner && python -B -m unittest discover -p "test_*.py"` — run touched/new tests while working, the full suite once at the end.
