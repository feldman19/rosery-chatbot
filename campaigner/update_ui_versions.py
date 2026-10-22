from pathlib import Path
root = Path(__file__).parent
p = root / 'manager.html'
s = p.read_text(encoding='utf-8')
s = s.replace("{id:task.id,status,note:note.value}", "{id:task.id,status,note:note.value,expected_revision:task.revision||1}")
p.write_text(s, encoding='utf-8')
p = root / 'dashboard.html'
s = p.read_text(encoding='utf-8')
s = s.replace("editingId=null;const draftKey", "editingId=null,editingRevision=null;const draftKey")
s = s.replace("_editingId:editingId", "_editingId:editingId,_editingRevision:editingRevision")
s = s.replace("editingId=d._editingId;", "{editingId=d._editingId;editingRevision=d._editingRevision;}")
s = s.replace("{id:editingId,payload:payload()}", "{id:editingId,payload:payload(),expected_revision:editingRevision}")
s = s.replace("editingId=task.id;go", "editingId=task.id;editingRevision=task.revision||1;go")
p.write_text(s, encoding='utf-8')
