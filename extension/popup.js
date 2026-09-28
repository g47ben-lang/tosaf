import { getSettings, setSettings } from "./lib/config.js";

const els = {
  notYoutube: document.getElementById("notYoutube"),
  videoInfo: document.getElementById("videoInfo"),
  videoTitle: document.getElementById("videoTitle"),
  form: document.getElementById("downloadForm"),
  format: document.getElementById("format"),
  quality: document.getElementById("quality"),
  qualityRow: document.getElementById("qualityRow"),
  audioLang: document.getElementById("audioLang"),
  audioLangRow: document.getElementById("audioLangRow"),
  subLang: document.getElementById("subLang"),
  subLangRow: document.getElementById("subLangRow"),
  startBtn: document.getElementById("startBtn"),
  progress: document.getElementById("progress"),
  progressFill: document.getElementById("progressFill"),
  progressStatus: document.getElementById("progressStatus"),
  cancelBtn: document.getElementById("cancelBtn"),
  errorBox: document.getElementById("errorBox"),
  doneBox: document.getElementById("doneBox"),
  serverStatus: document.getElementById("serverStatus"),
  openOptions: document.getElementById("openOptions"),
};

let currentTabId = null;
let currentJobId = null;
let lastInfo = null;

const STATUS_LABELS = {
  preparing: "מתכונן…",
  downloading: "מוריד…",
  completed: "הושלם",
  failed: "נכשל",
  cancelled: "בוטל",
};

els.openOptions.addEventListener("click", () => chrome.runtime.openOptionsPage());

function showError(msg) {
  els.errorBox.textContent = msg;
  els.errorBox.classList.remove("hidden");
}
function clearError() {
  els.errorBox.classList.add("hidden");
}

function populateSelect(select, entries, placeholder) {
  select.innerHTML = "";
  const opt0 = document.createElement("option");
  opt0.value = "";
  opt0.textContent = placeholder;
  select.appendChild(opt0);
  for (const e of entries) {
    const opt = document.createElement("option");
    opt.value = e.value;
    opt.textContent = e.label;
    select.appendChild(opt);
  }
}

function uniqueAudioLangs(info) {
  const seen = new Map();
  for (const f of [...(info.adaptive || [])]) {
    if (f.audioLang && !seen.has(f.audioLang)) seen.set(f.audioLang, f.audioLang);
  }
  return [...seen.keys()].map((l) => ({ value: l, label: l }));
}

function updateFormVisibility() {
  const isVideo = els.format.value !== "audio";
  els.qualityRow.classList.toggle("hidden", !isVideo);
  els.audioLangRow.classList.toggle("hidden", !(isVideo && lastInfo && uniqueAudioLangs(lastInfo).length > 1));
  els.subLangRow.classList.toggle(
    "hidden",
    !(isVideo && lastInfo && lastInfo.captions && lastInfo.captions.length),
  );
}

async function init() {
  const settings = await getSettings();
  els.format.value = settings.lastFormat;
  els.quality.value = settings.lastQuality;

  fetch(`${settings.serverUrl.replace(/\/$/, "")}/api/ping`)
    .then((r) => r.json())
    .then((p) => {
      els.serverStatus.textContent = `שרת מחובר · yt-dlp ${p.ytDlp}${p.ffmpeg ? "" : " · ⚠️ אין ffmpeg"}`;
    })
    .catch(() => {
      els.serverStatus.textContent = "⚠️ לא מצליח להתחבר לשרת — בדוק הגדרות";
    });

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !/^https:\/\/(www\.)?youtube\.com\/watch/.test(tab.url || "")) {
    els.notYoutube.classList.remove("hidden");
    return;
  }
  currentTabId = tab.id;

  const info = await chrome.runtime.sendMessage({ type: "sb-inspect-tab", tabId: tab.id });
  if (!info || info.error) {
    els.notYoutube.classList.remove("hidden");
    els.notYoutube.textContent = "לא הצלחתי לקרוא את נתוני הווידאו. רענן את העמוד ונסה שוב.";
    return;
  }
  lastInfo = info;
  els.videoTitle.textContent = info.title || "";
  els.videoInfo.classList.remove("hidden");

  populateSelect(els.audioLang, uniqueAudioLangs(info), "ברירת מחדל");
  populateSelect(
    els.subLang,
    (info.captions || []).map((c) => ({ value: c.lang, label: c.name })),
    "ללא",
  );

  updateFormVisibility();
  els.form.classList.remove("hidden");
}

els.format.addEventListener("change", updateFormVisibility);

els.form.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  clearError();
  els.doneBox.classList.add("hidden");

  const opts = {
    format: els.format.value,
    quality: els.quality.value,
    audioLang: els.audioLang.value,
    subLang: els.subLang.value,
  };
  await setSettings({
    lastFormat: opts.format,
    lastQuality: opts.quality,
    lastAudioLang: opts.audioLang,
    lastSubLang: opts.subLang,
  });

  els.form.classList.add("hidden");
  els.progress.classList.remove("hidden");
  els.progressStatus.textContent = "מתחיל…";
  els.progressFill.style.width = "0%";

  const resp = await chrome.runtime.sendMessage({ type: "sb-start-download", tabId: currentTabId, opts });
  if (resp && resp.error) {
    showError(resp.error);
    els.progress.classList.add("hidden");
    els.form.classList.remove("hidden");
    return;
  }
  currentJobId = resp.jobId;
  if (resp.usedFallback) {
    els.progressStatus.textContent = "לא נמצא פורמט מוכן בעמוד — נופל למסלול השרת (דורש פרוקסי מוגדר)…";
  }
});

els.cancelBtn.addEventListener("click", async () => {
  if (!currentJobId) return;
  await chrome.runtime.sendMessage({ type: "sb-cancel-job", jobId: currentJobId });
});

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.jobId !== currentJobId) return;

  if (msg.type === "sb-progress") {
    if (msg.error) {
      showError(msg.error);
      return;
    }
    const job = msg.job;
    els.progressFill.style.width = `${job.percent || 0}%`;
    const label = STATUS_LABELS[job.status] || job.status;
    const bits = [label];
    if (job.speed) bits.push(job.speed);
    if (job.eta) bits.push(`נותרו ${job.eta}`);
    els.progressStatus.textContent = bits.join(" · ");

    if (job.status === "failed" && job.error) {
      showError(job.error.message || job.error.messageEn || "ההורדה נכשלה");
    }
  }

  if (msg.type === "sb-done") {
    els.progress.classList.add("hidden");
    els.doneBox.classList.remove("hidden");
  }
});

init();
