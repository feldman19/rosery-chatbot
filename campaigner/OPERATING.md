# ROSERY pilot — current operating path

Open the manager at http://127.0.0.1:8767/ while its local process is running.

1. **דוחות** shows advertising and store results separately. Choose the period and campaign; missing values are not zero and platform attribution is not verified profit.
2. **מרכז פעולות → הוספת מודעות → השתמש בחומרים הקיימים** opens the saved proposal. Select images and one reviewed copy per ad. Continue to the exact preparation preview; navigation and preview do not publish.
3. **בקשה וחומרים חדשים** saves a new preparation brief and Drive links. After saving, explicitly import the sources. The source gallery contains downloaded image files, not approved ads. Selecting source images opens an unsent request for content review.
4. Read preparation progress inside **בדיקות וייבוא חומרים**. A new request invalidates earlier source/proposal bindings. An uncertain receipt is reconciled with its existing idempotency key rather than submitted as a new action.
5. **ניהול קיים** handles the fixed ROSERY parent campaign. Review exact current settings and the proposed difference before separately approving and executing. Current production phone access does not grant campaign execution.

The preparation executor supports an explicit paired feed/story, single-copy API path as well as the older feed-only path. These formats are separate: one must never silently substitute for the other or discard requested copy alternatives. Creation stays paused and requires approval of the exact proposal. API readback is not rendered visual acceptance, editability proof, delivery or profitability.

As of 20 October, real image-analysis evidence and an isolated UI preparation walkthrough exist. The walkthrough used fake write providers; a complete owner-driven live upload through this UI remains unproven. Do not label the full workflow production-ready or promise upload timing from this evidence.

**הצעות לפעולה** distinguishes a supported proposal from insufficient evidence. The current evidence includes active-ad inventory, six selected historical creative-replacement receipts and owner-only Shopify economics. The receipts do not prove complete change history. Store costs and historical scenarios do not set an approved CPA ceiling. Autonomous scaling is not implemented.

The current manager runtime uses `--no-startup-execution-workers`: read-only report refresh can run, but this does not mean an agent continuously develops the product or automatically executes queued campaign changes. A preparation/review task is complete only when its actual result and remaining limitations are recorded.

The 2026-10-18 source-gallery repair validates existing cached bytes and dimensions only. It preserves provider timestamps and does not mark images visually reviewed. No Drive or advertising write is required for that repair.

## Development verification

From this directory: `python -B -m unittest discover -p "test_*.py"`.
Run each `test_*.js` with Node for the offline DOM suites. HTTP tests use temporary data and fake providers. Browser walkthroughs provide separate layout evidence; neither test type proves real campaign delivery or profitability.

`README.txt` contains historical implementation notes. Earlier capability statements and test counts describe their recorded revision, not current live state.

Source-grid thumbnails are separate authenticated JPEG previews, bounded to480 pixels. They do not replace, recompress or authorize the original advertising files. Final hour evidence:759 Python cases and11 offline Node suites; isolated browser/FakeProvider acceptance only.
