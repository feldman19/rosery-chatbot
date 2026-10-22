"""Owner-selected source subset bound to the current request and exact bytes."""
import json,re
from pathlib import Path
from batch_limits import MAX_CREATIVE_IMAGES


class CreativeSelection:
 def __init__(self,directory):self.path=Path(directory)/'creative-selection.json'
 def current(self,request):
  try:value=json.loads(self.path.read_text(encoding='utf-8'))
  except FileNotFoundError:return None
  except (OSError,ValueError):raise ValueError('Saved creative selection unreadable') from None
  if not isinstance(value,dict):raise ValueError('Invalid saved creative selection')
  if (value.get('request_id'),value.get('request_revision'))!=(request['id'],request['revision']):return None
  return value
 def save(self,request,selected,available):
  if not isinstance(selected,list) or not 1<=len(selected)<=MAX_CREATIVE_IMAGES:raise ValueError('Select up to 30 exact image files')
  byid={a['id']:a for a in available};seen=set();items=[]
  for row in selected:
   if not isinstance(row,dict) or set(row)!={'id','sha256'}:raise ValueError('Exact selected source identity required')
   identity=row['id'];sha=row['sha256']
   if not isinstance(identity,str) or not isinstance(sha,str) or not re.fullmatch('[a-f0-9]{64}',sha) or identity in seen or byid.get(identity,{}).get('sha256')!=sha:raise ValueError('Selected source changed or is unavailable')
   seen.add(identity);items.append(dict(row))
  value={'request_id':request['id'],'request_revision':request['revision'],'items':items}
  temp=self.path.with_suffix('.pending');temp.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8');temp.replace(self.path)
  return value
 def apply(self,request,assets):
  selection=self.current(request)
  if selection is None:return assets
  byid={a['id']:a for a in assets};result=[]
  items=selection.get('items')
  if not isinstance(items,list) or not 1<=len(items)<=MAX_CREATIVE_IMAGES:raise ValueError('Invalid saved creative selection')
  seen=set()
  for item in items:
   if not isinstance(item,dict) or set(item)!={'id','sha256'}:raise ValueError('Invalid saved selected source')
   row=byid.get(item.get('id'))
   if not row or row['id'] in seen or row.get('sha256')!=item.get('sha256'):raise ValueError('Selected source bytes changed; choose current images again')
   seen.add(row['id']);result.append(row)
  return result
