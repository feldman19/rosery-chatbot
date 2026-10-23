## Design: True Profit (read‑only)

### Approach
Keep Meta's own ROAS/CPA untouched and add a second, clearly-labeled number — "תשואה אמיתית לכל ₪1" (true return per ₪1 after VAT, COGS, variable costs) — computed server-side from Shopify + owner-supplied costs, shown next to Meta's number, never merged into it. Reuse the existing conditional/scenario machinery in `order_economics.py` rather than inventing a new "verified profit" model: today it already returns `residual_if_sales_include_18pct_vat` / `..._excludes_vat` and `configured_variable_costs` scenarios, with `budget_recommendation_allowed: False` and explicit `missing`/`limitation` fields. True-profit should extend this pattern down to product and ad granularity, still refusing to produce a single number when inputs are missing — per the "missing is unknown, never zero" rule enforced throughout `management_report.py`.

### COGS per product and per order, with current scopes
Current scopes (`read_reports`, `read_orders`) already pull `cost_of_goods_sold` and `net_sales_with_cost_recorded`/`net_sales_without_cost_recorded` via ShopifyQL in `shopify_direct.read_reports`, but only at the aggregate new/returning-customer segment level — not per order or per SKU, and `net_sales_with_cost_recorded` isn't consumed anywhere yet. `shopify_orders.read_orders` (GraphQL) has no line items and already stubs `product_cost: None` pending a cost model. Two options:
- **Stay read-only, aggregate-only**: parse `net_sales_with_cost_recorded` vs `without_cost_recorded` to know what *fraction* of sales has a verified COGS, and report true-profit only over the covered fraction, flagging the rest as unknown. No new scope needed.
- **Per-product COGS**: needs `lineItems` in the orders GraphQL query (already in the `read_orders` scope) plus product `cost` fields — Shopify's GraphQL exposes `InventoryItem.unitCost`, which requires `read_products` (a scope we don't currently have). This is the scope gap to flag to the owner; until granted, per-SKU true-profit stays "unknown," not zero.

### Attribution: Meta vs UTM vs blended
There's no existing bridge between `account_history.py` (Meta-attributed spend/purchases/revenue per ad) and Shopify orders — they're separate universes joined only loosely by cohort timing in `cohort.py`. Three honest options, none pretending to solve attribution:
1. **Meta-attributed revenue, Shopify-sourced costs** (recommended default): keep Meta's purchase attribution as-is, but replace the "revenue" side of per-ad economics with Meta's own `action_values` scaled by the *store-wide* true-margin ratio (true-profit ÷ gross revenue) computed from Shopify for the same window. This is blended but transparent — label it "ad return using store-wide margin" and never claim per-ad COGS precision.
2. **UTM-order linkage**: add `utm_content`/`url_tags` capture if the store sets them, join orders to ads directly. More precise but only works for traffic that actually carries tags (not all does), so coverage would again be partial and must be reported as such.
3. **No attribution, store-level only**: show true-profit as a store-wide number in Today/Ask, without trying to attribute it to individual ads at all.

Recommendation: ship (1) now since it needs no new data, label it loudly as blended, and offer (2) later only if the owner confirms Shopify is tagging checkout URLs with UTM params already.

### Owner questionnaire (Hebrew, for Today/setup flow)
- עלות משלוח ממוצעת להזמנה (אם לא כלולה בדוח Shopify) — ₪?
- עלות אריזה בפועל — האם ה-₪4 הקיים נכון עדיין?
- עמלת סליקה/תשלום בפועל (אחוז + סכום קבוע) לפי ספק סליקה
- אחוז החזרות ועלות ממוצעת להחזרה (איסוף, אריזה חדשה, בדיקה)
- עלויות משתנות אחרות (מתנות, ביטוח משלוח וכו')
- האם יש מפתח API ל-Triple Whale? (אם כן — מאפשר צומת עצמאי לאימות)
- האם Shopify מתעד UTM/url_tags על הזמנות היום?

### Data flow
Shopify (ShopifyQL economics query + orders) → `order_economics.py` (extended with per-window true-profit calc, store-wide margin ratio) → new evidence file under `outputs/` (local, not committed, like existing `economics_evidence.py` outputs) → `account_history.py` read-only consumer adds a `true_return_per_shekel` field alongside existing `roas`/`cpa` (not replacing them) → Today/Ask tabs render both side-by-side.

### Files touched (future implementation, not this doc)
`order_economics.py` (extend), `economics_evidence.py` (new validator for per-product/window COGS coverage), `business_context.py` (add owner questionnaire answers as structured fields, likely promoted out of free-text `profile.json` facts into the rulebook), `account_history.py` (additive field only), Today/Ask UI templates (Hebrew labels), new `rules/` entries for owner-set shipping/returns costs (LF-enforced).

### Risks
Blended attribution read as precise if not labeled clearly; partial COGS coverage silently narrowing to "profitable-looking" subset if coverage isn't surfaced; scope creep toward requesting `read_products` without owner sign-off; any eventual CAPI write step must stay a separate, explicitly-approved phase — not bundled here.

### Tests
Extend `order_economics.py`/`economics_evidence.py` unit tests for: VAT-included vs excluded scenarios, missing-COGS-coverage fraction reporting, zero silent-fill rejection when a day's data is absent. Run baseline suite from `campaigner/`; report any delta from the known 6 failures/19 errors baseline.

### Questions for the owner
1. Confirm current ₪4 packaging and 18% VAT are still accurate.
2. Shipping/payment-fee/returns numbers from the questionnaire above.
3. Is `read_products` scope approval available, or should per-SKU COGS stay out of scope for now?
4. Does the owner want attribution option (1) blended-margin as the default, or prefer store-level-only until UTM tagging is confirmed?
5. Triple Whale API key — exists or not?
