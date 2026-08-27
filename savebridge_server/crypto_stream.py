"""Encryption that exactly matches the extension's decryptor (offscreen.js).

The Chrome extension, when "encryptDownloads" is on, generates an RSA-OAEP
key pair and sends us the public key (SPKI, base64) with each job. We:

  1. generate a random AES-256 key,
  2. wrap (encrypt) that AES key with the client's RSA public key,
  3. stream the file as a sequence of AES-256-GCM "frames".

The extension unwraps the AES key with its private key and decrypts the frames
back into the original file, in memory, then saves it to disk. Nothing but
opaque bytes ever crosses the network.

Wire format (must not change without changing offscreen.js):

  * enc.alg       = "RSA-OAEP-256+AES-256-GCM"
  * enc.wrappedKey= base64(RSA-OAEP-SHA256(aes_key))
  * enc.salt      = base64(8 random bytes)
  * enc.frameSize = plaintext bytes per frame (int)

  stream = ( <uint32 BE ciphertext_len> <ciphertext(+16B GCM tag)> )*  <uint32 0>

  per frame index i (0-based):
    iv  = salt(8 bytes) || i as uint32 big-endian   (12 bytes)
    aad = i as uint32 big-endian                     (4 bytes)
"""

from __future__ import annotations

import base64
import os
import struct
from dataclasses import dataclass
from typing import Iterator

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ALG = "RSA-OAEP-256+AES-256-GCM"
# Plaintext bytes per frame. Must stay well under the extension's 16 MiB hard
# cap; 1 MiB keeps memory tiny and matches the streaming reader on the client.
FRAME_SIZE = 1024 * 1024


@dataclass
class EncSession:
    """Everything needed to encrypt one job's file."""

    aes_key: bytes
    salt: bytes
    wrapped_key_b64: str

    def enc_meta(self) -> dict:
        """The `enc` object the extension reads from the job JSON."""
        return {
            "alg": ALG,
            "wrappedKey": self.wrapped_key_b64,
            "salt": base64.b64encode(self.salt).decode("ascii"),
            "frameSize": FRAME_SIZE,
        }


def new_session(pub_key_b64: str) -> EncSession:
    """Create an AES key and wrap it with the client's RSA public key.

    `pub_key_b64` is base64 of the SPKI DER produced by the extension's
    crypto.subtle.exportKey("spki", ...).
    """
    spki_der = base64.b64decode(pub_key_b64)
    public_key = serialization.load_der_public_key(spki_der)

    aes_key = os.urandom(32)
    salt = os.urandom(8)

    wrapped = public_key.encrypt(
        aes_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    return EncSession(
        aes_key=aes_key,
        salt=salt,
        wrapped_key_b64=base64.b64encode(wrapped).decode("ascii"),
    )


def encrypt_stream(session: EncSession, source: Iterator[bytes]) -> Iterator[bytes]:
    """Turn a stream of plaintext bytes into the framed cipher stream.

    `source` yields arbitrary-sized chunks; we re-chunk to FRAME_SIZE so frame
    indices/IVs line up with what the client expects.
    """
    aesgcm = AESGCM(session.aes_key)
    index = 0
    buffer = bytearray()

    def emit_frame(plaintext: bytes) -> bytes:
        nonlocal index
        iv = session.salt + struct.pack(">I", index)
        aad = struct.pack(">I", index)
        ciphertext = aesgcm.encrypt(iv, plaintext, aad)  # ciphertext || 16B tag
        index += 1
        return struct.pack(">I", len(ciphertext)) + ciphertext

    for chunk in source:
        if not chunk:
            continue
        buffer.extend(chunk)
        while len(buffer) >= FRAME_SIZE:
            frame = bytes(buffer[:FRAME_SIZE])
            del buffer[:FRAME_SIZE]
            yield emit_frame(frame)

    if buffer:
        yield emit_frame(bytes(buffer))

    # zero-length prefix = end of stream
    yield struct.pack(">I", 0)
