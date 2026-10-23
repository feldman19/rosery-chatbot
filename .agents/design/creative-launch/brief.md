# Brief: the launch proposal format (design only, short)

The owner wants one flow: upload new creatives (images AND videos) → the system analyzes each (angle,
audience hypothesis, message, what is seen/heard) → it forms a strategy (new ad set or an existing one,
which ads go together) → sends the owner a proposal → after exact approval it uploads to Meta (PAUSED),
reads back, and logs the result.

Split agreed by the owner:
- **Claude** builds steps 1–4: upload in the simple screen (`simple-web.html`), image + video analysis
  (`creative_vision.py`, `creative_preparation.py`), the strategy, and the proposal shown to the owner.
- **Codex** builds steps 5–6 and video upload to Meta: generalize the write path (`campaign_operations.py`,
  `campaign_operations_transport.py`, `operations_config.py`, `meta_batch.py`) from one fixed campaign and
  new-ad-set-only to: a chosen campaign from the account, a new OR an existing ad set, image or video ads,
  single ads (no forced feed+story pair) — always created PAUSED, exact approval, read-back.

The one thing that must be agreed first is the **proposal object** that Claude's part produces and
Codex's part executes. Today there are pieces: `launch_proposal.py` (signed draft),
`creative_preparation.build_creative_draft`, `operations_config.operation_proposal`,
`campaign_operations.preview/approve/execute`. Read them.

Owner rules that the strategy must follow (quoted in `campaigner/rules/rosery-rulebook.json`):
- `new_creatives_new_dated_adset`: "קריאייטיבים חדשים → אד-סט חדש מתוארך בתוך הקמפיין הרץ."
- `ads_per_adset`: 5–8 ads per ad set. `scale_in_place`: winners stay in their original ad set.
- `full_copy_set`: every ad has 5 texts + 5 headlines. `cta_shop_now`, `offer_in_headline`, `no_carousel`, etc.
Adding to an EXISTING ad set is new: it needs a rule the owner states (e.g. "only when the ad set has fewer
than 5 ads and the same product/angle") — propose the default and mark it as an owner question.

Write, in under ~700 words:
1. The proposal object: fields (JSON shape), what each side fills, versioning/digest, what makes the
   executor refuse it (unknown field, stale data, rule not cited, video not processed, etc.).
2. How it replaces or extends the existing draft/operation formats without breaking current tests.
3. The contract tests both sides run against a shared fixture.
4. Anything in the split you would change, and why.
5. Questions for the owner.
Design only: do not modify files; reply with the design as your final message.
