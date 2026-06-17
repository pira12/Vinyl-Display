"""FastAPI app: serves the kiosk frontend and a websocket of live state."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .state import StateManager

log = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


def create_app(state: StateManager) -> FastAPI:
    app = FastAPI(title="Vinyl Display")

    @app.get("/")
    async def index() -> FileResponse:  # noqa: D401
        return FileResponse(FRONTEND_DIR / "index.html")

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok", "state": state.status}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        state.register(websocket)
        try:
            await websocket.send_json(state.payload())   # prime the client
            while True:
                # We don't expect inbound messages; this keeps the socket open
                # and lets us notice disconnects.
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            state.unregister(websocket)

    if FRONTEND_DIR.exists():
        app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    return app
