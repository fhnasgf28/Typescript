from __future__ import annotations
import hashlib,re
from datetime import datetime, timezone
from pathlib import Path
from mcp_transfer_node.config import TransferSettings
UNSAFE=re.compile(r'[^A-Za-z0-9._-]+'); DASH=re.compile(r'-+')
def ensure_runtime_dirs(settings:TransferSettings)->None:
    for p in (settings.inbox_dir,settings.metadata_dir,settings.config_dir,settings.logs_dir): p.mkdir(parents=True, exist_ok=True)
def sanitize_filename(filename:str)->str:
    name=Path(filename).name
    if name in {'','.','..'}: name='uploaded-file'
    safe=DASH.sub('-',UNSAFE.sub('-',name).strip('.-'))
    return safe or 'uploaded-file'
def sanitize_source(source:str)->str: return DASH.sub('-',UNSAFE.sub('-',source).strip('.-')) or 'unknown'
def build_stored_filename(received_at:datetime, source:str, original_filename:str, transfer_id:str)->str:
    ts=received_at.astimezone(timezone.utc).strftime('%Y-%m-%dT%H%M%SZ'); safe=sanitize_filename(original_filename); src=sanitize_source(source)
    name=f'{ts}-{src}-{safe}'
    if len(name)<=180: return name
    ext=''.join(Path(safe).suffixes)[-20:]; stem=Path(safe).stem[:80]
    return f'{ts}-{src}-{stem}-{sanitize_source(transfer_id)}{ext}'
def sha256_file(path:Path)->str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()
