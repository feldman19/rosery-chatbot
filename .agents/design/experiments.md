# Experiment engine (design, Claude, 2026-10-23)

Goal (owner): an engine that learns, per business, what works and why, through tests run on the ad account,
without concluding from noise; the accumulated, proven per-business knowledge is the moat.

## What an experiment is
- One question, one variable: "do customer-review videos sell more per ₪ than product videos?" Everything
  else equal: same campaign, same copied targeting (gender rule), same daily budget per arm, same dates.
- Arms A and B (two at first). Each arm = one new PAUSED ad set with 3–5 ads of its kind (rule 5–8 ads per
  ad set is respected by pairing arms with enough creatives; the engine refuses a test it cannot fill).
- Primary metric: purchases per ₪1,000 spend and revenue per ₪ (Meta attribution, view-through included,
  as everywhere else). Secondary: cost per checkout and per cart (to read early direction only).
- Budget and length set before start: e.g. ₪100/day per arm, 10–21 days; total cost shown up front.

## Clean comparison
1. Prefer Meta's own A/B test (split test / ad study) so audiences do not overlap and Meta randomizes.
   Verify the endpoint and fields against Meta's documentation before building; if the account or API cannot
   create it, fall back to two ad sets and say on screen that audiences may overlap (weaker evidence).
2. Same start day for both arms, whole weeks (Friday/Saturday in each arm), holiday windows flagged.
3. No mid-test edits. A change to either arm ends the test as "interrupted".

## When it decides (evidence gate, same spirit as the journal)
- Model purchases per ₪ as a Poisson rate per arm with a weak Gamma prior; compute P(B better than A) and the
  likely lift range (90% interval).
- Verdict only when each arm has ≥ ₪600 spend and ≥ 5 purchases, AND P ≥ 0.95 (or ≤ 0.05): "B better" / "A
  better". If the max length is reached without that: "לא הוכרע" with the interval, never a winner.
- Early stop only for harm: an arm with ≥ ₪400 and 0 checkouts while the other has ≥ 3 purchases.
- Results go to the hypothesis journal as evidence (one experiment = one case), and to the per-business
  "מה עובד אצלך" page, with dates, cost, interval and caveats.

## Where ideas come from
- Open hypotheses in the journal ("testing"/inconclusive), account-history trait groups that look different
  but lack data (e.g. testimonial vs product), the owner's own ideas typed in plain Hebrew, and outside
  research (context only). The engine ranks by: expected value, cost to learn, and whether creatives exist.
- At most one running experiment per account at first (budget sanity, cleaner reads).

## Owner flow (plain Hebrew, two approvals)
1. Card "ניסוי מוצע": the question, why it matters, arms, cost (₪/day and total), length, what we will
   learn, what it will NOT prove.
2. "אשר והכן במצב מושהה" → the existing PAUSED write path builds both arms (manifest + digest approval).
3. "הפעל ניסוי" → a separate exact approval activates both arms together (same minute).
4. Daily status line: spend per arm, purchases, current P, days left; "עצור ניסוי" any time.
5. Result card with the verdict and one suggested next step (e.g. "להעביר את התקציב לסוג B").

## Later (with onboarding of more businesses)
- Cross-business priors: results of similar businesses (category, price, audience) become the starting prior
  for a new business, then its own data takes over. Anonymized, opt-in, never one business's data shown to
  another. This network effect is the strongest moat.

## Build order
1. `experiments.py`: model (plan, arms, state machine planned→prepared→running→decided/interrupted),
   Poisson–Gamma decision, ranking of candidates; pure, fully tested with synthetic data.
2. Server routes + card UI (after chat threads land, to avoid conflicts in server.py/simple-web.html).
3. Meta split-test creation through the PAUSED write path, after reading Meta's docs; live canary only with
   the owner's explicit approval.
