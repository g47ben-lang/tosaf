"""POST /api/start must accept a `resolved`-only payload (no `url`) — that's
what the extension's proxy-free path sends. Regression test for the bug
where /api/start unconditionally required `url` and rejected every
resolved-only request with 400 before it ever reached JobManager.
"""

import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from savebridge_server.jobs import COMPLETED
from savebridge_server.server import app, jobs


class _Handler(BaseHTTPRequestHandler):
    content = b"stub-video-bytes"

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.content)))
        self.end_headers()
        self.wfile.write(self.content)

    def log_message(self, *a):
        pass


def _serve():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_missing_url_and_resolved_rejected():
    c = TestClient(app)
    resp = c.post("/api/start", json={"title": "x", "format": "video"})
    assert resp.status_code == 400
    print("OK: /api/start rejects payload with neither url nor resolved")


def test_resolved_only_payload_accepted_and_completes():
    jobs._jobs.clear()
    server = _serve()
    try:
        c = TestClient(app)
        resp = c.post(
            "/api/start",
            json={
                "title": "clip",
                "format": "video",
                "quality": "best",
                "resolved": {"video": {"url": f"http://127.0.0.1:{server.server_port}/v.mp4", "ext": "mp4"}},
            },
        )
        assert resp.status_code == 200, resp.text
        job_id = resp.json()["jobId"]

        job = None
        for _ in range(50):
            job = jobs.get(job_id)
            if job.status in (COMPLETED, "failed"):
                break
            time.sleep(0.1)

        assert job.status == COMPLETED, job.error
        with open(job.file_path, "rb") as fh:
            assert fh.read() == _Handler.content
        print("OK: /api/start with resolved-only payload -> job completes end to end")
    finally:
        server.shutdown()


if __name__ == "__main__":
    test_missing_url_and_resolved_rejected()
    test_resolved_only_payload_accepted_and_completes()
    print("ALL START-ENDPOINT TESTS PASSED")
