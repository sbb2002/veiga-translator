// popup.js — Stage 1/2 have no real UI yet; this popup exists purely to
// start/stop capture and visually verify that partial/final transcripts +
// translations are arriving correctly (see PRD §7 for the
// partial/final distinction).

const toggleBtn = document.getElementById("toggle");
const statusEl = document.getElementById("status");
const logEl = document.getElementById("log");

const segmentEls = new Map(); // segment_id -> <div> element, so a "final" can replace its "partial"

async function getActiveTabId() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab?.id;
}

async function refreshState() {
  const state = await chrome.runtime.sendMessage({ type: "GET_CAPTURE_STATE" });
  toggleBtn.textContent = state?.active ? "Stop Capture" : "Start Capture";
  statusEl.textContent = state?.active ? `capturing tab ${state.tabId}` : "idle";
}

toggleBtn.addEventListener("click", async () => {
  const state = await chrome.runtime.sendMessage({ type: "GET_CAPTURE_STATE" });
  if (state?.active) {
    await chrome.runtime.sendMessage({ type: "STOP_CAPTURE" });
  } else {
    const tabId = await getActiveTabId();
    if (!tabId) {
      statusEl.textContent = "no active tab";
      return;
    }
    const res = await chrome.runtime.sendMessage({ type: "START_CAPTURE", tabId });
    if (!res?.ok) {
      statusEl.textContent = `error: ${res?.error ?? "unknown"}`;
      await refreshState();
      return;
    }
  }
  await refreshState();
});

function renderEvent(data) {
  const { type, text, translation, segment_id: segmentId } = data ?? {};
  if (!segmentId) return;

  let entry = segmentEls.get(segmentId);
  if (!entry) {
    const container = document.createElement("div");
    const jaLine = document.createElement("div");
    const koLine = document.createElement("div");
    jaLine.className = "ja-line";
    koLine.className = "ko-line";
    container.appendChild(jaLine);
    container.appendChild(koLine);
    entry = { container, jaLine, koLine };
    segmentEls.set(segmentId, entry);
    logEl.appendChild(container);
  }

  const stateClass = type === "final" ? "final" : "partial";
  entry.jaLine.className = `ja-line ${stateClass}`;
  entry.koLine.className = `ko-line ${stateClass}`;
  entry.jaLine.textContent = text ?? "";
  entry.koLine.textContent = translation ?? "";
}

chrome.runtime.onMessage.addListener((message) => {
  if (message?.type !== "TRANSCRIPT_EVENT") return;
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
  const log = await chrome.runtime.sendMessage({ type: "GET_TRANSCRIPT_LOG" });
  for (const event of log ?? []) {
    renderEvent(event);
  }
  logEl.scrollTop = logEl.scrollHeight;
}

restoreHistory();
refreshState();
