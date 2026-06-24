from __future__ import annotations
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import httpx
from mcp.server.fastmcp import FastMCP
from mcp_transfer_node.config import TransferSettings, load_destinations, load_settings
from mcp_transfer_node.metadata import get_record, list_records, mark_deleted
mcp=FastMCP('mcp-transfer-node')
@dataclass(frozen=True)
class ResolvedDestination: name:str; url:str; token:str
def _mp(s:TransferSettings)->Path: return s.metadata_dir/'transfers.jsonl'
def _d(r)->dict[str,Any]: return {'id':r.id,'receivedAt':r.received_at.isoformat(),'source':r.source,'originalFilename':r.original_filename,'storedFilename':r.stored_filename,'storedPath':r.stored_path,'sizeBytes':r.size_bytes,'sha256':r.sha256,'note':r.note,'status':r.status}
def resolve_destination(name:str, settings:TransferSettings, env:Mapping[str,str]|None=None)->ResolvedDestination:
    src=os.environ if env is None else env
    for d in load_destinations(settings.config_dir/'destinations.json'):
        if d.name==name:
            token=src.get(d.token_env)
            if not token: raise ValueError(f'missing token env for destination {name}: {d.token_env}')
            return ResolvedDestination(d.name,d.url.rstrip('/'),token)
    if name.startswith('https://'): raise ValueError('direct URL destinations require configured aliases to avoid exposing tokens')
    raise ValueError(f'unknown destination: {name}')
async def send_file_to_destination(local_path:Path,destination:str,note:str,settings:TransferSettings,env:Mapping[str,str]|None=None)->dict[str,object]:
    if not local_path.exists(): raise FileNotFoundError(f'local file not found: {local_path}')
    if not local_path.is_file(): raise ValueError(f'local path is not a regular file: {local_path}')
    if local_path.stat().st_size>settings.max_file_mb*1024*1024: raise ValueError(f'file exceeds {settings.max_file_mb} MB limit: {local_path.stat().st_size} bytes')
    rd=resolve_destination(destination,settings,env); headers={'Authorization':f'Bearer {rd.token}','X-Transfer-Source':settings.server_name}
    async with httpx.AsyncClient(timeout=60) as client:
        with local_path.open('rb') as f: resp=await client.post(f'{rd.url}/api/upload',files={'file':(local_path.name,f,'application/octet-stream')},data={'note':note},headers=headers)
    payload=resp.json()
    if resp.status_code>=400 or payload.get('success') is not True: raise RuntimeError('destination rejected upload: '+str((payload.get('error') or {}).get('message','unknown error')))
    return dict(payload['data'])
@mcp.tool()
async def send_file(local_path:str,destination:str,note:str='')->dict[str,object]: return await send_file_to_destination(Path(local_path),destination,note,load_settings())
@mcp.tool()
def list_received_files(limit:int=20)->dict[str,object]: return {'files':[_d(r) for r in list_records(_mp(load_settings()),limit) if r.status!='deleted']}
@mcp.tool()
def get_received_file_info(transfer_id:str)->dict[str,object]:
    r=get_record(_mp(load_settings()),transfer_id)
    if r is None: raise ValueError(f'transfer not found: {transfer_id}')
    return _d(r)
@mcp.tool()
def delete_received_file(transfer_id:str)->dict[str,object]:
    s=load_settings(); r=get_record(_mp(s),transfer_id)
    if r is None: raise ValueError(f'transfer not found: {transfer_id}')
    p=Path(r.stored_path)
    if p.exists(): p.unlink()
    return {'deleted':mark_deleted(_mp(s),transfer_id),'transferId':transfer_id}
def run()->None: mcp.run()
