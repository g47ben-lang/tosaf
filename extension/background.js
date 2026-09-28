// SaveBridge background service worker.
//
// Flow: read the current tab's already-resolved player formats (the tab has
// real cookies + a real Botguard PO Token + a real IP already — see
// savebridge_server README, "מסלול 1"), send the chosen ones to the local
// server as `resolved`, poll the job, then save the finished file. Falls
// back to sending cookies + the page URL (server-side yt-dlp, needs a
// proxy configured server-side) only when nothing usable could be resolved
// client-side.

import { getSettings, getOrCreateKeyPair } from "./lib/config.js";

const activeJobs = new Map(); // jobId -> { tabId, serverUrl }

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg?.type === "sb-inspect-tab") {
    inspectTab(msg.tabId).then(sendResponse).catch((e) => sendResponse({ error: String(e) }));
    return true;
  }
  if (msg?.type === "sb-start-download") {
    startDownload(msg.tabId, msg.opts).then(sendResponse).catch((e) => sendResponse({ error: String(e) }));
    return true;
  }
  if (msg?.type === "sb-cancel-job") {
    cancelJob(msg.jobId).then(sendResponse).catch((e) => sendResponse({ error: String(e) }));
    return true;
  }
  return false;
});

// ---- page-context extraction -----------------------------------------

/** Runs INSIDE the YouTube tab's own page context (not isolated world), so
 * it sees window.ytInitialPlayerResponse exactly as the real player does.
 * Must be fully self-contained: no closures over outer variables.
 */
function extractYouTubeFormats() {
  try {
    const pr = window.ytInitialPlayerResponse;
    if (!pr || !pr.streamingData) return { error: "no_player_response" };
    const videoDetails = pr.videoDetails || {};
    const sd = pr.streamingData;

    const toEntry = (f) => {
      const mime = f.mimeType || "";
      const extMatch = mime.match(/\/([a-zA-Z0-9]+)/);
      return {
        itag: f.itag,
        ext: extMatch ? extMatch[1] : "mp4",
        height: f.height || null,
        bitrate: f.bitrate || 0,
        hasAudio: !!f.audioQuality || mime.startsWith("audio/"),
        hasVideo: !!f.width || mime.startsWith("video/"),
        audioLang: (f.audioTrack && (f.audioTrack.id || f.audioTrack.displayName)) || null,
        url: f.url || null,
        ciphered: !f.url && !!(f.signatureCipher || f.cipher),
      };
    };

    const progressive = (sd.formats || []).map(toEntry);
    const adaptive = (sd.adaptiveFormats || []).map(toEntry);
    const capList =
      (pr.captions && pr.captions.playerCaptionsTracklistRenderer && pr.captions.playerCaptionsTracklistRenderer.captionTracks) || [];
    const captions = capList.map((c) => ({
      lang: c.languageCode,
      name: (c.name && c.name.simpleText) || c.languageCode,
      url: c.baseUrl,
    }));

    const thumbs = (videoDetails.thumbnail && videoDetails.thumbnail.thumbnails) || [];

    return {
      title: videoDetails.title || document.title,
      videoId: videoDetails.videoId || null,
      thumbnail: thumbs.length ? thumbs[thumbs.length - 1].url : null,
      progressive,
      adaptive,
      captions,
    };
  } catch (e) {
    return { error: String(e && e.message ? e.message : e) };
  }
}

async function inspectTab(tabId) {
  const [{ result } = {}] = await chrome.scripting.executeScript({
    target: { tabId },
    world: "MAIN",
    func: extractYouTubeFormats,
  });
  return result || { error: "injection_failed" };
}

// ---- format selection ---------------------------------------------------

function pickResolved(info, { format, quality, audioLang, subLang }) {
  const usable = (arr) => (arr || []).filter((f) => f.url && !f.ciphered);
  const prog = usable(info.progressive);
  const adap = usable(info.adaptive);
  const wantHeight = quality === "best" || !quality ? Infinity : parseInt(quality, 10) || Infinity;

  const pickAudio = (list) => {
    let auds = list.filter((f) => f.hasAudio && !f.hasVideo);
    if (audioLang) {
      const byLang = auds.filter((f) => f.audioLang && f.audioLang.toLowerCase().startsWith(audioLang.toLowerCase()));
      if (byLang.length) auds = byLang;
    }
    auds.sort((a, b) => (b.bitrate || 0) - (a.bitrate || 0));
    return auds[0];
  };

  const pickSubtitle = () => {
    if (!subLang || !info.captions || !info.captions.length) return null;
    const cap =
      info.captions.find((c) => c.lang === subLang) ||
      info.captions.find((c) => c.lang && c.lang.toLowerCase().startsWith(subLang.toLowerCase()));
    return cap ? { url: `${cap.url}&fmt=vtt`, ext: "vtt" } : null;
  };

  if (format === "audio") {
    const a = pickAudio(adap);
    if (!a) return null;
    return { audio: { url: a.url, ext: a.ext } };
  }

  // Prefer a progressive stream (audio already baked in) near the requested
  // height — no muxing needed server-side at all.
  const progCandidates = prog
    .filter((f) => f.hasAudio && f.hasVideo)
    .filter((f) => !isFinite(wantHeight) || (f.height || 0) <= wantHeight)
    .sort((a, b) => (b.height || 0) - (a.height || 0));
  if (progCandidates.length) {
    const result = { video: { url: progCandidates[0].url, ext: progCandidates[0].ext } };
    const sub = pickSubtitle();
    if (sub) result.subtitle = sub;
    return result;
  }

  const videos = adap
    .filter((f) => f.hasVideo)
    .filter((f) => !isFinite(wantHeight) || (f.height || 0) <= wantHeight)
    .sort((a, b) => (b.height || 0) - (a.height || 0));
  const video = videos[0];
  if (!video) return null;

  const result = { video: { url: video.url, ext: video.ext } };
  const audio = pickAudio(adap);
  if (audio) result.audio = { url: audio.url, ext: audio.ext };
  const sub = pickSubtitle();
  if (sub) result.subtitle = sub;
  return result;
}

// ---- cookies (fallback path only) ---------------------------------------

async function getCookiesTxt() {
  const cookies = await chrome.cookies.getAll({ domain: "youtube.com" });
  const lines = ["# Netscape HTTP Cookie File"];
  for (const c of cookies) {
    const domain = c.domain.startsWith(".") ? c.domain : c.hostOnly ? c.domain : "." + c.domain;
    const includeSub = domain.startsWith(".") ? "TRUE" : "FALSE";
    const secure = c.secure ? "TRUE" : "FALSE";
    const expiry = c.expirationDate ? Math.floor(c.expirationDate) : 0;
    lines.push([domain, includeSub, c.path, secure, expiry, c.name, c.value].join("\t"));
  }
  return lines.join("\n") + "\n";
}

// ---- job lifecycle --------------------------------------------------------

function notifyPopup(msg) {
  chrome.runtime.sendMessage(msg).catch(() => {});
}

async function startDownload(tabId, opts) {
  const settings = await getSettings();
  const serverUrl = settings.serverUrl.replace(/\/$/, "");

  const info = await inspectTab(tabId);
  if (info.error) {
    throw new Error(`לא הצלחתי לקרוא את נתוני העמוד (${info.error}). ודא שאתה בטאב של יוטיוב עם וידאו טעון.`);
  }

  const payload = {
    title: info.title,
    format: opts.format,
    quality: opts.quality,
    audioLang: opts.audioLang || undefined,
    subLang: opts.subLang || undefined,
  };

  const resolved = pickResolved(info, opts);
  let usedFallback = false;
  if (resolved && (resolved.video || resolved.audio)) {
    payload.resolved = resolved;
  } else {
    usedFallback = true;
    if (!info.videoId) throw new Error("לא זוהה מזהה וידאו בעמוד.");
    payload.url = `https://www.youtube.com/watch?v=${info.videoId}`;
    payload.cookies = await getCookiesTxt();
  }

  if (settings.encryptDownloads) {
    const { publicKeyB64 } = await getOrCreateKeyPair();
    payload.pubKey = publicKeyB64;
  }

  const startResp = await fetch(`${serverUrl}/api/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!startResp.ok) {
    const body = await startResp.text().catch(() => "");
    throw new Error(`השרת סירב להתחיל הורדה (${startResp.status}): ${body}`);
  }
  const { jobId } = await startResp.json();
  activeJobs.set(jobId, { tabId, serverUrl });

  pollJob(jobId, serverUrl, usedFallback);
  return { jobId, usedFallback };
}

async function cancelJob(jobId) {
  const entry = activeJobs.get(jobId);
  if (!entry) return { ok: false };
  await fetch(`${entry.serverUrl}/api/jobs/${jobId}/cancel`, { method: "POST" }).catch(() => {});
  return { ok: true };
}

async function pollJob(jobId, serverUrl, usedFallback) {
  for (;;) {
    let job;
    try {
      const resp = await fetch(`${serverUrl}/api/jobs/${jobId}`);
      job = await resp.json();
    } catch {
      notifyPopup({ type: "sb-progress", jobId, error: "השרת לא מגיב. ודא שהוא רץ." });
      return;
    }

    notifyPopup({ type: "sb-progress", jobId, job, usedFallback });

    if (job.status === "completed") {
      try {
        await finishDownload(serverUrl, job);
        notifyPopup({ type: "sb-done", jobId });
      } catch (e) {
        notifyPopup({ type: "sb-progress", jobId, error: `ההורדה הושלמה בשרת אבל שמירת הקובץ נכשלה: ${e}` });
      }
      activeJobs.delete(jobId);
      return;
    }
    if (job.status === "failed" || job.status === "cancelled") {
      activeJobs.delete(jobId);
      return;
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
}

async function finishDownload(serverUrl, job) {
  const fileUrl = `${serverUrl}/api/jobs/${job.id}/file`;
  if (job.enc) {
    await ensureOffscreen();
    const resp = await chrome.runtime.sendMessage({
      type: "sb-decrypt-and-download",
      fileUrl,
      enc: job.enc,
      fileName: job.fileName,
    });
    if (!resp || !resp.ok) throw new Error((resp && resp.error) || "פענוח נכשל");
    return;
  }
  await chrome.downloads.download({ url: fileUrl, filename: job.fileName, saveAs: false });
}

async function ensureOffscreen() {
  if (chrome.offscreen.hasDocument && (await chrome.offscreen.hasDocument())) return;
  try {
    await chrome.offscreen.createDocument({
      url: "offscreen.html",
      reasons: ["BLOBS"],
      justification: "פענוח AES-GCM ויצירת Blob URL להורדה",
    });
  } catch (e) {
    // already exists (e.g. hasDocument() unavailable on this Chrome version) — fine
    if (!/single offscreen document|already exists/i.test(String(e))) throw e;
  }
}
