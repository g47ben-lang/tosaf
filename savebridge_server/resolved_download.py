"""Fetch pre-resolved googlevideo.com CDN URLs directly — no yt-dlp, no PO
Token, no proxy, no TLS impersonation.

How free extensions avoid needing a proxy: they don't do the extraction
from a server at all. They let the *real browser tab* — with its real
cookies, its real Botguard-issued PO Token, its real residential IP and
its real Chrome TLS stack — resolve the playable `googlevideo.com`
URL(s) for the video/audio track. That negotiation is the part YouTube
polices; once it hands back a signed URL, fetching the actual media bytes
from that URL is not IP-locked or re-checked against Botguard the same
way, so any machine can fetch it within the URL's expiry window (a few
hours).

So the extension resolves the URL(s) client-side (e.g. by reading
`ytInitialPlayerResponse.streamingData` from the loaded watch page, or by
sniffing the `*.googlevideo.com/videoplayback` requests the page's own
player already fires) and sends them here instead of a bare video URL.
This module just streams those URLs to disk and muxes with ffmpeg — the
one thing a server is still useful for (a browser tab can't easily
combine a video-only + audio-only stream into one file, or transcode).

This is the primary, proxy-free path. The `url`-based yt-dlp path in
jobs.py (which does need PO Token / proxy / impersonation, since it's the
server itself negotiating with YouTube) stays as a fallback for cases
where the extension couldn't resolve URLs client-side.
"""

from __future__ import annotations

import os
import subprocess
from typing import Callable, Optional

import requests

_CHUNK = 256 * 1024
_TIMEOUT = 30


class ResolveError(RuntimeError):
    pass


class _Cancelled(Exception):
    pass


def _fetch(url: str, headers: Optional[dict], dest: str, on_chunk: Callable[[int, int], None], cancel_check) -> None:
    with requests.get(url, headers=headers or {}, stream=True, timeout=_TIMEOUT) as resp:
        if resp.status_code >= 400:
            raise ResolveError(
                f"השרת של יוטיוב החזיר {resp.status_code} — כנראה שהקישור פג תוקף "
                "(הקישורים תקפים לכמה שעות). נסה שוב מהעמוד."
            )
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with open(dest, "wb") as fh:
            for chunk in resp.iter_content(_CHUNK):
                if cancel_check():
                    raise _Cancelled()
                if not chunk:
                    continue
                fh.write(chunk)
                done += len(chunk)
                on_chunk(done, total)


def _run_ffmpeg(args: list[str]) -> None:
    proc = subprocess.run(["ffmpeg", "-y", *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if proc.returncode != 0:
        tail = proc.stdout.decode(errors="replace")[-2000:]
        raise ResolveError(f"ffmpeg נכשל: {tail}")


def convert_low_phone(src: str, work_dir: str) -> str:
    dest = os.path.join(work_dir, "out_phone.3gp")
    _run_ffmpeg(["-i", src, dest])
    return dest


def download_resolved(
    *,
    work_dir: str,
    fmt: str,
    resolved: dict,
    cancel_check: Callable[[], bool],
    on_progress: Callable[[float], None],
) -> str:
    """resolved shape (sent by the extension, extracted from the real tab):

        {
          "video":    {"url": "...", "ext": "mp4", "headers"?: {...}},  # video-only or progressive (has audio baked in)
          "audio":    {"url": "...", "ext": "m4a", "headers"?: {...}},  # omit if the video stream is progressive
          "subtitle": {"url": "...", "ext": "vtt", "headers"?: {...}},  # optional
        }

    Returns the path to the finished media file.
    """
    video = resolved.get("video")
    audio = resolved.get("audio")
    subtitle = resolved.get("subtitle")

    if fmt == "audio":
        if not audio:
            raise ResolveError("לא נשלח קישור אודיו מהתוסף")
        streams = [("audio", audio)]
    else:
        if not video:
            raise ResolveError("לא נשלח קישור וידאו מהתוסף")
        streams = [("video", video)]
        if audio:
            streams.append(("audio", audio))
    if subtitle:
        streams.append(("subtitle", subtitle))

    n = len(streams)
    paths: dict[str, str] = {}

    for idx, (name, spec) in enumerate(streams):
        ext = spec.get("ext") or {"audio": "m4a", "video": "mp4", "subtitle": "vtt"}[name]
        dest = os.path.join(work_dir, f"src_{name}.{ext}")

        def hook(done: int, total: int, _idx=idx) -> None:
            per_stream = (done / total * 100.0) if total else 0.0
            on_progress(((_idx + min(per_stream, 99.0) / 100.0) / n) * 90.0)

        _fetch(spec["url"], spec.get("headers"), dest, hook, cancel_check)
        paths[name] = dest

    on_progress(92.0)

    if fmt == "audio":
        out = os.path.join(work_dir, "out.mp3")
        _run_ffmpeg(["-i", paths["audio"], "-vn", "-q:a", "0", out])
        return out

    if "audio" in paths:
        args = ["-i", paths["video"], "-i", paths["audio"]]
        maps = ["-map", "0:v:0", "-map", "1:a:0"]
        extra: list[str] = []
        if "subtitle" in paths:
            args += ["-i", paths["subtitle"]]
            maps += ["-map", "2:s:0"]
            extra = ["-c:s", "mov_text"]
        out = os.path.join(work_dir, "out.mp4")
        _run_ffmpeg([*args, *maps, "-c:v", "copy", "-c:a", "copy", *extra, out])
        return out

    if "subtitle" in paths:
        out = os.path.join(work_dir, "out.mp4")
        _run_ffmpeg([
            "-i", paths["video"], "-i", paths["subtitle"],
            "-map", "0", "-map", "1", "-c", "copy", "-c:s", "mov_text", out,
        ])
        return out

    return paths["video"]
