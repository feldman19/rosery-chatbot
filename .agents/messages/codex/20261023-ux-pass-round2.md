Round 2 on your `codex/ux-pass` branch (it already has your UX pass plus two small fixes from Claude: inline `.fact-chip` and rule-fixed experiment ideas filtered in `experiments.py`). Owner: "check every bug; show today's data; this must be a polished system." Fix all below, keep tests green, do not commit, Hebrew report. No Meta writes, no rules edits, UI Hebrew.

A. Today's data (server side).
1. Shopify: the store read stops at yesterday, so today's `store_orders`/`store_revenue` are null ("דוח החנות אינו מכסה את כל תאריכי החלון"). Include today so far in the Shopify read (`shopify_direct.py`, the live collector) with the shop's timezone, and mark it partial. Missing stays null, never 0.
2. Meta: today's account `spend` is null while today's ad rows carry spend (₪129, ₪100, …). Find why `_daily` (management_report.py ~346) leaves spend unknown for the partial day (coverage of spend evidence?) and fill it from the complete read for today; if the read is genuinely incomplete, show "לפחות ₪X" from the ad rows with that wording, not "—". ROAS for today follows.
3. The today view (simple-web.html periodData/hero ~529-577): "היום עד עכשיו · מתעדכן כל כמה דקות" with the last update time; same cards as other days.

B. Screen bugs (from a QA pass at 375px; line numbers refer to your branch's simple-web.html).
1. On iPhone Safari the bottom tab bar was seen floating mid-page while scrolling. Make it robust: no `overflow-x:clip` on html/body (put it on `main`), `nav.tabs{position:fixed;left:0;right:0;bottom:0}`, no backdrop-filter on the fixed bar (use a solid surface with a top border), no animation restarting on every render (run `enter` once per tab switch), `dvh` instead of `vh` (sheet max-height), composer offset from the real bar height.
2. "יש גרסה חדשה של המסך" banner: show only when the server's version is really newer than the page's (compare commit ids, never a raw placeholder); never cover the header (put it below the header, not sticky over it); "__VERSION__" must never appear (fallback "גרסה לא ידועה").
3. Learning card: outside research shown as a short Hebrew summary (2 lines) + "מקורות (N)" collapsed list with plain links (source name only, no green bold paragraphs); fix bidi around English names; if the last outside check failed, say it in one muted line with "נסה שוב". Also find why the outside check fails now ("המחקר החיצוני נכשל") in `outside_check.py` / its server thread and fix or report the cause.
4. Experiments: no API names in text (SHOP_NOW, CTR, ROAS weights) — Hebrew labels; the idea textarea/button styled like the rest, ≥44px.
5. Upload tab: an expired draft shows as "ההצעה פגה" with one button "הכן מחדש" and nothing else active; one campaign picker only (after analysis), defaulting to the campaign the analysis used, never silently to an internal test campaign; budget field empty with placeholder "₪ ליום" (no silent 100); preview "קנה עכשיו" buttons look non-interactive; analysis text normal weight; one ▶ only.
6. Truncated subtitles (`.hello p` nowrap): allow wrapping to 2 lines.
7. Hebrew grammar: number agreement ("מכירה אחת", "2 מכירות", "מודעה אחת"), "ייתכן שמתאים", "מחכה לאישורך מהיום", consistent "עצור מודעה" title/button pair, ₪ placement in RTL (₪600, ₪3.16 על כל ₪1) via an LTR isolate.
8. Sheets: role="dialog", aria-modal, focus to the sheet title, background scroll locked while open, Escape/backdrop closes.
9. Ask tab: styled kind picker (chips, not a tiny native select), back button aligned, an empty state ("עוד אין שיחות. שאל משהו כדי להתחיל") and an error state when the list request fails.
10. Touch targets ≥44px for "?" buttons and checkboxes (enlarge the hit area).

Verify in a 375px browser if your tools allow; run the full suite (`cd campaigner && python -B -m unittest discover -p "test_*.py"`; baseline here 6 failures, 19 errors) and report.
