// Matches savebridge_server/crypto_stream.py exactly. Do not change the
// frame format here without changing it there too.
//
//   enc.alg        = "RSA-OAEP-256+AES-256-GCM"
//   enc.wrappedKey = base64(RSA-OAEP-SHA256(aes_key))
//   enc.salt       = base64(8 random bytes)
//
//   stream = ( <uint32 BE ciphertext_len> <ciphertext(+16B GCM tag)> )*  <uint32 0>
//   frame i: iv = salt(8B) || i as uint32 BE (12B); aad = i as uint32 BE (4B)

export function b64ToBytes(b64) {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export function bytesToB64(bytes) {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}

function u32be(n) {
  const b = new Uint8Array(4);
  new DataView(b.buffer).setUint32(0, n, false);
  return b;
}

function concatBytes(a, b) {
  const out = new Uint8Array(a.length + b.length);
  out.set(a, 0);
  out.set(b, a.length);
  return out;
}

export async function generateKeyPair() {
  return crypto.subtle.generateKey(
    { name: "RSA-OAEP", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" },
    true,
    ["encrypt", "decrypt"],
  );
}

export async function exportPublicKeyB64(publicKey) {
  const spki = await crypto.subtle.exportKey("spki", publicKey);
  return bytesToB64(new Uint8Array(spki));
}

export async function exportPrivateKeyJwk(privateKey) {
  return crypto.subtle.exportKey("jwk", privateKey);
}

export async function importPrivateKeyJwk(jwk) {
  return crypto.subtle.importKey(
    "jwk", jwk, { name: "RSA-OAEP", hash: "SHA-256" }, true, ["decrypt"],
  );
}

/**
 * Decrypt the framed stream from `response` (a fetch Response whose body is
 * the wire format above) using the extension's RSA private key + the `enc`
 * metadata the server returned alongside the job. Returns a Blob of the
 * decrypted file.
 */
export async function decryptStreamToBlob(response, enc, privateKey) {
  const wrappedKey = b64ToBytes(enc.wrappedKey);
  const salt = b64ToBytes(enc.salt);
  const rawAesKey = await crypto.subtle.decrypt({ name: "RSA-OAEP" }, privateKey, wrappedKey);
  const aesKey = await crypto.subtle.importKey("raw", rawAesKey, { name: "AES-GCM" }, false, ["decrypt"]);

  const reader = response.body.getReader();
  let buf = new Uint8Array(0);
  const chunks = [];
  let index = 0;
  let ended = false;

  while (!ended) {
    const { value, done } = await reader.read();
    if (value && value.length) buf = concatBytes(buf, value);

    while (true) {
      if (buf.length < 4) break;
      const len = new DataView(buf.buffer, buf.byteOffset, 4).getUint32(0, false);
      if (len === 0) {
        ended = true;
        break;
      }
      if (buf.length < 4 + len) break;
      const frame = buf.slice(4, 4 + len);
      buf = buf.slice(4 + len);
      const iv = concatBytes(salt, u32be(index));
      const plain = await crypto.subtle.decrypt(
        { name: "AES-GCM", iv, additionalData: u32be(index), tagLength: 128 },
        aesKey,
        frame,
      );
      chunks.push(plain);
      index += 1;
    }

    if (done && !ended) {
      throw new Error("הזרם נגמר לפני פריים הסיום — כנראה שההורדה נקטעה.");
    }
  }

  return new Blob(chunks);
}
