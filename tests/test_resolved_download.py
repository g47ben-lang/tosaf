"""resolved_download.py — the proxy-free path where the extension hands the
server already-signed CDN URLs instead of a bare YouTube URL. Spins up a
throwaway local HTTP server to stand in for googlevideo.com.
"""

import os
import shutil
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from savebridge_server.resolved_download import ResolveError, download_resolved

_HAS_FFMPEG = bool(shutil.which("ffmpeg"))


class _Handler(BaseHTTPRequestHandler):
    routes: dict[str, bytes] = {}

    def do_GET(self):  # noqa: N802
        body = self.routes.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # silence
        pass


def _serve(routes: dict[str, bytes]):
    _Handler.routes = routes
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_missing_video_raises(tmp_path):
    try:
        download_resolved(
            work_dir=str(tmp_path), fmt="video", resolved={},
            cancel_check=lambda: False, on_progress=lambda p: None,
        )
        raise AssertionError("expected ResolveError")
    except ResolveError:
        print("OK: missing video -> ResolveError")


def test_missing_audio_raises(tmp_path):
    try:
        download_resolved(
            work_dir=str(tmp_path), fmt="audio", resolved={},
            cancel_check=lambda: False, on_progress=lambda p: None,
        )
        raise AssertionError("expected ResolveError")
    except ResolveError:
        print("OK: missing audio -> ResolveError")


def test_expired_link_raises(tmp_path):
    server = _serve({})
    try:
        download_resolved(
            work_dir=str(tmp_path), fmt="video",
            resolved={"video": {"url": f"http://127.0.0.1:{server.server_port}/gone.mp4", "ext": "mp4"}},
            cancel_check=lambda: False, on_progress=lambda p: None,
        )
        raise AssertionError("expected ResolveError")
    except ResolveError as e:
        assert "404" in str(e)
        print("OK: 404 from CDN -> clear ResolveError")
    finally:
        server.shutdown()


def test_progressive_video_passthrough(tmp_path):
    """video-only resolved entry, no audio/subtitle -> no ffmpeg call, exact bytes."""
    content = os.urandom(50_000)
    server = _serve({"/v.mp4": content})
    try:
        seen_progress = []
        out = download_resolved(
            work_dir=str(tmp_path), fmt="video",
            resolved={"video": {"url": f"http://127.0.0.1:{server.server_port}/v.mp4", "ext": "mp4"}},
            cancel_check=lambda: False, on_progress=seen_progress.append,
        )
        with open(out, "rb") as fh:
            assert fh.read() == content
        assert seen_progress and max(seen_progress) <= 92.0
        print("OK: progressive video passthrough, no mux needed")
    finally:
        server.shutdown()


def test_audio_only_conversion(tmp_path):
    if not _HAS_FFMPEG:
        print("SKIP: audio conversion (ffmpeg not installed here)")
        return
    # 0.2s of silence, real WAV so ffmpeg can actually decode it.
    import struct
    sr, dur = 8000, 0.2
    n = int(sr * dur)
    pcm = struct.pack("<%dh" % n, *([0] * n))
    wav = (
        b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, sr, sr * 2, 2, 16)
        + b"data" + struct.pack("<I", len(pcm)) + pcm
    )
    server = _serve({"/a.wav": wav})
    try:
        out = download_resolved(
            work_dir=str(tmp_path), fmt="audio",
            resolved={"audio": {"url": f"http://127.0.0.1:{server.server_port}/a.wav", "ext": "wav"}},
            cancel_check=lambda: False, on_progress=lambda p: None,
        )
        assert out.endswith("out.mp3") and os.path.getsize(out) > 0
        print("OK: audio-only resolved -> mp3 via ffmpeg")
    finally:
        server.shutdown()


if __name__ == "__main__":
    import tempfile
    from pathlib import Path

    for fn in (
        test_missing_video_raises,
        test_missing_audio_raises,
        test_expired_link_raises,
        test_progressive_video_passthrough,
        test_audio_only_conversion,
    ):
        fn(Path(tempfile.mkdtemp()))
    print("ALL RESOLVED-DOWNLOAD TESTS PASSED")
