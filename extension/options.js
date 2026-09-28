import { getSettings, setSettings, getOrCreateKeyPair } from "./lib/config.js";

const serverUrlEl = document.getElementById("serverUrl");
const encryptEl = document.getElementById("encryptDownloads");
const savedMsg = document.getElementById("savedMsg");

async function init() {
  const s = await getSettings();
  serverUrlEl.value = s.serverUrl;
  encryptEl.checked = s.encryptDownloads;
}

document.getElementById("save").addEventListener("click", async () => {
  const serverUrl = serverUrlEl.value.trim().replace(/\/$/, "") || "http://127.0.0.1:8723";
  const encryptDownloads = encryptEl.checked;
  await setSettings({ serverUrl, encryptDownloads });
  if (encryptDownloads) {
    // make sure a keypair exists so the first download doesn't stall on it
    await getOrCreateKeyPair();
  }
  savedMsg.classList.remove("hidden");
  setTimeout(() => savedMsg.classList.add("hidden"), 2000);
});

init();
