# Task: make the test suite green
Run the full suite. The owner's PC shows ~6 failures / 19 errors that predate current work; a sandbox shows more (8/30) because of missing local files (data folder, tokens, Windows paths, SQLite handle locking). For each one classify: (a) needs a local file or credential → fixture or `skipUnless` with a clear reason, never a fake pass; (b) real bug → minimal code fix; (c) stale expectation → update only if current behaviour is clearly intended (cite the commit). Close every SQLite connection in tests (`contextlib.closing`). Test-only changes around `simple-web.html` only if unavoidable.
Done = suite passes with only documented skips; a table (plain Hebrew + test names) of every test touched, cause class and fix, at `.agents/messages/claude/<date>-test-cleanup-report.md`.

## Start
`git fetch origin claude/project-understanding-hg3b34 && git checkout -b claude/test-cleanup origin/claude/project-understanding-hg3b34`, and push all work to `claude/test-cleanup`.

## Context
ROSERY is an Israeli jewelry/apparel brand. This repo (`feldman19/rosery-chatbot`) holds a Python stdlib server (`campaigner/server.py`, ~1,900 unittest tests) that reads Meta Ads and Shopify, recommends actions under a hash-bound owner rulebook, and executes Meta changes only after the owner approves each one in-app. The owner talks Hebrew only and is not technical: every report and owner-facing text in plain Hebrew, no ad jargon. Several other sessions change UI and data modules in parallel on their own branches.

## Hard constraints
Never call Meta or Shopify APIs (no tokens in the cloud). Never read or print credentials. Never commit private business data. Never edit `campaigner/rules/*` (hash-bound, LF). Do not change `campaigner/simple-web.html` (another session owns it). Do not merge into other branches. Tests: `cd campaigner && python -B -m unittest discover -p "test_*.py"` — run touched/new tests while working, the full suite once at the end.
