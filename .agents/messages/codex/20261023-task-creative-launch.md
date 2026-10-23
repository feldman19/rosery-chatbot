Build your part of `.agents/design/creative-launch/decision.md` (read it, plus `brief.md`, `codex.md`, `claude.md` in the same folder).

Order:
1. `campaigner/launch_proposal_schema.py`: `validate_proposal_v2(obj)` (closed schema, unknown fields refused), `make_valid_proposal_v2()` fixture (one new ad set, one existing-ad-set reuse that stays blocked unless the rule exists, one image ad, one video ad, 5 texts + 5 headlines), digest helper consistent with existing `canonical/digest`. Tests: `campaigner/test_launch_proposal_schema.py`, one labelled case per refusal reason.
2. Generalize the write path (campaign_operations.py, campaign_operations_transport.py, operations_config.py, meta_batch.py) for schema_version 2 only: chosen campaign from the verified account list, new or existing ad set (existing only when rule `existing_adset_reuse` exists in the rulebook and its condition holds on live read data), image or video ads (video upload + bounded wait for processing; failure/timeout never re-uploads), single ads without forced feed+story pairing, 5+5 variations inside one ad, always PAUSED, exact approval, read-back. `preview` returns an execution manifest with digest that the owner approves.
3. v1 paths and their tests stay unchanged. No real Meta calls: fake transport only.

Your sandbox keeps `.git` read-only, so do NOT try to commit: leave the changes in your worktree and finish with the report (what changed, tests run and results, open questions). Claude reviews and commits for you.
