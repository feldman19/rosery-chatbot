# Brief: true profit instead of revenue (design only, read-only)

Meta optimizes for purchases and purchase value (revenue, including VAT and returns). The owner wants the
system to know the real profit and, later, to feed it back to Meta.

What the owner asked for:
- Pull from Shopify the cost of goods (COGS) per product and the revenue. Current app scopes are
  `read_reports` and `read_orders` (`shopify_direct.py`, `shopify_orders.py`, `private_orders.py`);
  ShopifyQL already returns `cost_of_goods_sold` and `net_sales_with_cost_recorded`.
- Ask the owner setup questions for what Shopify does not know: shipping cost per order, packaging
  (₪4 is recorded in `order_economics.py`), payment fees, return rate and cost per return, other variable costs.
- Triple Whale may be used if the owner has an API key (unknown yet).
- Show "true return per ₪1" (after VAT 18%, COGS and variable costs) per ad and in the Today and Ask tabs,
  next to Meta's own number, never merged with it. The rulebook has break-even CPA ₪190, AOV ₪315, gross margin 0.8.
- Later step, design only: send a profit value per purchase to Meta through the Conversions API
  (a write; it needs the owner's exact approval and a controlled comparison).

Existing pieces to reuse: `order_economics.py`, `economics_evidence.py`, `business_context.py`,
`account_history.py` (ad-level history), `management_report.py` (missing is unknown, never zero).

Constraints: read-only toward Meta and Shopify in this work. Order data is private: no customer
identities leave the machine; the existing encrypted order store stays as it is.

Write: how to get COGS per product and per order with current scopes (or which scope is missing);
how to tie profit to ads (Meta attribution vs. UTM/url_tags on orders vs. blended); the owner
questionnaire; data flow; files; UI (Hebrew); risks; tests; questions for the owner. Under ~900 words.
