// popup.js — Stage 1/2 have no real UI yet; this popup exists purely to
// start/stop capture and visually verify that partial/final transcripts +
// translations are arriving correctly (see PRD §7 for the
// partial/final distinction).

const tabId = Number(new URLSearchParams(location.search).get("tabId"));

const toggleBtn = document.getElementById("toggle");
const captureLabel = document.getElementById("captureLabel");
const liveDot = document.getElementById("liveDot");
const statusState = document.getElementById("statusState");
const statusDetail = document.getElementById("statusDetail");
const logEl = document.getElementById("log");

const segmentEls = new Map(); // segment_id -> <div> element, so a "final" can replace its "partial"

// restoreHistory() below awaits an async round-trip to fetch the persisted
// log, but the onMessage listener is already live the moment this script
// runs — a live TRANSCRIPT_EVENT for a brand-new segment can arrive and get
// appended to logEl while restoreHistory is still mid-fetch, landing above
// older history segments that haven't been appended yet (DOM order = first-
// append order). Buffer live events until history is restored, then replay
// them in arrival order, so segments always land in the log in the order
// they actually happened.
let restoring = true;
const pendingEvents = [];

let lastCaptureTitle = null; // Remember the tab title across state changes

async function refreshState() {
  const state = await chrome.runtime.sendMessage({ type: "GET_CAPTURE_STATE", tabId });
  const isActive = state?.active ?? false;

  // Update toggle button: toggle "recording" class and set label text via captureLabel
  toggleBtn.classList.toggle("recording", isActive);
  captureLabel.textContent = isActive ? "캡처 중지" : "캡처 시작";

  // Update status state label
  statusState.textContent = isActive ? "캡처 중" : "일시정지";

  // Update status detail line
  if (isActive) {
    lastCaptureTitle = state?.title ?? `탭 #${tabId}`;
    statusDetail.textContent = lastCaptureTitle;
  } else {
    // While idle, show the tab title if we have one from a prior active session
    statusDetail.textContent = lastCaptureTitle ? lastCaptureTitle : "";
  }

  // Update live dot
  liveDot.classList.toggle("off", !isActive);
}

// Diagnostic aid (2026-08-19): a hung background service worker previously
// left this handler awaiting forever with nothing visible anywhere — no
// error, no console output, button just never changes. Wrap every step so a
// hang surfaces as a timeout error instead of silence, and log at each step
// so the panel's own console shows exactly how far the click got.
function withTimeout(promise, label, ms = 5000) {
  return Promise.race([
    promise,
    new Promise((_, reject) =>
      setTimeout(() => reject(new Error(`${label} timed out after ${ms}ms`)), ms)
    ),
  ]);
}

// This file only ever runs in the detached chrome.windows.create viewer
// window now (background.js's action.onClicked opens it directly — see
// there). Starting capture requires activeTab, which is only granted by the
// toolbar-icon click itself, not by a later gesture inside this already-open
// window (confirmed live 2026-08-19: chrome.sidePanel had the exact same
// problem) — so this window can only stop an active capture; starting one
// again means clicking the toolbar icon.
toggleBtn.addEventListener("click", async () => {
  console.log("[popup] toggle clicked");
  try {
    const state = await withTimeout(
      chrome.runtime.sendMessage({ type: "GET_CAPTURE_STATE", tabId }),
      "GET_CAPTURE_STATE"
    );
    console.log("[popup] current state:", state);
    if (state?.active) {
      await withTimeout(chrome.runtime.sendMessage({ type: "STOP_CAPTURE", tabId }), "STOP_CAPTURE");
      await refreshState();
      return;
    }
    statusDetail.textContent = "확장 아이콘을 클릭해서 다시 시작하세요";
  } catch (err) {
    console.error("[popup] toggle failed:", err);
    statusDetail.textContent = `error: ${err?.message ?? err}`;
  }
});

// Mirrors backend/config.py's filtering thresholds — kept in sync manually,
// since the extension can't import the Python config. Used only to color
// the bars below (red = "on the silence/hallucination side of the actual
// filter"), not to re-implement the filtering logic itself.
const AUDIO_RMS_SILENCE_FLOOR = 0.006;
const WHISPER_NO_SPEECH_THRESHOLD = 0.6;
const WHISPER_AVG_LOGPROB_THRESHOLD = -1.0;

function clampPct(fraction) {
  return Math.max(0, Math.min(1, fraction)) * 100;
}

// One bar per confidence metric: label, numeric value, and a horizontal
// fill sized against a fixed reference range so values are visually
// comparable across segments at a glance. Fill is red when the value sits
// on the side of the corresponding backend threshold that risks being
// treated as silence/hallucination, green otherwise.
const METRICS = [
  {
    key: "audio_rms",
    label: "rms",
    format: (v) => v.toFixed(4),
    pct: (v) => clampPct(v / 0.05), // ~0.05 treated as "full bar" (well above speech floor)
    ok: (v) => v >= AUDIO_RMS_SILENCE_FLOOR,
  },
  {
    key: "no_speech_prob",
    label: "no_speech",
    format: (v) => v.toFixed(2),
    pct: (v) => clampPct(v),
    ok: (v) => v < WHISPER_NO_SPEECH_THRESHOLD,
  },
  {
    key: "avg_logprob",
    label: "avg_logprob",
    format: (v) => v.toFixed(2),
    pct: (v) => clampPct((v + 2) / 2), // -2 (worst) .. 0 (best) mapped to 0%..100%
    ok: (v) => v > WHISPER_AVG_LOGPROB_THRESHOLD,
  },
];

function renderConfidenceBars(confLine, data) {
  confLine.textContent = "";
  for (const metric of METRICS) {
    const value = data?.[metric.key];
    if (value == null) continue;
    const row = document.createElement("div");
    row.className = "metric-row";
    const label = document.createElement("span");
    label.className = "metric-label";
    label.textContent = `${metric.label} ${metric.format(value)}`;
    const bar = document.createElement("span");
    bar.className = "metric-bar";
    const fill = document.createElement("span");
    fill.className = `metric-fill ${metric.ok(value) ? "metric-ok" : "metric-risk"}`;
    fill.style.width = `${metric.pct(value)}%`;
    bar.appendChild(fill);
    row.appendChild(label);
    row.appendChild(bar);
    confLine.appendChild(row);
  }
}

function renderEvent(data) {
  const { type, text, translation, segment_id: segmentId } = data ?? {};
  if (!segmentId) return;

  let entry = segmentEls.get(segmentId);
  if (!entry) {
    const container = document.createElement("div");
    const jaLine = document.createElement("div");
    const koLine = document.createElement("div");
    const confLine = document.createElement("div");
    jaLine.className = "ja-line";
    koLine.className = "ko-line";
    confLine.className = "conf-line";
    container.appendChild(jaLine);
    container.appendChild(koLine);
    container.appendChild(confLine);
    entry = { container, jaLine, koLine, confLine, flagged: false, confidence: null };
    segmentEls.set(segmentId, entry);
    logEl.appendChild(container);

    // Manual mislabel tagging while watching a live capture: click a
    // sentence to mark it wrong (pink), click again to undo. Reads whatever
    // text is currently displayed at click time, so it always reflects the
    // latest partial/final content shown to the user. Relayed through to
    // the backend (offscreen.js -> ws "flag_segment") so it lands in
    // data/flagged_segments.jsonl for later review — see backend/main.py.
    container.addEventListener("click", () => {
      entry.flagged = !entry.flagged;
      container.classList.toggle("flagged", entry.flagged);
      chrome.runtime
        .sendMessage({
          type: "FLAG_SEGMENT",
          tabId,
          segmentId,
          flagged: entry.flagged,
          text: entry.jaLine.textContent,
          translation: entry.koLine.textContent,
          // Whatever confidence numbers were last shown for this segment
          // (final events only — see below), so flagged_segments.jsonl
          // carries the evidence needed to tune the no_speech_prob
          // thresholds in backend/config.py instead of guessing.
          ...(entry.confidence ?? {}),
        })
        .catch(() => {});
    });
  }

  const stateClass = type === "final" ? "final" : "partial";
  entry.jaLine.className = `ja-line ${stateClass}`;
  entry.koLine.className = `ko-line ${stateClass}`;
  entry.container.classList.toggle("is-final", stateClass === "final");
  entry.container.classList.toggle("is-partial", stateClass === "partial");
  entry.jaLine.textContent = text ?? "";
  entry.koLine.textContent = translation ?? "";
  // Debugging aid (see CLAUDE.md hallucination-filtering discussion): only
  // "final" events carry Whisper's confidence signals today (audio_session.py
  // doesn't compute them on the fast/partial pass), so this stays blank
  // until a segment finalizes.
  if (type === "final") {
    renderConfidenceBars(entry.confLine, data);
    entry.confidence = {
      audio_rms: data.audio_rms,
      no_speech_prob: data.no_speech_prob,
      avg_logprob: data.avg_logprob,
    };
  } else {
    entry.confLine.textContent = "";
  }
}

chrome.runtime.onMessage.addListener((message) => {
  if (message?.type !== "TRANSCRIPT_EVENT") return;
  if (message.tabId !== tabId) return; // Ignore messages from other tabs
  if (restoring) {
    pendingEvents.push(message.data);
    return;
  }
  renderEvent(message.data);
  logEl.scrollTop = logEl.scrollHeight;
});

// The popup's own DOM/state is destroyed every time it closes (standard
// Chrome popup behavior — e.g. clicking the page to pause the video closes
// it), but capture keeps running independently in the background/offscreen
// contexts. Restore whatever history accumulated while this popup instance
// didn't exist, from the persisted log in background.js, before subscribing
// to new live events above.
async function restoreHistory() {
  const log = await chrome.runtime.sendMessage({ type: "GET_TRANSCRIPT_LOG", tabId });
  for (const event of log ?? []) {
    renderEvent(event);
  }
  restoring = false;
  for (const event of pendingEvents) {
    renderEvent(event);
  }
  pendingEvents.length = 0;
  logEl.scrollTop = logEl.scrollHeight;
}

restoreHistory();
refreshState();

// --- Chat reply (draft, 2026-08-20): translate the viewer's own Korean ---
// --- chat message into Japanese, using the live broadcast as context. ---
const chatInputEl = document.getElementById("chatInput");
const chatTranslateBtn = document.getElementById("chatTranslateBtn");
const chatOutputEl = document.getElementById("chatOutput");
const chatStatusEl = document.getElementById("chatStatus");

let pendingChatRequestId = null;

async function sendChatTranslateRequest() {
  const text = chatInputEl.value.trim();
  if (!text) return;
  // One in flight at a time — a second click before the first reply lands
  // just supersedes it (pendingChatRequestId check below drops the stale
  // reply rather than showing an out-of-order result).
  const requestId = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  pendingChatRequestId = requestId;
  chatTranslateBtn.disabled = true;
  chatStatusEl.textContent = "번역 중...";
  chatOutputEl.textContent = "";
  chatOutputEl.classList.remove("copied");
  try {
    await chrome.runtime.sendMessage({ type: "TRANSLATE_CHAT", tabId, text, requestId });
  } catch (err) {
    console.error("[popup] TRANSLATE_CHAT send failed:", err);
    chatStatusEl.textContent = `error: ${err?.message ?? err}`;
    chatTranslateBtn.disabled = false;
  }
}

chatTranslateBtn.addEventListener("click", sendChatTranslateRequest);
chatInputEl.addEventListener("keydown", (e) => {
  // Enter sends, Shift+Enter for a newline (matches common chat-box convention).
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendChatTranslateRequest();
  }
});

// Click the translated output to copy it — the whole point is pasting into
// the stream's own chat box, which this extension has no reliable way to
// reach directly (see popup.html's UI-scoping discussion, 2026-08-20).
chatOutputEl.addEventListener("click", async () => {
  if (!chatOutputEl.textContent) return;
  try {
    await navigator.clipboard.writeText(chatOutputEl.textContent);
    chatOutputEl.classList.add("copied");
    chatStatusEl.textContent = "복사됨";
  } catch (err) {
    console.error("[popup] clipboard write failed:", err);
    chatStatusEl.textContent = "복사 실패 — 직접 선택해서 복사하세요";
  }
});

chrome.runtime.onMessage.addListener((message) => {
  if (message?.type !== "CHAT_TRANSLATION") return;
  if (message.tabId !== tabId) return; // Ignore messages from other tabs
  const { request_id: requestId, translation } = message.data ?? {};
  chatTranslateBtn.disabled = false;
  if (requestId !== pendingChatRequestId) return; // superseded by a newer request
  chatOutputEl.textContent = translation || "(번역 결과 없음)";
  chatStatusEl.textContent = translation ? "클릭해서 복사" : "";
});

// Debug toggle
document.getElementById("debugToggle").addEventListener("change", (e) => {
  document.body.classList.toggle("show-debug", e.target.checked);
});
