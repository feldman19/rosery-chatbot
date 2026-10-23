# Task board

| Task | Owner | Branch | Files it may touch | State |
|---|---|---|---|---|
| account-history (stages 1–3) | claude | claude/project-understanding-hg3b34 | account_history.py, meta_direct.py, claude_chat.py | done (527fdef) |
| agent-bridge | claude | claude/project-understanding-hg3b34 | tools/agent_bridge.py, .agents/* | done (638c75c) |
| auto-mode-shadow | codex | codex/auto-mode-shadow | new automation_policy.py, automation_shadow.py, their tests; small marked sections in server.py and simple-web.html | building — see .agents/design/auto-mode/decision.md |
| true-profit (read-only) | claude | claude/project-understanding-hg3b34 | new true_profit.py, profit_attribution.py, their tests; shopify_direct.py, shopify_orders.py, order_economics.py; small marked sections in server.py and simple-web.html | building — see .agents/design/true-profit/decision.md |

Shared files (`server.py`, `simple-web.html`): each agent adds its own clearly marked block; Claude merges `codex/auto-mode-shadow` after reviewing it.
