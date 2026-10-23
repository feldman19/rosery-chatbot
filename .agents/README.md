# ROSERY — rules for the two agents (Claude Code + Codex)

Both agents build this project in parallel and review each other. These rules are given to every
agent run by `tools/agent_bridge.py`. The owner talks Hebrew: any text meant for the owner is Hebrew.

## How we split work
- Each agent works on its own branch in its own git worktree (`codex/<task>`, `claude/<task>`).
  Never edit files in the other agent's worktree. Never push; the owner decides what is pushed.
- `.agents/tasks.md` says who owns which task and which files it may touch. Touch only your files.
  If you must change a shared file (`server.py`, `simple-web.html`), keep the change small and say so in your report.
- After a commit, the other agent reviews it read-only. Reviews go to `.agents/reviews/<sha>-by-<agent>.md`.
  The author answers in `.agents/messages/<reviewer>/` and fixes on its own branch.
- Brainstorming: for a big task each agent writes its own design to `.agents/design/<task>/<agent>.md`
  without reading the other's first; then one joint decision is written to `.agents/design/<task>/decision.md`.

## Hard rules (from START-HERE.md and the owner)
- No writes to Meta or Shopify. Do not run the server against the live data folder
  (`Documents\Codex\...\campaigner\.local`), do not enable execution workers, do not read or print credentials
  (`*.dpapi`, tokens, `auth.json`).
- Missing data is unknown, never zero. No causal claims from before/after comparisons.
- Every live campaign change needs the owner's exact approval through the existing approval flow.
- Private business files (`campaigner/businesses/rosery/profile.json`, `outputs/*.json` evidence) stay local; never commit them.
- Match the surrounding code style. Add or update tests for what you change; run
  `python -B -m unittest discover -s . -p "test_*.py"` from `campaigner/`. Known baseline: 6 failures and
  19 errors from files not included in the export — report any change from that baseline.
- Windows checkout: `campaigner/rules/*` must stay LF (`.gitattributes`), or the rulebook disables itself.
