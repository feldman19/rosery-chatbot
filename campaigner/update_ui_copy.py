"""One-time local UI copy migration, safe to run repeatedly."""
from pathlib import Path
root = Path(__file__).parent
p = root / 'manager.html'
s = p.read_text(encoding='utf-8')
s = s.replace('async function teamQueue()', 'const managerNotify=note; async function teamQueue()', 1) if 'const managerNotify=note;' not in s else s
s = s.replace("note('הבדיקה נשמרה — לא פורסמה מודעה')", "managerNotify('הבדיקה נשמרה — לא פורסמה מודעה')")
p.write_text(s, encoding='utf-8')

p = root / 'dashboard.html'
s = p.read_text(encoding='utf-8')
s = s.replace('JSON.stringify(payload())', 'JSON.stringify({...payload(),_editingId:editingId})')
s = s.replace("if(d)for(let [k,id]", "if(d&&typeof d._editingId==='string')editingId=d._editingId;if(d)for(let [k,id]") if "if(d&&typeof d._editingId" not in s else s
p.write_text(s, encoding='utf-8')
p = root / 'dashboard.html'
s = p.read_text(encoding='utf-8')
if 'function editTask(' not in s:
    s = s.replace("let step=1,lastQuestion='';", "let step=1,lastQuestion='',editingId=null;")
    s = s.replace("function go(page){", "function go(page){if(page==='prepare'&&!$('task-success').hidden)$('another-task').click();")
    s = s.replace("await api('tasks',payload());", "await api(editingId?'tasks/resubmit':'tasks',editingId?{id:editingId,payload:payload()}:payload());editingId=null;")
    s = s.replace("$('task-form').reset();", "editingId=null;$('task-form').reset();")
    s = s.replace("function taskCard(task){", "function editTask(task){$('task-form').hidden=false;$('task-success').hidden=true;for(let [k,id] of Object.entries({name:'task-name',materials_url:'materials',formats:'formats',copy:'copy',destination:'destination'}))$(id).value=task[k];editingId=task.id;go('prepare');showStep(1);$('draft-status').textContent='תיקון חבילה קיימת — הגרסה הקודמת נשארת עד לשמירה'} function taskCard(task){")
    s = s.replace("c.append(d);return c}", "c.append(d);if(task.status==='needs_changes'){let b=el('button','תקן את החומרים','secondary');b.onclick=()=>editTask(task);c.append(b)}return c}")
    s = s.replace(".checklist li:before{content:'✓'", ".checklist li:before{content:'•'")
    p.write_text(s, encoding='utf-8')
p = root / 'dashboard.html'
s = p.read_text(encoding='utf-8')
s = s.replace("el('span','ממתין לבדיקה','state')", "el('span',({awaiting_manager:'ממתין לבדיקה',needs_changes:'נדרש תיקון',ready_for_manager:'מוכן להמשך בדיקת המנהל'}[task.status]||'בבדיקה'),'state')")
s = s.replace("let d=el('details','');d.append(el('summary','פרטי החומרים'))", "if(task.manager_note)c.append(el('p','הערת המנהל: '+task.manager_note));let d=el('details','');d.append(el('summary','פרטי החומרים'))") if 'if(task.manager_note)' not in s else s
p.write_text(s, encoding='utf-8')
