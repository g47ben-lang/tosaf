// Small storage-backed config helpers shared by background.js, popup.js and
// options.js.

import { generateKeyPair, exportPublicKeyB64, exportPrivateKeyJwk, importPrivateKeyJwk } from "./crypto.js";

const DEFAULTS = {
  serverUrl: "http://127.0.0.1:8723",
  encryptDownloads: false,
  lastFormat: "video",
  lastQuality: "best",
  lastAudioLang: "",
  lastSubLang: "",
};

export async function getSettings() {
  const stored = await chrome.storage.local.get(Object.keys(DEFAULTS));
  return { ...DEFAULTS, ...stored };
}

export async function setSettings(partial) {
  await chrome.storage.local.set(partial);
}

/** Returns { publicKeyB64, privateKey } — generates and persists a keypair on first use. */
export async function getOrCreateKeyPair() {
  const { sbPrivateKeyJwk, sbPublicKeyB64 } = await chrome.storage.local.get([
    "sbPrivateKeyJwk",
    "sbPublicKeyB64",
  ]);
  if (sbPrivateKeyJwk && sbPublicKeyB64) {
    const privateKey = await importPrivateKeyJwk(sbPrivateKeyJwk);
    return { publicKeyB64: sbPublicKeyB64, privateKey };
  }
  const { publicKey, privateKey } = await generateKeyPair();
  const publicKeyB64 = await exportPublicKeyB64(publicKey);
  const jwk = await exportPrivateKeyJwk(privateKey);
  await chrome.storage.local.set({ sbPrivateKeyJwk: jwk, sbPublicKeyB64: publicKeyB64 });
  return { publicKeyB64, privateKey };
}
