Owner (2026-10-24, about the live screen): "I like how it's built, but a lot is not understandable, even to me. It must be as accessible, simple and clear as possible; explain the situation to someone who does not understand, with minimum effort." Rewrite the owner-facing copy of `campaigner/simple-web.html` (and Hebrew strings the server sends for cards). Keep behaviour, IDs and tests; do not commit; Hebrew report; no Meta writes; no rules edits.

Principles (apply everywhere):
- Each card answers, in this order, in at most 3 short lines: what is happening (one sentence, everyday words), what to do (the button), why (one sentence with ONE number). Everything else under "פרטים".
- No jargon on the surface: ROAS → "כמה חזר על כל ₪1"; CTR → "כמה לחצו"; ad set → "קבוצת מודעות"; creative → "מודעה/תמונה/סרטון"; retargeting/prospecting → "אנשים שכבר היו באתר" / "קהל חדש"; Advantage+ enhancements → "שינויים אוטומטיים שמטא עושה במודעה". Technical names only inside "פרטים".
- Numbers people can feel: "הוציאה ₪270 בשלושה ימים והביאה ₪430 — כל שקל החזיר ₪1.59. כדי להרוויח צריך לפחות ₪2.60." Shekel before the number, Hebrew number agreement.
- A one-line page summary at the top of היום: "בשורה אחת: אתמול נמכר ב-₪3,270 (קצת פחות מהרגיל). יש 2 מודעות שמפסידות כסף ו-9 מודעות שמטא שינתה בלי רשות."
- When today contradicts the advice, say it plainly first: "היום היא דווקא מוכרת טוב (כל שקל החזיר ₪5.91). כדאי לחכות למחר לפני שעוצרים." and make "לחכות" the primary option, stop secondary.
- Drift cards: "מטא שינתה את המודעה בלי רשות (הוסיפה אנימציה, שינתה טקסט…). לפי החוק שלך זה צריך להיות כבוי." List at most 3 changes, the rest under פרטים. Group identical cards: "9 מודעות עם אותו שינוי — לכבות בכולן" (one link list), instead of 9 near-identical cards.
- Experiments: never show internal system hypotheses (e.g. the score variant without CTR) as experiment ideas to the owner — those stay in "מה המערכת לומדת". Owner-facing ideas must be business questions in plain Hebrew ("מה מוכר יותר: סרטונים של שרשראות או של חולצות?"), with what is missing in one line and the cost in shekels.
- "מה המערכת לומדת": one plain sentence per hypothesis ("בודקים אם דרך חדשה לדרג מודעות מזהה מוקדם יותר מה יעבוד. עוד אין מספיק נתונים — תוצאה ראשונה בעוד כשבוע.").
- Every "?" opens a 2-line explanation in everyday words.
- Read it as a shop owner who never ran ads: if a sentence needs ads knowledge, rewrite it.
Add tests that the surface text of each card type contains no banned jargon (ROAS, CTR, Advantage+, אד-סט, קריאייטיב, ריטרגטינג, פרוספקטינג) outside "פרטים". Full suite (baseline here 6 failures, 19 errors; your sandbox shows 8/30–31 for missing local files — say which).
