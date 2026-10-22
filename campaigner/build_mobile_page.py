"""Embed the same owner task UI in the authenticated mobile page."""
from pathlib import Path
root=Path(__file__).resolve().parent
template=(root/'owner-portal.template.html').read_text(encoding='utf-8')
template=template.replace('/* STUDIO_DESIGN */',(root/'studio-design.css').read_text(encoding='utf-8'))
template=template.replace('/* DASHBOARD_DESIGN */',(root/'dashboard-design.css').read_text(encoding='utf-8'))
for marker,filename in [('/* OWNER_PORTAL_MODEL */','owner-portal-model.js'),('/* PREPARATION_TASK_UI */','preparation-task-ui.js'),('/* OWNER_PORTAL_APP */','owner-portal.js')]:
 assert template.count(marker)==1
 source=(root/filename).read_text(encoding='utf-8')
 assert '</script' not in source.lower()
 template=template.replace(marker,source)
assert template.count('/* LOCAL_CONFIGURATION */')==1
(root/'owner-web.html').write_text(template.replace('/* LOCAL_CONFIGURATION */',"const token='__SESSION__';const localMode=true;"),encoding='utf-8')
(root/'mobile-web.html').write_text(template.replace('/* LOCAL_CONFIGURATION */','const token=null;const localMode=false;'),encoding='utf-8')
