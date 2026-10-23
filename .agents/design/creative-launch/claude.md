Confirmed — no existing rule for reusing an ad set, as the brief said. Here is the design.

---

# Design: the launch-proposal contract (Claude's steps 1–4 → Codex's steps 5–6)

## 1. The proposal object

Keep today's envelope mechanics (`LaunchProposal`: HMAC-signed, `request_id`/`request_revision`/`proposal_hash` binding, per-asset `_membership`/`_file` re-verification against `drive-assets/`) — they're format-agnostic already and shouldn't change. Extend the **payload** `LaunchProposal.save()` wraps:

```jsonc
{
  "schema_version": 2,                 // new — see below
  "id": "creative-<hash>",
  "summary": "...",
  "strategy": {"campaign_id": "<verified id>", "source": "model_advisory"},
  "adsets": [{
    "key": "creative_<hash>", "label": "...", "audience_hypothesis": "...",
    "rationale": "...", "targeting_mode": "broad_il_adult",
    "target": {"mode": "new"}
             | {"mode": "existing", "adset_id": "<id>", "existing_ad_count": 3,
                "rule_cited": "existing_adset_reuse_v1"}
  }],
  "ads": [{
    "asset_id": "...", "sha256": "...", "media_kind": "image" | "video",
    "source_name": "...", "source_kind": "drive" | "device",
    "review": {"visual_checked": true, "method": "...", "notes": "..."},
    "story": {...} | null,             // null when required_format doesn't pair
    "video": {"asset_id": "...", "sha256": "...", "duration_ms": 8000,
              "thumbnail_asset_id": "..."},   // only when media_kind=video
    "primary_text": "...", "headline": "...", "description": "...",
    "primary_text_options": [...5], "headline_options": [...5],
    "adset_key": "...", "cta": "SHOP_NOW", "url_tags": "...",
    "destination_url": "...", "destination_source": "...",
    "destination_verified_live": false, "classification": {...}
  }],
  "required_format": "single_ad_flexible" | "dedicated_feed_story_single_copy" | "dedicated_feed_story_five_copy",
  "gaps": [...], "current_authorization": {}, "activation_available": false
}
```

Claude's side fills `strategy`, `adsets[].target`, `ads[].media_kind/video`, `headline_options`, `required_format`; Codex's side only reads these — it never infers strategy, it verifies it.

**Refusals the executor (Codex) must apply**, mirroring the strict allow-list style already used everywhere (`preview()`'s `set(body)-{...}`, `vision_output_schema`'s `additionalProperties: False`):
- unknown top-level or nested field, or unrecognized `required_format`/`schema_version` → refuse, never ignore-and-proceed.
- `source_digest`/`proposal_hash` mismatch → already covered by existing `_proposal(expected_source)` / `digest()` machinery; extends unchanged.
- `target.mode == "existing"` without `rule_cited` naming a real rulebook id, or where Codex's own live `_descendants()` read contradicts the claimed `existing_ad_count`/product match → refuse. Codex verifies the condition from live data; it never trusts Claude's count.
- `media_kind == "video"` without a `video` block carrying host-verified technical metadata (duration, no corruption, from `review.visual_checked: true`) → refuse.
- `strategy.campaign_id` not present in a freshly read, verified list of campaigns under the fixed `ACCOUNT` → refuse (generalizes today's hardcoded `PARENT` equality check in `_entity`).

## 2. Compatibility

`LaunchProposal.save/report` already iterates generically over `ad`/`ad['story']` without assuming story is mandatory, and over `asset_id`/`sha256` without assuming image bytes — only `_file()`'s magic-byte sniff is image-only. Extend `_file()` to recognize a video container (e.g. `.mp4`/`ftyp` box) alongside JPEG/PNG; this is additive.

`operations_config.operation_proposal()` and `campaign_operations._prepare()` keep today's `required_format` values (`feed_original_single_copy`, `dedicated_feed_story_single_copy`, `dedicated_feed_story_five_copy`) with unchanged behavior — existing tests (`test_operations_config.py`, `test_campaign_operations.py`) keep passing. A **new** value, `single_ad_flexible`, gets a new code path for: chosen campaign, new-or-existing ad set, image-or-video, no forced story pairing. `schema_version` defaults to `1` when absent (today's records); `_prepare` gates new capabilities (`target.mode=="existing"`, `media_kind=="video"`) behind `schema_version >= 2`, so an old proposal can never accidentally unlock a new write path and a new proposal can never be silently handled by old-shaped logic.

## 3. Contract tests

Add `launch_proposal_schema.py`: a pure-Python `validate_proposal_v2(obj)` plus one builder, `make_valid_proposal_v2()`, returning a complete, digest-consistent fixture (one new adset + one existing-adset reuse + one image ad + one video ad). Both `creative_preparation.py` and `campaign_operations.py`/`operations_config.py` import this module — never duplicate the shape by hand on each side.

`test_launch_proposal_schema.py` (shared) asserts:
- the fixture round-trips through `LaunchProposal.save()`/`report()` unchanged.
- `build_creative_draft()`'s output validates against `validate_proposal_v2`.
- `operation_proposal()`/`_prepare()` accept the exact fixture and produce the expected steps.
- `_prepare()` rejects each of: unknown field added, stale `source_digest`, video `ad` missing its `video` block, `target.mode=="existing"` missing `rule_cited`, `campaign_id` outside the verified account list — one labeled case per refusal reason, so a broken refusal shows up by name, not just "some test failed."

## 4. What I'd change in the split

- The "existing ad set" threshold is a judgment call both sides must agree on — today it exists nowhere. Put it once in `rules/rosery-rulebook.json` as a new rule id (e.g. `existing_adset_reuse_v1`); have Claude's strategy and Codex's refusal check both read that same entry, rather than each hardcoding the number.
- Claude's strategy should read "what exists in the account" from `CampaignOperations.status()['entities']` (already host-verified, read-only) rather than building a second independent Meta-read path — one source of truth for account state.
- Video *technical* validation (duration, corruption, aspect) belongs with Claude's analysis step, alongside `image_validation.py` — add a twin `video_validation.py` there — even though Codex does the Meta upload, so Codex only ever receives already-verified metadata.

## 5. Questions for the owner

1. Exact rule for reusing an existing ad set — my default guess: "fewer than 5 ads AND same product/angle." Confirm or correct the threshold.
2. Are video ads feed-only for v1, or do they also need a Reels/Story crop?
3. Does the owner pick the target campaign per launch in the UI, or may the strategy choose among existing campaigns on its own?
4. Should the old `required_format` paths (hardcoded single campaign, forced story-pairing) be removed once `single_ad_flexible` ships, or kept running in parallel for some transition period?
