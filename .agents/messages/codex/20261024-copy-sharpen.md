Follow-up on `codex/ux-pass` (after the history view). Owner: the plain-language pass is still not clear and sharp enough. Do not commit; Hebrew report; no Meta writes; no rules edits. Keep IDs/behaviour/tests.

Bugs seen on the live preview (fix all, add tests so they cannot return):
1. Number agreement: "1 הצעות לעצירת מודעות" → "הצעה אחת לעצור מודעה". Write one helper (1 → "אחת/אחד", singular noun; 2 → "2 …"/"שתי"; else plural) and use it everywhere counts appear.
2. Blind replacement produced "שינויים אוטומטיים שמטא עושה במודעה מודעה". Never substitute the long phrase inside lists; inside a list of changes use short names ("אנימציה", "טקסט אוטומטי", "התאמת תמונה למיקום"). No word may repeat twice in a row (test).
3. "הופעלו 0 שינויים:" with an empty list → a card with 0 changes must not render (or say what is unknown). Test.
4. Two identical "4 מודעות עם אותו שינוי" cards → merge groups by the same set of changes; if the sets differ, the title names the change ("4 מודעות עם אנימציה אוטומטית").
5. Stop card does not say WHICH ad or why in money. Title = the action + the ad name: "לעצור את „תוכן חדש 1“". One line why: "בשלושה ימים הוציאה ₪130 והחזירה ₪2.17 על כל ₪1 — מתחת ל-₪2.60 שקבעת." Button "עצור".
6. "לשים עין" card has no reason → "לעקוב אחרי „ביקורת לקוח R02“: הוציאה ₪41 היום בלי מכירה. אם מחר עדיין בלי מכירה — נציע לעצור."
7. Top summary must be a real sentence with a judgement: "אתמול נמכר ב-₪3,344. יש מודעה אחת שכדאי לעצור ו-10 מודעות שמטא שינתה בלי רשות." No semicolons.
8. "מתחת לסף העצירה שלך (₪2.6)" → "היום כל ₪1 בפרסום החזיר ₪2.53 — קצת פחות מ-₪2.60 שקבעת. מוקדם לשפוט, היום עוד לא נגמר." Money always with 2 decimals for ratios (₪2.60).
9. "רווחיות אמיתית" → "כמה באמת הרווחת" + one line what is missing ("חסרות עלויות מוצר ומשלוח — 2 דקות להשלים").
10. "גרסה לא ידועה" in the footer → hide when unknown.

Rules for every string: action verb first, the ad's name in quotes, one number with ₪, no semicolons, ≤ 2 lines on a 375px screen, no "בניגוד לחוק" (say "לפי החוק שלך זה צריך להיות כבוי"). Section titles say what to do: "מה לעשות היום", "מודעות שמטא שינתה בלי רשות".
Add a test that renders each card type from fixtures and checks: no doubled words, no "0 שינויים", correct singular for 1, ad name present in stop/watch cards. Full suite (baseline 6 failures, 19 errors).

11. MOST IMPORTANT — the "Meta changed the ad" cards (owner, with a screenshot: "it does not explain what the problem is"). Today they say "מטא שינתה הגדרות במודעה בלי אישור שלך · הופעלו 1 שינויים: שיפורים סטנדרטיים" — no ad name, no picture, no idea what the customer sees or why it hurts. Each card / group must answer:
   - WHICH ad: name in quotes + the thumbnail.
   - WHAT the customer sees differently, per feature in everyday words (map every Meta feature key to such a sentence; unknown key → "שינוי אוטומטי נוסף של מטא"): e.g. standard_enhancements → "מטא משנה בעצמה את הבהירות והחיתוך של התמונה ומוסיפה לה אפקטים"; text_optimizations → "מטא מחליפה את הטקסט שכתבת בטקסט אחר משלה"; image_animation → "מטא הופכת את התמונה לסרטון זז"; music → "מטא מוסיפה מוזיקה לבחירתה"; description_automation → "מטא מוסיפה שורת תיאור שלא כתבת"; adapt_to_placement → "מטא חותכת את התמונה אחרת בכל מקום שבו היא מופיעה"; video_filtering → "מטא מוסיפה פילטר לסרטון"; site_extensions → "מטא מוסיפה קישורים לעמודים אחרים באתר".
   - WHY it is a problem, one line: "הלקוחות רואים מודעה שלא אישרת, ואי אפשר לדעת אם מה שעובד זה התוכן שלך או השינוי של מטא. החוק שלך: לכבות."
   - THE ACTION + effect: button "לכבות" and under it "המודעה תחזור להיראות בדיוק כמו שהעלית. המודעה ממשיכה לרוץ; ייתכן שמטא תלמד אותה מחדש כמה ימים. (Must match what swap_creative really does — check and state it truthfully.)" "השאר כך" stays secondary.
   - The group card ("4 מודעות עם אותו שינוי") names the change in its title ("4 מודעות שמטא משנה להן את הטקסט"), lists the 4 ad names, and the expand shows each with its button.
   - The word "שיפורים" never appears on the surface (it sounds positive); say "שינויים של מטא".
   Test: every drift card's surface text contains an ad name and a "what the customer sees" sentence, and never "שיפורים סטנדרטיים"/"הופעלו N שינויים".

12. EVERY action card gets a fixed "עכשיו → אחרי" block (owner: "the clearest thing in the world: what is happening now and what you propose to change"). Two short rows, same layout in all card types, right under the title:
    - "עכשיו:" the current state in the customer's / owner's terms, with the number. Stop: "עכשיו: „תוכן חדש 1“ פעילה ומוציאה כ-₪45 ביום. כל ₪1 החזיר ₪2.17." Meta-changes: "עכשיו: מטא מחליפה את הטקסט שלך, מוסיפה אנימציה ומחביאה את המחיר (3 שינויים)." Budget: "עכשיו: ₪100 ביום."
    - "אחרי:" exactly what will be different after the button, and what will NOT change. Stop: "אחרי: המודעה מושהית, לא תוציא יותר כסף. אפשר להפעיל שוב בלחיצה." Meta-changes: "אחרי: הלקוחות רואים את המודעה בדיוק כמו שהעלית. המודעה ממשיכה לרוץ." Budget: "אחרי: ₪120 ביום (+₪20)."
    - Then one "למה:" line with one number. Everything else under פרטים.
    - The button text repeats the change ("לעצור את המודעה", "לכבות את 3 השינויים", "להעלות ל-₪120"). For catalog/multi-media ads that must be done in Meta, the button says "לפתוח את המודעה במטא" and the card says in one line what to click there.
    - "השאר כך" / "הסר מהרשימה" stay secondary. The approval sheet (approve-execute preview) uses the same עכשיו/אחרי rows from the server's exact preview.
    Test: every action card's surface has the עכשיו and אחרי rows, non-empty.
13. Audience type, NEVER by name (owner: "we said it was not identified as remarketing in the end — use all the data"). In `rule_engine.py` remove the name/objective regex fallback (`RETARGETING.search(campaign_text)`) as a decision input. Retargeting = targeting reads custom audiences of site visitors/engagers (include), or the data says so (high frequency + mostly returning customers, from `audience_classification`). When the targeting could not be read AND the data is insufficient → audience "לא ידוע", use the NORMAL threshold, and the card says "לא הצלחנו לקרוא למי המודעה מוצגת — השתמשנו בסף הרגיל." Never print "זוהתה … לפי השם" anywhere. The catalog ad "קטלוג נמכרים ביותר" (broad Israel targeting, frequency ~2.8) must come out as קהל חדש with the normal threshold — add a fixture test for exactly this case, plus one where targeting includes a website custom audience → retargeting.
