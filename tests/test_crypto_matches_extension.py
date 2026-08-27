"""Round-trip test: encrypt with the server, decrypt with a faithful re-impl of
the extension's offscreen.js, and assert we get the original bytes back.

If this passes, the wire format the server emits is exactly what the installed
extension will accept.
"""

import base64
import os
import struct
import sys

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from savebridge_server.crypto_stream import ALG, new_session, encrypt_stream


def make_client_keypair():
    """Mirror the extension: RSA-OAEP 2048, SHA-256, exported as SPKI base64."""
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    spki = priv.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return priv, base64.b64encode(spki).decode("ascii")


def decrypt_like_offscreen(priv, enc, stream: bytes) -> bytes:
    """Faithful port of decryptFramedToBlob() from offscreen.js."""
    assert enc["alg"] == ALG

    raw_aes = priv.decrypt(
        base64.b64decode(enc["wrappedKey"]),
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    aesgcm = AESGCM(raw_aes)
    salt = base64.b64decode(enc["salt"])

    out = bytearray()
    pos = 0
    index = 0
    while True:
        (length,) = struct.unpack(">I", stream[pos : pos + 4])
        pos += 4
        if length == 0:
            break
        assert length >= 16
        payload = stream[pos : pos + length]
        pos += length
        iv = salt + struct.pack(">I", index)
        aad = struct.pack(">I", index)
        out.extend(aesgcm.decrypt(iv, payload, aad))
        index += 1
    return bytes(out)


def test_roundtrip_multiframe():
    priv, pub_b64 = make_client_keypair()
    session = new_session(pub_b64)

    # ~2.5 MiB so we exercise several frames plus a partial last frame
    original = os.urandom(2_500_003)

    def source():
        # feed in oddly-sized chunks to prove re-chunking is correct
        for i in range(0, len(original), 7000):
            yield original[i : i + 7000]

    stream = b"".join(encrypt_stream(session, source()))
    recovered = decrypt_like_offscreen(priv, session.enc_meta(), stream)

    assert recovered == original
    print("OK: multiframe round-trip, %d bytes" % len(original))


def test_roundtrip_small():
    priv, pub_b64 = make_client_keypair()
    session = new_session(pub_b64)
    original = b"hello savebridge"
    stream = b"".join(encrypt_stream(session, [original]))
    recovered = decrypt_like_offscreen(priv, session.enc_meta(), stream)
    assert recovered == original
    print("OK: small round-trip")


if __name__ == "__main__":
    test_roundtrip_small()
    test_roundtrip_multiframe()
    print("ALL CRYPTO TESTS PASSED")
