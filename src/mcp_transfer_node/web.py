from __future__ import annotations
import uuid
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from mcp_transfer_node.auth import verify_web_password
from mcp_transfer_node.config import TransferSettings
from mcp_transfer_node.files import build_stored_filename, sha256_file
from mcp_transfer_node.metadata import TransferRecord, append_record, get_record, list_records, mark_deleted
TEMPLATES=Jinja2Templates(directory=str(Path(__file__).parent/'templates'))
def _metadata_path(s:TransferSettings)->Path: return s.metadata_dir/'transfers.jsonl'
def _login(req:Request)->None:
    if req.session.get('authenticated') is not True: raise HTTPException(303, headers={'Location':'/login'})
def create_web_router(settings:TransferSettings)->APIRouter:
    router=APIRouter()
    @router.get('/login', response_class=HTMLResponse)
    def login_page(request:Request): return TEMPLATES.TemplateResponse('login.html',{'request':request,'error':''})
    @router.post('/login', response_class=HTMLResponse)
    def login(request:Request,password:str=Form(...)):
        if not verify_web_password(password,settings.web_admin_password): return TEMPLATES.TemplateResponse('login.html',{'request':request,'error':'Login gagal'},status_code=401)
        request.session['authenticated']=True; return RedirectResponse('/',303)
    @router.post('/logout')
    def logout(request:Request): request.session.clear(); return RedirectResponse('/login',303)
    @router.get('/', response_class=HTMLResponse)
    def index(request:Request): _login(request); return TEMPLATES.TemplateResponse('index.html',{'request':request,'settings':settings,'records':[r for r in list_records(_metadata_path(settings)) if r.status!='deleted']})
    @router.post('/web/upload')
    async def web_upload(request:Request,file:UploadFile=File(...),source:str=Form(default='manual'),note:str=Form(default='')):
        _login(request); now=datetime.now(timezone.utc); tid=f'transfer_{uuid.uuid4().hex}'; stored=build_stored_filename(now,source or 'manual',file.filename or 'uploaded-file',tid); final=settings.inbox_dir/stored; temp=settings.inbox_dir/f'.{tid}.uploading'; size=0
        try:
            with temp.open('wb') as out:
                while chunk:=await file.read(1024*1024):
                    size+=len(chunk)
                    if size>settings.max_file_mb*1024*1024: raise HTTPException(413, detail='File terlalu besar')
                    out.write(chunk)
            digest=sha256_file(temp); temp.rename(final)
            append_record(_metadata_path(settings),TransferRecord(tid,now,source or 'manual',file.filename or 'uploaded-file',stored,str(final),size,digest,note,'received'))
            return RedirectResponse('/',303)
        finally:
            if temp.exists(): temp.unlink()
    @router.get('/web/files/{transfer_id}/download')
    def web_download(request:Request,transfer_id:str):
        _login(request); r=get_record(_metadata_path(settings),transfer_id)
        if r is None or r.status=='deleted': raise HTTPException(404,detail='File tidak ditemukan')
        return FileResponse(Path(r.stored_path),filename=r.original_filename)
    @router.post('/web/files/{transfer_id}/delete')
    def web_delete(request:Request,transfer_id:str):
        _login(request); r=get_record(_metadata_path(settings),transfer_id)
        if r:
            p=Path(r.stored_path)
            if p.exists(): p.unlink()
            mark_deleted(_metadata_path(settings),transfer_id)
        return RedirectResponse('/',303)
    return router
