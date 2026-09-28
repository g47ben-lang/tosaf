"""In-memory job manager: one download = one background thread running yt-dlp."""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from typing import Optional

import yt_dlp

from .crypto_stream import EncSession, new_session
from .resolved_download import ResolveError, convert_low_phone, download_resolved
from .ytdlp_opts import build_ydl_opts

# status values the extension understands
PREPARING = "preparing"
DOWNLOADING = "downloading"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"

_EXT_BY_FORMAT = {"audio": "mp3", "low_phone": "3gp"}

# Anti-bot-detection config, read fresh from the environment on every
# download so a systemd `Environment=` change just needs a restart, no
# code change. See README for what each one does.
_DEFAULT_PLAYER_CLIENTS = "web,ios,android"


def _proxy_from_env() -> Optional[str]:
    return os.environ.get("SAVEBRIDGE_PROXY_URL") or None


def _impersonate_from_env() -> Optional[str]:
    return os.environ.get("SAVEBRIDGE_IMPERSONATE", "chrome") or None


def _player_clients_from_env() -> Optional[list[str]]:
    raw = os.environ.get("SAVEBRIDGE_PLAYER_CLIENTS", _DEFAULT_PLAYER_CLIENTS)
    clients = [c.strip() for c in raw.split(",") if c.strip()]
    return clients or None


def _human_speed(bps: Optional[float]) -> Optional[str]:
    if not bps or bps <= 0:
        return None
    units = ["B/s", "KB/s", "MB/s", "GB/s"]
    value = float(bps)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= 1024
    return None


def _human_eta(seconds: Optional[float]) -> Optional[str]:
    if seconds is None:
        return None
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return None
    m, s = divmod(max(seconds, 0), 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


class _Cancelled(Exception):
    pass


@dataclass
class Job:
    id: str
    title: str
    fmt: str
    quality: str
    status: str = PREPARING
    percent: float = 0.0
    speed: Optional[str] = None
    eta: Optional[str] = None
    error: Optional[dict] = None
    file_name: Optional[str] = None
    file_path: Optional[str] = None
    enc: Optional[EncSession] = None
    work_dir: str = ""
    _cancel: threading.Event = field(default_factory=threading.Event)

    def public(self) -> dict:
        """The JSON shape the extension polls for on /api/jobs/{id}."""
        return {
            "id": self.id,
            "title": self.title,
            "format": self.fmt,
            "quality": self.quality,
            "status": self.status,
            "percent": round(self.percent, 1),
            "speed": self.speed,
            "eta": self.eta,
            "error": self.error,
            "fileName": self.file_name,
            "hasFile": self.status == COMPLETED and bool(self.file_path),
            "enc": self.enc.enc_meta() if self.enc else None,
        }


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if not job:
            return False
        job._cancel.set()
        return True

    def start(self, payload: dict) -> str:
        job_id = uuid.uuid4().hex
        work_dir = tempfile.mkdtemp(prefix=f"sb_{job_id}_")

        enc: Optional[EncSession] = None
        pub_key = payload.get("pubKey")
        if pub_key:
            enc = new_session(pub_key)

        job = Job(
            id=job_id,
            title=(payload.get("title") or "video").strip() or "video",
            fmt=payload.get("format") or "video",
            quality=str(payload.get("quality") or "best"),
            enc=enc,
            work_dir=work_dir,
        )
        with self._lock:
            self._jobs[job_id] = job

        thread = threading.Thread(target=self._run, args=(job, payload), daemon=True)
        thread.start()
        return job_id

    # -- internals ---------------------------------------------------------

    def _run(self, job: Job, payload: dict) -> None:
        cookiefile = self._write_cookies(job, payload.get("cookies"))
        try:
            self._download(job, payload, cookiefile)
        except _Cancelled:
            job.status = CANCELLED
        except Exception as err:  # noqa: BLE001 - surface any failure to the client
            job.status = FAILED
            job.error = {
                "message": "ההורדה נכשלה.",
                "messageEn": f"Download failed: {err}",
            }
        finally:
            if cookiefile and os.path.exists(cookiefile):
                os.remove(cookiefile)

    def _write_cookies(self, job: Job, cookies: Optional[str]) -> Optional[str]:
        if not cookies:
            return None
        path = os.path.join(job.work_dir, "cookies.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(cookies)
        return path

    def _progress_hook(self, job: Job):
        def hook(d: dict) -> None:
            if job._cancel.is_set():
                raise _Cancelled()
            status = d.get("status")
            if status == "downloading":
                job.status = DOWNLOADING
                downloaded = d.get("downloaded_bytes") or 0
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                if total:
                    job.percent = min(99.0, downloaded / total * 100.0)
                job.speed = _human_speed(d.get("speed"))
                job.eta = _human_eta(d.get("eta"))
            elif status == "finished":
                # a stream finished; merge/postprocess still to come
                job.percent = 99.0
                job.speed = None
                job.eta = None

        return hook

    def _download(self, job: Job, payload: dict, cookiefile: Optional[str]) -> None:
        job.status = DOWNLOADING
        resolved = payload.get("resolved")
        if resolved:
            # Fast path: the extension already resolved playable CDN URLs
            # from inside the real tab (real cookies/PO Token/IP/TLS), so
            # this is a plain HTTP fetch + ffmpeg mux — no yt-dlp, no
            # proxy needed. See resolved_download.py for why that's safe.
            final = self._download_resolved(job, resolved)
        else:
            final = self._download_ytdlp(job, payload, cookiefile)

        if job._cancel.is_set():
            raise _Cancelled()
        if not final:
            raise RuntimeError("output file not found after download")

        job.file_path = final
        job.file_name = os.path.basename(final)
        job.percent = 100.0
        job.speed = None
        job.eta = None
        job.status = COMPLETED

    def _download_resolved(self, job: Job, resolved: dict) -> str:
        def on_progress(pct: float) -> None:
            job.percent = min(99.0, pct)

        try:
            final = download_resolved(
                work_dir=job.work_dir,
                fmt=job.fmt,
                resolved=resolved,
                cancel_check=job._cancel.is_set,
                on_progress=on_progress,
            )
        except ResolveError as err:
            raise RuntimeError(str(err)) from err

        if job.fmt == "low_phone":
            final = convert_low_phone(final, job.work_dir)
        return final

    def _download_ytdlp(self, job: Job, payload: dict, cookiefile: Optional[str]) -> Optional[str]:
        outtmpl = os.path.join(job.work_dir, "%(title).150B.%(ext)s")
        opts = build_ydl_opts(
            fmt=job.fmt,
            quality=job.quality,
            audio_lang=payload.get("audioLang"),
            sub_lang=payload.get("subLang"),
            outtmpl=outtmpl,
            cookiefile=cookiefile,
            progress_hook=self._progress_hook(job),
            proxy=_proxy_from_env(),
            impersonate=_impersonate_from_env(),
            player_clients=_player_clients_from_env(),
        )

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(payload["url"], download=True)

        if job._cancel.is_set():
            raise _Cancelled()

        return self._resolve_output(job, info)

    def _resolve_output(self, job: Job, info: dict) -> Optional[str]:
        """Pick the finished media file out of the job dir.

        Post-processing (mp3/3gp conversion, subtitle/thumb embedding) rewrites
        extensions, so we look for the expected final extension rather than
        trusting yt-dlp's pre-processing filename.
        """
        want_ext = _EXT_BY_FORMAT.get(job.fmt, "mp4")
        candidates = []
        for name in os.listdir(job.work_dir):
            if name in ("cookies.txt",):
                continue
            path = os.path.join(job.work_dir, name)
            if not os.path.isfile(path):
                continue
            if name.endswith((".part", ".ytdl", ".temp")):
                continue
            candidates.append(path)

        exact = [p for p in candidates if p.lower().endswith(f".{want_ext}")]
        pool = exact or candidates
        if not pool:
            return None
        # the largest remaining file is the media output, not a leftover thumb
        return max(pool, key=lambda p: os.path.getsize(p))

    def cleanup(self, job: Job) -> None:
        if job.work_dir and os.path.isdir(job.work_dir):
            shutil.rmtree(job.work_dir, ignore_errors=True)
