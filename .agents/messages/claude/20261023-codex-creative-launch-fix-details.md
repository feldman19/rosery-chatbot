# creative-launch — תיקוני סקירה

העבודה נשארה בענף `codex/creative-launch`. לא בוצעו כתיבות ל־Meta/Shopify, הפעלת workers על נתונים חיים, commit או push. בהתאם להנחיית git לקריאה בלבד, קובצי הכללים והאסטרטגיה המעודכנים נקראו מהעץ הראשי והועתקו לעץ העבודה הזה בלבד; לא בוצע merge.

## מה השתנה

- `creation_recipe` הוא פרמטר מפורש בהצעה החתומה: `adset_fields`, `creative_fields`, `optimization_type`. אין ברירת מחדל לייצור; preview בלי מתכון נחסם. הבדיקות משתמשות במתכונים סינתטיים, שאינם הוכחת תאימות ל־Meta.
- הקריאה החוזרת של v2 מבקשת את השדות שנשלחו ומשווה רק אותם, כולל השוואה רקורסיבית של שדות בתוך פריטי רשימות. מספר הפריטים וסדרם עדיין חייבים להתאים.
- חלון הביצוע של v2 נקשר לזמן האישור ונבדק לפני תחילת הכתיבה ב־preflight. הוא אינו נבדק מחדש בכל שליחה. בדיקות שלמות המניפסט, שינוי ההצעה ועצירת בעלים נשארו פעילות. התנהגות התפוגה של v1 נשמרה.
- בדיקת וידאו קוראת ישירות `id,status` מהצומת השמור. צומת שטרם זמין ממתין עד deadline; timeout ההעלאה גדל לפי גודל הקובץ. אין ניסיון חוזר אוטומטי ל־POST.
- דחייה מפורשת של וידאו מאפשרת העלאה בהצעה חדשה ובאישור חדש. צעד unknown ללא provider_id יכול לקבל ויתור מפורש, מתועד, עם operation_id, digest ו־step_key. הנתיב המאומת הוא `/api/campaign-operations/abandon-video`, עם `owner_confirmation: true`; הוא אינו ממשיך את העבודה הישנה. האפשרות לקובץ יתום אצל הספק נשמרת כלא ידועה.
- תקציב CBO חוסם preview של אד־סט חדש עם תקציב משלו.
- סטטוס משתמש במטמון קמפיינים של 30 שניות, שומר את זמן התצפית המקורי ותופס חריגות בלי להחזיר את תוכנן. בדיקת שימוש היסטורי מקבלת את קמפיין v2 הנבחר.
- מוצר באד־סט קיים מחושב מחדש משמות חיים באמצעות אותה פונקציה של `launch_strategy` ו־`account_history.CATEGORIES`. זווית מחייבת `owner_confirmations: [{"adset_id": "...", "same_angle": true}]` בבקשת preview; האישור נכלל במניפסט וב־digest ומאושר דרך אישור המניפסט הקיים.
- סיומות JPEG ו־MOV וסוג התוכן תוקנו. השוואת preflight מתמקדת בקמפיין ובאד־סטים הקיימים שנבחרו; שינוי effective_status או ישות לא קשורה אינו חוסם.
- השינוי בקובץ המשותף `server.py` הוא שלוש שורות בלבד, עבור נתיב הוויתור. קובצי הכללים נבדקו ונשארו LF.

## בדיקות

- `python -B -m unittest test_creative_launch_review test_launch_proposal_schema test_campaign_operations test_campaign_operations_dispatch test_campaign_operations_reconcile test_campaign_operations_batch_transport test_http_creative_launch_flow`: עברו 207 בדיקות.
- לאחר הוספת נתיב הוויתור: `python -B -m unittest test_creative_launch_review.HTTPWaiverTests test_http_creative_launch_flow`: עברו 5 בדיקות, כולל דחיית בקשות ללא session, ללא אישור מפורש ועם שדות נוספים.
- `python -B -m unittest discover -s . -p "test_*.py"`: נרשמו 1,705 בדיקות, 8 כישלונות, 30 שגיאות ו־2 דילוגים. הסריקה המלאה קדמה להשלמות האחרונות; הבדיקות הממוקדות לעיל הורצו לאחריהן.
- לעומת קו הבסיס שנמסר, זהו פער של 2 כישלונות ו־11 שגיאות. כל 38 הבדיקות שנכשלו שוחזרו עם גרסאות ארבעת המודולים ששונו מתוך HEAD והכללים המקוריים: שוב בדיוק 8 כישלונות ו־30 שגיאות. אין כשל creative-launch בדוח המלא.
- `git diff --check` עבר. אין הוכחת ביצוע או latency מול Meta אמיתית.

הדוח המלא נמצא ב־`creative-launch-review-tests.txt`; תוצאת שחזור הבסיס נמצאת ב־`creative-launch-baseline-check.txt`.

## נשאר פתוח

בחירת מתכון Meta בפועל באמצעות canary יחיד ב־PAUSED, ורק לאחר אישור מדויק של הבעלים. לא נקבע מתכון ייצור ולא בוצעה בדיקה חיה.
