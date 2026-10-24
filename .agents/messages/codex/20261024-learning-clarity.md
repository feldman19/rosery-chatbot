Follow-up on `codex/ux-pass` (after copy-sharpen). Do not commit; Hebrew report; no Meta writes; no rules edits. Owner, with screenshots of "מה המערכת לומדת" and "רעיון לבדיקה": "How long has this been in testing? Where does it stand? How would an end user understand it? The test ideas — what do they need from me, how do I start one? Not clear at all. The whole system must be the clearest and most accessible there is — go over it again."

A. "מה המערכת לומדת" — each hypothesis card, surface only (everything technical — weights, "מצב צל", "מחלוקת", "ציון משוקלל", baskets, p-values, backtest numbers, the outside-sources text — goes under "פרטים"):
   1. Title = one everyday sentence of the question: "בודקים: האם דרך חדשה לדרג מודעות מזהה מוקדם יותר אילו מודעות יצליחו?" (the no-CTR variant: "בודקים: האם אפשר לדרג מודעות גם בלי לספור כמה לחצו?").
   2. Progress bar + line from real data: "נבדקו 3 מתוך 10 מקרים שצריך · התחיל ב-22.10". Estimate: "תשובה ראשונה בעוד כ-X ימים" computed from the observed case rate; if not computable → "עוד לא ידוע מתי".
   3. Status in words: "עוד מוקדם — אין תשובה" / "נראה שכן, אבל צריך עוד מקרים" / "יש תשובה: כן/לא".
   4. "מה זה אומר לך:" one line — "כרגע שום דבר לא משתנה בהמלצות. כשתהיה תשובה, נציע לך אם לשנות את הדרך שבה המערכת מדרגת מודעות — רק באישורך."
   5. Button "לבדוק מה ידוע על זה באינטרנט" with a one-line explanation under it ("מחפש מחקרים ותיעוד של מטא. לא משנה כלום במערכת.").
   6. A one-paragraph intro at the top of the section: what learning is, that nothing changes without the owner, and how many questions are open.

B. Experiment ideas ("רעיון לבדיקה"):
   1. Internal system hypotheses (score variants, CTR weight, anything from hypotheses.py) NEVER appear as ideas — they live only in A. Test this explicitly (the screenshot shows "גרסת הציון בלי שיעור הקלקות" as an idea — that must be impossible).
   2. Title = the business question: "מה מוכר יותר: סרטונים של חולצות או של שרשראות?" (not "רעיון לבדיקה").
   3. "מה צריך ממך:" concrete steps in order, each with a ✓ when already satisfied from data: "1. להעלות 5 סרטונים של חולצות ✓ · 2. להעלות 5 סרטונים של שרשראות (חסרים 3) · 3. לאשר תקציב". No "הגדרת שני הצדדים".
   4. "כמה זה עולה וכמה זמן:" "בערך ₪560 לשבוע (₪80 ביום), תשובה אחרי כ-7–14 ימים" — derived from the account's real cost per purchase and the experiment's sample needs; say how it was computed under פרטים.
   5. "מה תקבל בסוף:" one line ("תדע על מה לשים יותר תקציב").
   6. Button: if something is missing → the button goes straight to the missing step ("להעלות סרטונים ›" opens the upload with the right tag). If everything is ready → the real start flow that exists in `experiments.py`/`experiment_ui.py` (check what exists; it must end in the normal approval sheet; nothing goes to Meta without approval). If no start path exists yet, say honestly "הפעלה מכאן תגיע בגרסה הבאה" — never a dead or misleading button.
   7. "הסר הצעה" stays secondary ("לא מעניין אותי").

C. Whole-system pass: open every screen (היום, מודעות, העלאה, שאל, היסטוריה, הגדרות, לומדת, ניסויים, approval sheet, errors/empty states) at 375px and apply the same rules as copy-sharpen: one-line "מה יש כאן" at the top of each screen; each card = what is happening · what to do (button that says the action) · why with one number; עכשיו→אחרי on action cards; no jargon on the surface; correct Hebrew number agreement; no dead buttons; every "?" explains in 2 everyday lines. List in the report every string you changed per screen (before → after) so it can be reviewed.
Tests: no internal hypothesis as an idea; hypothesis card surface has progress + status + "מה זה אומר לך"; idea card has "מה צריך ממך" steps and a working button target. Run ONLY the tests of the files you touched (test_simple_screen*, test_home_screen, test_experiment*, test_hypotheses*, test_rule_engine*, and the new ones). Do NOT run the full suite — it runs once before release.
