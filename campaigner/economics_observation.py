"""Descriptive Shopify aggregates only; no profitability or action authority."""
from datetime import datetime,timezone
from decimal import Decimal,InvalidOperation
from business import ACTIVE as BUSINESS
SHOP=BUSINESS.shop
FIELDS=('orders','net_sales','shipping_charges','cost_of_goods_sold','net_sales_with_cost_recorded','net_sales_without_cost_recorded')
LABELS={'orders':'מספר הזמנות מדווח','net_sales':'מכירות נטו מדווחות','shipping_charges':'משלוח שנגבה מהלקוחות','cost_of_goods_sold':'עלות מוצרים הרשומה ב־Shopify','net_sales_with_cost_recorded':'מכירות שדווחו עם עלות רשומה','net_sales_without_cost_recorded':'מכירות שדווחו ללא עלות רשומה'}
def recorded_economics(snapshot,now=None):
 now=now or datetime.now(timezone.utc)
 if not isinstance(now,datetime) or now.tzinfo is None:raise ValueError('Aware observation clock required')
 sources=snapshot.get('sources') if isinstance(snapshot,dict) else {}
 source=sources.get('shopify') if isinstance(sources,dict) else {}
 source=source if isinstance(source,dict) else {}
 data=source.get('data');data=data if isinstance(data,dict) else {}
 table=data.get('economics_28d') or {}
 result={'state':'unavailable','scope':'store_aggregate_not_customer_segments','source_ref':'sources.shopify.data.economics_28d','shop':SHOP,'currency':BUSINESS.currency,'observed_at':source.get('last_success_at'),'freshness':'unknown','source_error_present':bool(source.get('error')),'period':{'stated_window':'28 ימים עד אתמול לפי הגדרת האיסוף; תאריכי התחלה וסיום לא נשמרו עם הנתונים','since':None,'until':None,'bounds_verified':False,'not_aligned_with_meta_windows':True},'values':{},'labels':LABELS.copy(),'cost_reconciliation':{'state':'unknown','difference':None,'coverage_verified':False},'decision_authority':False,'verified_profit':False,'limitations':['עלות רשומה בחנות אינה הוכחה לעלות ששולמה בפועל.','לא חושבו רווח, LTV, CAC או תקציב מומלץ.','אין כאן הפרדה בין לקוחות חדשים לחוזרים.','טיפול במע״מ, עמלות, עלות משלוח בפועל והחזרות דורשים אימות נפרד.','אין לשייך סכומים אלה לחלונות הביצועים של Meta.']}
 if not isinstance(data,dict) or data.get('verified') is not True or data.get('shop')!=SHOP or data.get('currency')!=BUSINESS.currency:return result
 try:
  stamp=datetime.fromisoformat(source['last_success_at']);age=(now-stamp).total_seconds()
  result['freshness']='fresh' if 0<=age<=900 and not source.get('error') else 'stale'
 except (ValueError,KeyError,TypeError):pass
 rows=table.get('rows') if isinstance(table,dict) else None
 if not isinstance(rows,list) or len(rows)!=1 or not isinstance(rows[0],dict):return result
 values={}
 try:
  for name in FIELDS:
   raw=rows[0][name]
   if raw is None or isinstance(raw,bool):raise ValueError()
   value=Decimal(str(raw))
   if not value.is_finite() or abs(value)>Decimal('1e15'):raise ValueError()
   values[name]=value
  if values['orders']<0 or values['orders']!=values['orders'].to_integral_value():raise ValueError()
 except (KeyError,ValueError,InvalidOperation):return result
 result['values']={k:str(v) for k,v in values.items()};result['state']='recorded_with_gaps'
 difference=values['net_sales_with_cost_recorded']+values['net_sales_without_cost_recorded']-values['net_sales']
 result['cost_reconciliation']={'state':'arithmetic_matches_not_coverage_proof' if difference==0 else 'unreconciled','difference':str(difference.quantize(Decimal('.01'))),'formula':'with_cost + without_cost - net_sales','coverage_verified':False}
 if difference!=0:result['limitations'].append('סכומי המכירות עם ובלי עלות רשומה אינם מתיישבים עם המכירות נטו; אין להסיק מכך כיסוי עלויות.')
 return result
