from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from starlette.staticfiles import StaticFiles

from mcp_transfer_node.api import create_api_router
from mcp_transfer_node.config import TransferSettings, load_settings
from mcp_transfer_node.files import ensure_runtime_dirs
from mcp_transfer_node.logging_config import configure_logging
from mcp_transfer_node.pmt_api import create_pmt_api_router
from mcp_transfer_node.pmt_web import create_pmt_web_router
from mcp_transfer_node.responses import error_response
from mcp_transfer_node.web import create_web_router

REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}


def create_app(settings: TransferSettings | None = None) -> FastAPI:
    resolved = settings or load_settings()
    ensure_runtime_dirs(resolved)
    configure_logging(resolved.logs_dir)
    app = FastAPI(title="MCP Transfer Node")
    app.add_middleware(SessionMiddleware, secret_key=resolved.session_secret, max_age=12 * 60 * 60)
    app.mount(
        "/static",
        StaticFiles(directory=str(Path(__file__).parent / "static")),
        name="static",
    )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_, exc: HTTPException):
        location = (exc.headers or {}).get("Location")
        if location is not None and exc.status_code in REDIRECT_STATUS_CODES:
            return RedirectResponse(location, status_code=exc.status_code)

        if isinstance(exc.detail, dict) and "success" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)

        return JSONResponse(
            status_code=exc.status_code,
            content=error_response("HTTP_ERROR", str(exc.detail)),
        )

    app.include_router(create_api_router(resolved))
    app.include_router(create_pmt_api_router(resolved))
    app.include_router(create_pmt_web_router(resolved))
    app.include_router(create_web_router(resolved))
    return app


def run() -> None:
    host = os.environ.get("MCP_TRANSFER_BIND_HOST", "127.0.0.1")
    port = int(os.environ.get("MCP_TRANSFER_BIND_PORT", "8787"))
    uvicorn.run("mcp_transfer_node.app:create_app", factory=True, host=host, port=port)
