# Task board

| Task | Owner | Branch | Files it may touch | State |
|---|---|---|---|---|
| account-history (stages 1–3) | claude | claude/project-understanding-hg3b34 | account_history.py, meta_direct.py, claude_chat.py | done (527fdef) |
| agent-bridge | claude | claude/project-understanding-hg3b34 | tools/agent_bridge.py, .agents/* | done (638c75c) |
| auto-mode-shadow | codex | codex/auto-mode-shadow | new automation_policy.py, automation_shadow.py, their tests; small marked sections in server.py and simple-web.html | building — see .agents/design/auto-mode/decision.md |
| true-profit (read-only) | claude | claude/project-understanding-hg3b34 | new true_profit.py, profit_attribution.py, their tests; shopify_direct.py, shopify_orders.py, order_economics.py; small marked sections in server.py and simple-web.html | building — see .agents/design/true-profit/decision.md |

| owner-auto-update | claude | claude/project-understanding-hg3b34 | run_owner.py, START-ROSERY.bat, update routes in server.py | done (0eb5c23, 6162128) |
| home-day-picker + accessibility | claude | claude/project-understanding-hg3b34 | management_report._daily, simple-web.html home | done (396cc91) |
| copy brief + customer read | claude | claude/project-understanding-hg3b34 | copy_variants.py, creative_launch.py, account_history.voice_examples | done (ac9319c) |
| multi-business | claude | claude/multi-business | business.py, onboarding*.py/html; threaded constants | done, not merged (79b2e78) — see .agents/design/multi-business.md |
| experiments-step3 | claude | claude/experiments-step3 | meta_experiment*.py; hook in experiments.py | done, not merged (ae97fbd) |
| copywriter: brand book + owner-edit memory | — | — | see .agents/design/copywriter-research.md | proposed next |

Shared files (`server.py`, `simple-web.html`): each agent adds its own clearly marked block; Claude merges `codex/auto-mode-shadow` after reviewing it.
