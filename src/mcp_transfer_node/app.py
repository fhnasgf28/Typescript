from __future__ import annotations
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware
from mcp_transfer_node.api import create_api_router
from mcp_transfer_node.config import TransferSettings, load_settings
from mcp_transfer_node.files import ensure_runtime_dirs
from mcp_transfer_node.logging_config import configure_logging
from mcp_transfer_node.responses import error_response
from mcp_transfer_node.web import create_web_router
def create_app(settings:TransferSettings|None=None)->FastAPI:
    s=settings or load_settings(); ensure_runtime_dirs(s); configure_logging(s.logs_dir); app=FastAPI(title='MCP Transfer Node')
    app.add_middleware(SessionMiddleware, secret_key=s.session_secret, max_age=12*60*60)
    @app.exception_handler(HTTPException)
    async def http_exception_handler(_, exc:HTTPException):
        if isinstance(exc.detail,dict) and 'success' in exc.detail: return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(status_code=exc.status_code, content=error_response('HTTP_ERROR',str(exc.detail)))
    app.include_router(create_api_router(s)); app.include_router(create_web_router(s)); return app
def run()->None: uvicorn.run('mcp_transfer_node.app:create_app', factory=True, host='127.0.0.1', port=8787)
