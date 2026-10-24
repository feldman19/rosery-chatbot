# Task: support more than one business
Goal: the system can later be sold to other shop owners.
1. Map every place that hard-codes ROSERY: ad account / campaign ids (`ACCOUNT`, `PARENT`, `LAUNCH` in `campaigner/*transport*.py`, `meta_direct.py`, `creative_drift.py`), the Shopify store, the rulebook path, store facts, Hebrew brand strings, currency/timezone, data-folder layout. Write the map to `.agents/design/multi-business.md`.
2. A business config (`campaigner/business.py` + one `business.json` per business in the data folder): ids, store, currency, timezone, language, rulebook path. Thread it through so ROSERY's behaviour is EXACTLY unchanged with ROSERY's config (test that it reproduces today's constants). A new business gets its own rules folder.
3. Onboarding design + skeleton, no live calls: connect Meta, pick account and campaign, connect Shopify, a short business questionnaire that seeds the rulebook and the business profile, review defaults. Plain-Hebrew flow in the design doc; backend endpoints stubbed and tested with fakes; any screen in a NEW file.
Done = design doc, config threaded with ROSERY unchanged, onboarding skeleton + tests, Hebrew report at `.agents/messages/claude/<date>-multi-business-report.md`.

## Start
`git fetch origin claude/project-understanding-hg3b34 && git checkout -b claude/multi-business origin/claude/project-understanding-hg3b34`, and push all work to `claude/multi-business`.

## Context
ROSERY is an Israeli jewelry/apparel brand. This repo (`feldman19/rosery-chatbot`) holds a Python stdlib server (`campaigner/server.py`, ~1,900 unittest tests) that reads Meta Ads and Shopify, recommends actions under a hash-bound owner rulebook, and executes Meta changes only after the owner approves each one in-app. The owner talks Hebrew only and is not technical: every report and owner-facing text in plain Hebrew, no ad jargon. Several other sessions change UI and data modules in parallel on their own branches.

## Hard constraints
Never call Meta or Shopify APIs (no tokens in the cloud). Never read or print credentials. Never commit private business data. Never edit `campaigner/rules/*` (hash-bound, LF). Do not change `campaigner/simple-web.html` (another session owns it). Do not merge into other branches. Tests: `cd campaigner && python -B -m unittest discover -p "test_*.py"` — run touched/new tests while working, the full suite once at the end.
