Step 2 of `.agents/design/experiments.md`: routes and owner cards for the experiment engine (`campaigner/experiments.py`, already merged). No Meta writes and no Meta calls in this step: "אשר והכן במצב מושהה" stays disabled with the note "החיבור למטא ייבנה בשלב הבא, אחרי אישור שלך". Do not commit; Hebrew report; UI Hebrew; do not edit rules.

- Storage: experiments saved in the data folder (JSON or SQLite, locked writes), one running experiment at a time.
- Routes (same session/origin checks as other owner routes; add them to `phone_gateway.py`'s allowlist): list experiments with state and live numbers; candidate proposals (from `experiments.py` ranking fed with the hypothesis journal, the account history in the data folder, and owner ideas); add an owner idea in plain Hebrew; dismiss a candidate; stop an experiment (state only).
- Cards in a new section "ניסויים" on the Ads tab (above "מה המערכת לומדת"): proposed experiment (question, why, arms, ₪/day and total, length, what it will not prove), running (spend/purchases per arm, current probability in plain words, days left), result (verdict, interval, one next step). A text box "יש לך רעיון לבדיקה?" adds an owner idea.
- Tests for the routes and the rendering helpers.

Run the full suite (`cd campaigner && python -B -m unittest discover -p "test_*.py"`; baseline here 6 failures, 19 errors) and report.
