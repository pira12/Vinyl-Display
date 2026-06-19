"""FastAPI app: the kiosk display, the live-state websocket, the cached album
art, and the phone companion app + its JSON API."""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .enrollment import EnrollmentService
from .recognition.models import TrackIndex
from .recognition.recognizer import apply_match, publish_album_track
from .settings import SettingsError
from .state import StateManager

log = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


def create_app(state: StateManager, index: TrackIndex,
               enrollment: EnrollmentService, art_dir: str,
               auth_token: Optional[str] = None,
               settings=None, tmp_dir: Optional[str] = None,
               acoustid=None) -> FastAPI:
    tmp_dir = tmp_dir or tempfile.gettempdir()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Let non-async callers (recognition/enrollment) schedule broadcasts.
        state.bind_loop(asyncio.get_running_loop())
        yield

    app = FastAPI(title="Vinyl Display", lifespan=lifespan)

    @app.middleware("http")
    async def require_token(request: Request, call_next):
        """Gate the companion API behind a shared token.

        The kiosk display (``/``, ``/ws``, ``/art``) stays open for local use;
        only the management/control API — which can start audio capture — is
        protected. Token may arrive as an ``X-Auth-Token`` header or ``?token=``.
        """
        if auth_token and request.url.path.startswith("/api"):
            supplied = (request.headers.get("x-auth-token")
                        or request.query_params.get("token") or "")
            if not hmac.compare_digest(supplied, auth_token):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)

    # ---- built single-page app ----
    web_dir = Path(os.environ.get("FRONTEND_DIST", FRONTEND_DIR / "dist"))
    index_file = web_dir / "index.html"

    # Serve index.html with no-cache so a redeploy's new hashed assets are
    # always picked up (the hashed assets themselves can be cached forever).
    _no_cache = {"Cache-Control": "no-cache, must-revalidate"}

    @app.get("/")
    async def index_page():
        if index_file.exists():
            return FileResponse(index_file, headers=_no_cache)
        return JSONResponse({"error": "frontend not built"}, status_code=404)

    @app.get("/manage")
    async def manage_page():
        # Same SPA; the frontend opens straight into Collection mode.
        if index_file.exists():
            return FileResponse(index_file, headers=_no_cache)
        return JSONResponse({"error": "frontend not built"}, status_code=404)

    @app.get("/healthz")
    async def healthz() -> dict:
        from .version import VERSION
        return {"status": "ok", "state": state.status, "version": VERSION}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        state.register(websocket)
        try:
            await websocket.send_json(state.payload())
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            state.unregister(websocket)

    # ---- companion API ----
    @app.get("/api/collection")
    async def api_collection() -> JSONResponse:
        return JSONResponse({
            "albums": enrollment.collection(),
            "recording": enrollment.recording_status(),
        })

    @app.get("/api/search")
    async def api_search(q: str) -> JSONResponse:
        return JSONResponse({"results": await enrollment.search(q)})

    @app.post("/api/albums")
    async def api_add_album(request: Request) -> JSONResponse:
        body = await request.json()
        mbid = (body or {}).get("release_mbid")
        if not mbid:
            return JSONResponse({"error": "release_mbid required"}, status_code=400)
        try:
            album = await enrollment.add_album(mbid)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"album": album, "sides": enrollment.sides_for(mbid)})

    @app.patch("/api/albums/{album_id}")
    async def api_edit_album(album_id: str, request: Request) -> JSONResponse:
        body = await request.json() or {}
        try:
            album = enrollment.update_album(album_id, body)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"album": album})

    @app.delete("/api/albums/{album_id}")
    async def api_delete_album(album_id: str) -> JSONResponse:
        try:
            enrollment.delete_album(album_id)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"deleted": album_id})

    # Enrollment: the iPad mic streams a whole side here as raw PCM chunks.
    @app.post("/api/record/start")
    async def api_record_start(request: Request) -> JSONResponse:
        body = await request.json() or {}
        album_id = body.get("album_id")
        if not album_id:
            return JSONResponse({"error": "album_id required"}, status_code=400)
        side = str(body.get("side", "A"))[:2]
        try:
            enrollment.start_client_recording(album_id, side)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(enrollment.recording_status())

    @app.post("/api/record/chunk")
    async def api_record_chunk(request: Request) -> JSONResponse:
        data = await request.body()
        if not data:
            return JSONResponse({"error": "empty chunk"}, status_code=400)
        try:
            received = enrollment.append_chunk(data)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"received_bytes": received})

    @app.post("/api/record/stop")
    async def api_record_stop() -> JSONResponse:
        try:
            # Fingerprinting (olaf subprocess + WAV write) is blocking.
            result = await asyncio.to_thread(enrollment.stop_client_recording)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"result": result})

    @app.post("/api/record/cancel")
    async def api_record_cancel() -> JSONResponse:
        enrollment.cancel_client_recording()
        return JSONResponse(enrollment.recording_status())

    @app.get("/api/record/status")
    async def api_record_status() -> JSONResponse:
        return JSONResponse(enrollment.recording_status())

    # Recognition: the iPad mic posts a short WAV clip; we fingerprint it here.
    @app.post("/api/recognize")
    async def api_recognize(request: Request) -> JSONResponse:
        if not state.listening:
            return JSONResponse({"status": "paused", "matched": False})
        data = await request.body()
        if not data:
            return JSONResponse({"error": "no audio"}, status_code=400)
        resolved = await asyncio.to_thread(_recognize_clip, enrollment, index,
                                           state, tmp_dir, data)
        return JSONResponse({
            "status": state.status,
            "matched": resolved is not None,
            "track": state.track,
        })

    # Auto-label: identify an unknown record via AcoustID and cache its album,
    # so the user doesn't have to search for it. Best-effort from a room mic.
    @app.post("/api/identify")
    async def api_identify(request: Request) -> JSONResponse:
        if acoustid is None or not acoustid.available:
            return JSONResponse({"available": False, "match": None})
        data = await request.body()
        if not data:
            return JSONResponse({"error": "no audio"}, status_code=400)
        clip = Path(tmp_dir) / "vinyl-identify.wav"
        await asyncio.to_thread(clip.write_bytes, data)
        match = await acoustid.identify(str(clip))
        if not match:
            return JSONResponse({"available": True, "match": None})
        try:
            album = await enrollment.add_album(match["release_mbid"])
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"available": True, "match": match,
                                 "error": str(exc)})
        # Show the identified track on the display straight away (best-effort,
        # no in-track position from AcoustID).
        album_obj = index.albums.get(match["release_mbid"])
        if album_obj:
            want = (match.get("title") or "").lower()
            idx = next((i for i, t in enumerate(album_obj.tracklist)
                        if want and want in (t.title or "").lower()), 0)
            publish_album_track(state, album_obj, idx)
        return JSONResponse({"available": True, "match": match, "album": album,
                             "sides": enrollment.sides_for(match["release_mbid"])})

    # ---- listening control ----
    @app.post("/api/listen")
    async def api_listen(request: Request) -> JSONResponse:
        body = await request.json() or {}
        if "enabled" not in body or not isinstance(body["enabled"], bool):
            return JSONResponse({"error": "enabled (bool) required"}, status_code=400)
        state.set_listening(body["enabled"])
        return JSONResponse({"listening": state.listening})

    # ---- settings ----
    @app.get("/api/settings")
    async def api_get_settings() -> JSONResponse:
        if settings is None:
            return JSONResponse({"error": "settings unavailable"}, status_code=404)
        return JSONResponse(settings.snapshot())

    @app.post("/api/settings")
    async def api_post_settings(request: Request) -> JSONResponse:
        if settings is None:
            return JSONResponse({"error": "settings unavailable"}, status_code=404)
        body = await request.json() or {}
        changes = body.get("changes") if isinstance(body.get("changes"), dict) else body
        if not isinstance(changes, dict) or not changes:
            return JSONResponse({"error": "no settings supplied"}, status_code=400)
        try:
            result = settings.update(changes)
        except SettingsError as exc:
            return JSONResponse({"error": str(exc), "fields": exc.errors},
                                status_code=400)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(result)

    # ---- static assets ----
    art_path = Path(art_dir)
    art_path.mkdir(parents=True, exist_ok=True)
    app.mount("/art", StaticFiles(directory=str(art_path)), name="art")

    # Serve the built SPA last so /api, /ws, /art, /manage take precedence.
    # html=True serves index.html for "/" and the hashed assets, favicon,
    # manifest, and the audio worklet straight from dist/.
    if index_file.exists():
        app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="spa")

    return app


def _recognize_clip(enrollment: EnrollmentService, index: TrackIndex,
                    state: StateManager, tmp_dir: str, wav: bytes):
    """Blocking: write the uploaded WAV, query Olaf, update shared state.

    Runs off the event loop (olaf is a subprocess). The clip is already a WAV
    encoded by the browser, so it's handed straight to the backend's query.
    """
    clip = Path(tmp_dir) / "vinyl-clip.wav"
    clip.write_bytes(wav)
    match = enrollment.backend.query(str(clip))
    return apply_match(state, index, match)
