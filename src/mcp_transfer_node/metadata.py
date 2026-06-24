from __future__ import annotations
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
@dataclass(frozen=True)
class TransferRecord:
    id:str; received_at:datetime; source:str; original_filename:str; stored_filename:str; stored_path:str; size_bytes:int; sha256:str; note:str; status:str
    def to_json_dict(self)->dict[str,object]:
        d=asdict(self); d['received_at']=self.received_at.isoformat(); return d
    @classmethod
    def from_json_dict(cls,p:dict[str,object])->'TransferRecord':
        return cls(str(p['id']),datetime.fromisoformat(str(p['received_at'])),str(p['source']),str(p['original_filename']),str(p['stored_filename']),str(p['stored_path']),int(p['size_bytes']),str(p['sha256']),str(p.get('note','')),str(p['status']))
def append_record(path:Path, record:TransferRecord)->None:
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a',encoding='utf-8') as f: f.write(json.dumps(record.to_json_dict(),sort_keys=True)+'\n')
def list_records(path:Path, limit:int=50)->list[TransferRecord]:
    if not path.exists(): return []
    rec=[TransferRecord.from_json_dict(json.loads(l)) for l in path.read_text(encoding='utf-8').splitlines() if l.strip()]
    return list(reversed(rec))[:limit]
def get_record(path:Path, transfer_id:str)->TransferRecord|None:
    return next((r for r in list_records(path,10000) if r.id==transfer_id), None)
def mark_deleted(path:Path, transfer_id:str)->bool:
    if not path.exists(): return False
    records=list(reversed(list_records(path,10000))); changed=False; out=[]
    for r in records:
        if r.id==transfer_id and r.status!='deleted':
            out.append(TransferRecord(r.id,r.received_at,r.source,r.original_filename,r.stored_filename,r.stored_path,r.size_bytes,r.sha256,r.note,'deleted')); changed=True
        else: out.append(r)
    if changed: path.write_text(''.join(json.dumps(r.to_json_dict(),sort_keys=True)+'\n' for r in out), encoding='utf-8')
    return changed
