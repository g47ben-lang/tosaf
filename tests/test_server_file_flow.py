"""End-to-end server plumbing test (no real YouTube download).

We inject a finished Job into the manager, then drive the HTTP endpoints the
extension uses and verify the encrypted file stream decrypts to the original.
"""

import base64
import os
import struct
import sys

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from savebridge_server import jobs as jobs_mod
from savebridge_server.crypto_stream import new_session
from savebridge_server.jobs import COMPLETED, Job
from savebridge_server.server import app, jobs


def _client_keypair():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    spki = priv.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return priv, base64.b64encode(spki).decode()


def _decrypt(priv, enc, stream):
    raw = priv.decrypt(
        base64.b64decode(enc["wrappedKey"]),
        padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    aesgcm = AESGCM(raw)
    salt = base64.b64decode(enc["salt"])
    out, pos, idx = bytearray(), 0, 0
    while True:
        (n,) = struct.unpack(">I", stream[pos : pos + 4]); pos += 4
        if n == 0:
            break
        chunk = stream[pos : pos + n]; pos += n
        out.extend(aesgcm.decrypt(salt + struct.pack(">I", idx), chunk, struct.pack(">I", idx)))
        idx += 1
    return bytes(out)


def _install_completed_job(tmp_path, content, enc=None) -> Job:
    work = str(tmp_path)
    fpath = os.path.join(work, "clip.mp4")
    with open(fpath, "wb") as fh:
        fh.write(content)
    job = Job(id="testjob", title="clip", fmt="video", quality="best",
              status=COMPLETED, percent=100.0, file_name="clip.mp4",
              file_path=fpath, enc=enc, work_dir=work)
    jobs._jobs[job.id] = job
    return job


def test_encrypted_file_endpoint(tmp_path):
    priv, pub = _client_keypair()
    content = os.urandom(1_300_000)
    _install_completed_job(tmp_path, content, enc=new_session(pub))

    c = TestClient(app)
    meta = c.get("/api/jobs/testjob").json()
    assert meta["status"] == "completed" and meta["hasFile"] is True
    assert meta["enc"]["alg"] == "RSA-OAEP-256+AES-256-GCM"

    stream = c.get("/api/jobs/testjob/file").content
    assert _decrypt(priv, meta["enc"], stream) == content
    # work dir cleaned after the file is served
    assert not os.path.isdir(str(tmp_path))
    print("OK: encrypted file endpoint + cleanup")


def test_plain_file_endpoint(tmp_path):
    content = os.urandom(50_000)
    _install_completed_job(tmp_path, content, enc=None)

    c = TestClient(app)
    meta = c.get("/api/jobs/testjob").json()
    assert meta["enc"] is None
    body = c.get("/api/jobs/testjob/file").content
    assert body == content
    print("OK: plain file endpoint")


if __name__ == "__main__":
    import tempfile
    for fn in (test_plain_file_endpoint, test_encrypted_file_endpoint):
        d = tempfile.mkdtemp()
        jobs._jobs.clear()
        fn(__import__("pathlib").Path(d))
    print("ALL SERVER FLOW TESTS PASSED")
