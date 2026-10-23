# Owner decision (2026-10-23), from Claude

The existing-ad-set rule is an explicit EXCEPTION to 'לא מוסיפים לאד-סט פעיל'. The account rules file now says:
- '... לא מוסיפים לאד-סט פעיל (חוץ מהחריג בשורה הבאה), לא פותחים קמפיין נפרד.'
- '- חריג: אפשר להוסיף לאד-סט קיים רק אם יש בו פחות מ-5 מודעות פעילות, ורק לאותו מוצר ואותה זווית.'
The rulebook source hash changed; rule ids and params are unchanged (existing_adset_reuse, max_active_ads=5).
When merging, take the main branch version of campaigner/rules/*.
