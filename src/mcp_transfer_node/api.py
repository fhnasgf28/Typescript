from __future__ import annotations
import logging, os, uuid
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from mcp_transfer_node.auth import authenticate_peer
from mcp_transfer_node.config import AllowedPeer, TransferSettings, load_allowed_peers
from mcp_transfer_node.files import build_stored_filename, ensure_runtime_dirs, sha256_file
from mcp_transfer_node.metadata import TransferRecord, append_record, get_record, list_records, mark_deleted
from mcp_transfer_node.responses import error_response, success_response
logger=logging.getLogger(__name__)
def _metadata_path(s:TransferSettings)->Path: return s.metadata_dir/'transfers.jsonl'
def _peers(s:TransferSettings)->list[AllowedPeer]: return load_allowed_peers(s.config_dir/'peers.json')
def _dict(r:TransferRecord)->dict[str,object]: return {'id':r.id,'receivedAt':r.received_at.isoformat(),'source':r.source,'originalFilename':r.original_filename,'storedFilename':r.stored_filename,'storedPath':r.stored_path,'sizeBytes':r.size_bytes,'sha256':r.sha256,'note':r.note,'status':r.status}
def create_api_router(settings:TransferSettings)->APIRouter:
    ensure_runtime_dirs(settings); router=APIRouter()
    def require_peer(authorization:str|None=Header(default=None), x_transfer_source:str|None=Header(default=None))->AllowedPeer:
        if not authorization or not authorization.startswith('Bearer ') or not x_transfer_source:
            raise HTTPException(401, detail=error_response('UNAUTHORIZED','Invalid or missing bearer token'))
        peer=authenticate_peer(authorization.removeprefix('Bearer ').strip(), x_transfer_source, _peers(settings))
        if peer is None:
            logger.warning('upload rejected source=%s reason=invalid_credentials', x_transfer_source)
            raise HTTPException(401, detail=error_response('UNAUTHORIZED','Invalid or missing bearer token'))
        return peer
    @router.get('/health')
    def health()->dict[str,object]:
        ensure_runtime_dirs(settings)
        return success_response({'serverName':settings.server_name,'status':'ok','inboxWritable':os.access(settings.inbox_dir,os.W_OK),'metadataWritable':os.access(settings.metadata_dir,os.W_OK),'maxFileMb':settings.max_file_mb})
    @router.post('/api/upload')
    async def upload_file(peer:AllowedPeer=Depends(require_peer), file:UploadFile=File(...), note:str=Form(default=''))->dict[str,object]:
        received_at=datetime.now(timezone.utc); tid=f'transfer_{uuid.uuid4().hex}'; stored=build_stored_filename(received_at,peer.name,file.filename or 'uploaded-file',tid)
        final=settings.inbox_dir/stored; temp=settings.inbox_dir/f'.{tid}.uploading'; max_bytes=settings.max_file_mb*1024*1024; size=0
        try:
            with temp.open('wb') as out:
                while chunk:=await file.read(1024*1024):
                    size+=len(chunk)
                    if size>max_bytes: raise HTTPException(413, detail=error_response('FILE_TOO_LARGE','File exceeds 50 MB limit'))
                    out.write(chunk)
            digest=sha256_file(temp); temp.rename(final)
            append_record(_metadata_path(settings),TransferRecord(tid,received_at,peer.name,file.filename or 'uploaded-file',stored,str(final),size,digest,note,'received'))
            return success_response({'transferId':tid,'storedFilename':stored,'sha256':digest})
        finally:
            if temp.exists(): temp.unlink()
    @router.get('/api/files')
    def files(_:AllowedPeer=Depends(require_peer))->dict[str,object]: return success_response({'files':[_dict(r) for r in list_records(_metadata_path(settings)) if r.status!='deleted']})
    @router.get('/api/files/{transfer_id}/download')
    def download_file(transfer_id:str, _:AllowedPeer=Depends(require_peer))->FileResponse:
        r=get_record(_metadata_path(settings), transfer_id)
        if r is None or r.status=='deleted' or not Path(r.stored_path).exists(): raise HTTPException(404, detail=error_response('NOT_FOUND','Transfer not found'))
        return FileResponse(Path(r.stored_path), filename=r.original_filename)
    @router.delete('/api/files/{transfer_id}')
    def delete_file(transfer_id:str, _:AllowedPeer=Depends(require_peer))->dict[str,object]:
        r=get_record(_metadata_path(settings), transfer_id)
        if r is None or r.status=='deleted': raise HTTPException(404, detail=error_response('NOT_FOUND','Transfer not found'))
        p=Path(r.stored_path)
        if p.exists(): p.unlink()
        return success_response({'deleted':mark_deleted(_metadata_path(settings),transfer_id)})
    return router
