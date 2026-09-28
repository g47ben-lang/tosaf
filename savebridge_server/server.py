"""SaveBridge personal server.

A drop-in replacement for the extension's remote backend. Implements the six
endpoints the extension calls, runs yt-dlp + ffmpeg locally, and (optionally)
streams the result back encrypted in the exact frame format the extension's
offscreen decryptor expects.

Run locally:
    uvicorn savebridge_server.server:app --host 127.0.0.1 --port 8723

Point the extension's SERVER_URL at http://127.0.0.1:8723 (local) or at your
https domain (remote, behind Caddy).
"""

from __future__ import annotations

import os
import shutil
import mimetypes

import yt_dlp
from fastapi import FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from .crypto_stream import encrypt_stream
from .jobs import JobManager

app = FastAPI(title="SaveBridge personal server")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

jobs = JobManager()

# This is your own server, so any non-empty bearer token is fine. The token is
# handed out by /api/hello and echoed back by the extension; we don't gate on
# its value, we only use its presence to mirror the original protocol.
STATIC_TOKEN = os.environ.get("SAVEBRIDGE_TOKEN", "local-personal-token")

_FILE_CHUNK = 256 * 1024


@app.post("/api/hello")
async def hello() -> JSONResponse:
    return JSONResponse({"token": STATIC_TOKEN})


@app.get("/api/ping")
async def ping() -> JSONResponse:
    from .jobs import _impersonate_from_env, _player_clients_from_env, _proxy_from_env

    return JSONResponse(
        {
            "ytDlp": getattr(yt_dlp.version, "__version__", "unknown"),
            "ffmpeg": bool(shutil.which("ffmpeg")),
            "proxy": bool(_proxy_from_env()),
            "impersonate": _impersonate_from_env(),
            "playerClients": _player_clients_from_env(),
        }
    )


@app.post("/api/start")
async def start(request: Request) -> JSONResponse:
    payload = await request.json()
    resolved = payload.get("resolved") or {}
    if not payload.get("url") and not (resolved.get("video") or resolved.get("audio")):
        return JSONResponse({"error": "missing url or resolved.video/resolved.audio"}, status_code=400)
    job_id = jobs.start(payload)
    return JSONResponse({"jobId": job_id})


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> JSONResponse:
    job = jobs.get(job_id)
    if not job:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(job.public())


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str) -> JSONResponse:
    ok = jobs.cancel(job_id)
    if not ok:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({"ok": True})


@app.get("/api/jobs/{job_id}/file")
async def get_file(job_id: str):
    job = jobs.get(job_id)
    if not job or not job.file_path or not os.path.exists(job.file_path):
        return JSONResponse({"error": "file not ready"}, status_code=404)

    path = job.file_path
    file_name = job.file_name or "video"

    def read_file():
        try:
            with open(path, "rb") as fh:
                while True:
                    chunk = fh.read(_FILE_CHUNK)
                    if not chunk:
                        break
                    yield chunk
        finally:
            # one download per job: clean up the work dir once bytes are served
            jobs.cleanup(job)

    if job.enc:
        body = encrypt_stream(job.enc, read_file())
        media_type = "application/octet-stream"
    else:
        body = read_file()
        media_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"

    headers = {"Content-Disposition": f'attachment; filename="{file_name}"'}
    return StreamingResponse(body, media_type=media_type, headers=headers)


@app.get("/")
async def root() -> JSONResponse:
    return JSONResponse({"service": "savebridge", "ok": True})
