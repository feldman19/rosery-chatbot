"""Morning summary for the owner's phone: yesterday in one look, and what waits for him today.

Built from the same management report the simple screen shows; read-only toward Meta and Shopify.
Short plain Hebrew lines, numbers as on the screen. Where it is delivered is decided elsewhere.
"""


def _ils(value):
    return '₪' + format(round(value), ',') if isinstance(value, (int, float)) else '—'


def build(report, receipts=(), *, self_check=None):
    windows = (report or {}).get('windows') or {}
    m1 = (windows.get('1') or {}).get('metrics') or {}
    m7 = (windows.get('7') or {}).get('metrics') or {}
    days7 = (windows.get('7') or {}).get('days') or 7
    lines = ['בוקר טוב ☀️ ROSERY · אתמול']
    revenue, spend = m1.get('store_revenue'), m1.get('spend')
    if isinstance(revenue, (int, float)):
        line = 'מכירות בחנות: ' + _ils(revenue)
        if isinstance(m7.get('store_revenue'), (int, float)) and m7['store_revenue'] > 0:
            delta = revenue / (m7['store_revenue'] / days7) - 1
            line += f" ({'▲' if delta >= 0 else '▼'} {abs(round(delta * 100))}% מהממוצע)"
        lines.append(line)
    if isinstance(spend, (int, float)):
        line = 'הוצאה במטא: ' + _ils(spend)
        if isinstance(revenue, (int, float)) and spend > 0:
            line += f' · {revenue / spend:.2f} מכירות לכל ₪1'
        lines.append(line)
    if isinstance(m1.get('store_orders'), (int, float)):
        lines.append(f"הזמנות: {round(m1['store_orders'])}")
    today = ((report or {}).get('recommendations') or {}).get('today') or {}
    items = [i for i in today.get('items') or [] if i.get('kind') == 'approve']
    if today.get('status') == 'blocked':
        lines.append('')
        lines.append('לא הצלחתי לבדוק היום: חסרים נתונים. כדאי לרענן במסך.')
    elif items:
        lines.append('')
        lines.append(f'מחכים לך היום ({len(items)}):')
        for item in items[:5]:
            lines.append('• ' + item.get('headline', ''))
    else:
        lines.append('')
        lines.append('אין מה לשנות היום ✓')
    done = [r for r in receipts if r.get('state') == 'verified' and not r.get('undone_by')][:3]
    if done:
        lines.append('')
        lines.append('בוצע לאחרונה:')
        for r in done:
            lines.append('• ' + r.get('title', '') + ': ' + (r.get('entity_name') or ''))
    from self_check import brief_line
    lines.insert(0,brief_line(self_check))
    return '\n'.join(lines)
