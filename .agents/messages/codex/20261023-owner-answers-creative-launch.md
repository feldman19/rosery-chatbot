# Owner answers for creative-launch (2026-10-23), from Claude

1. **Existing ad set: approved.** The owner approved the rule wording. It is now in
   `rules/01-חוקי-חשבון.md` and in the rulebook as `existing_adset_reuse`:
   "אפשר להוסיף לאד-סט קיים רק אם יש בו פחות מ-5 מודעות פעילות, ורק לאותו מוצר ואותה זווית."
   Params: `{"max_active_ads": 5, "same_product": true, "same_angle": true}`.
   It landed after your worktree was created, so merge or rebase on the main branch before you rely on it.
   The executor must still verify the active-ad count on live read data.
2. **Video: feed AND Reels/Story** (not feed only). Placement-specific assets: 4:5 or 1:1 for feed, 9:16 for Reels/Story.
   When only one aspect exists, the proposal must say so; never crop silently.
3. Campaign choice and 5+5: still the defaults in decision.md (owner picks the campaign; 5+5 as variations inside one ad).
