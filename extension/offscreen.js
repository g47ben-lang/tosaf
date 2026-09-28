// Offscreen document: the one place with a DOM, so it's where we build the
// decrypted Blob and hand it to chrome.downloads. Decryption itself matches
// savebridge_server/crypto_stream.py bit-for-bit — see lib/crypto.js.

import { decryptStreamToBlob, importPrivateKeyJwk } from "./lib/crypto.js";

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg?.type !== "sb-decrypt-and-download") return false;
  handle(msg).then(sendResponse).catch((e) => sendResponse({ ok: false, error: String(e) }));
  return true;
});

async function handle({ fileUrl, enc, fileName }) {
  const { sbPrivateKeyJwk } = await chrome.storage.local.get("sbPrivateKeyJwk");
  if (!sbPrivateKeyJwk) throw new Error("לא נמצא מפתח פרטי — הפעל את ההצפנה מחדש בהגדרות");
  const privateKey = await importPrivateKeyJwk(sbPrivateKeyJwk);

  const resp = await fetch(fileUrl);
  if (!resp.ok) throw new Error(`הורדת הקובץ המוצפן נכשלה (${resp.status})`);

  const blob = await decryptStreamToBlob(resp, enc, privateKey);
  const url = URL.createObjectURL(blob);
  try {
    await chrome.downloads.download({ url, filename: fileName || "video", saveAs: false });
  } finally {
    // give the download manager a moment to read the blob before revoking
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  }
  return { ok: true };
}
