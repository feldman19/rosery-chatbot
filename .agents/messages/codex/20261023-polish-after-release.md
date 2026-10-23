Polish after releasing c688e52 (seen on the live screen at phone size). Fix on your branch, tests where logic changes, do not commit, Hebrew report. No Meta writes, no rules edits, UI Hebrew.

1. Experiments card (Ads tab) is mostly "לא ידוע / טרם הוגדרה": a proposal with no arms, no budget and no CPA should not look like a ready plan. Show such a candidate as a short "רעיון לבדיקה" (question + why + what is missing in one line, e.g. "חסרים: 5 קריאייטיבים לכל צד, מחיר רכישה בחשבון") and hide the empty arm/budget/cost lines; fill the account CPA from the 7-day report (spend / purchases) when available, and a default budget suggestion (₪100/day per arm) with the total.
2. Chat list: the preview line shows raw `**` from old answers — strip markdown in previews too; "זמן התשובה הישנה אינו ידוע" → hide when unknown; the kind pill "שאלה כללית" looks like an empty input box — style it as a small pill; the search row is cramped at 375px (search box full width on its own line, "ארכיון" toggle as a chip).
3. Footer says "עדכון אוטומטי לא פעיל: ... START-ROSERY.bat". The owner chose manual releases; remove that sentence (keep the version line). Do not change `run_owner.py`.
4. The browser console shows several 403 responses on page load; find which requests return 403 on a fresh load of the simple screen and fix the cause if it is ours (e.g. a request sent before the session token or to an owner-only route), or report why they are expected.

Run the full suite (`cd campaigner && python -B -m unittest discover -p "test_*.py"`; baseline here 6 failures, 19 errors) and report.
