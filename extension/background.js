// background.js — MV3 service worker.
// Owns capture-session orchestration only: creating the offscreen document,
// obtaining a tabCapture streamId, and handing it off. It cannot touch
// AudioContext/getUserMedia itself (no DOM in a service worker), so all
// actual audio work happens in offscreen.js.
//
// IMPORTANT: MV3 service workers are killed by Chrome after ~30s of
// inactivity and restarted fresh on the next event — any state kept only in
// a module-level variable is lost when that happens. Capture can easily run
// longer than that (the whole point is a long-running background session),
// so capture state MUST live in chrome.storage.session (survives service
// worker restarts, cleared on browser close) rather than a plain variable.
// Without this, reopening the popup after the service worker was recycled
// shows "idle" even though offscreen.js is still actively capturing, and
// clicking Start Capture again stacks a second capture on top instead of
// recognizing one is already running.

const OFFSCREEN_URL = "offscreen.html";
const MAX_LOG_ENTRIES = 200;

async function getCaptureState() {
  const { captureState } = await chrome.storage.session.get("captureState");
  return captureState ?? { active: false, tabId: null };
}

async function setCaptureState(state) {
  await chrome.storage.session.set({ captureState: state });
}

async function ensureOffscreenDocument() {
  const existing = await chrome.runtime.getContexts({
    contextTypes: ["OFFSCREEN_DOCUMENT"],
  });
  if (existing.length > 0) return;

  await chrome.offscreen.createDocument({
    url: OFFSCREEN_URL,
    reasons: ["USER_MEDIA"],
    justification: "Capture and stream tab audio to the local translation backend.",
  });
}

async function startCapture(tabId) {
  const current = await getCaptureState();
  if (current.active) {
    // Already capturing (possibly a stale popup that lost track of this
    // after the service worker was recycled) — never silently layer a
    // second capture on top. Only a real STOP_CAPTURE should tear it down.
    return current;
  }

  // Clear the log at session START rather than at stop: finals drained by
  // the backend after a stop still land in the log, and the user can review
  // the transcript after stopping until the next session begins.
  await chrome.storage.session.remove("transcriptLog");

  await ensureOffscreenDocument();
  const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tabId });
  await chrome.runtime.sendMessage({ type: "INIT_CAPTURE", streamId });

  const next = { active: true, tabId };
  await setCaptureState(next);
  return next;
}

async function stopCapture() {
  await chrome.runtime.sendMessage({ type: "STOP_CAPTURE" }).catch(() => {});
  const next = { active: false, tabId: null };
  await setCaptureState(next);
  return next;
}

// TRANSCRIPT_EVENT arrives in bursts (partial/final for several segments
// close together); appendToLog does read-modify-write on
// chrome.storage.session, so concurrent unserialized calls race and the
// loser silently overwrites the winner's update (e.g. a "final" flip gets
// clobbered back to "partial"). Chain calls through one promise to force
// them to run one at a time, in arrival order.
let logChain = Promise.resolve();
function queueAppendToLog(event) {
  logChain = logChain.then(() => appendToLog(event)).catch(() => {});
}

async function appendToLog(event) {
  const { transcriptLog = [] } = await chrome.storage.session.get("transcriptLog");
  const idx = transcriptLog.findIndex((e) => e.segment_id === event.segment_id);
  if (idx >= 0) {
    transcriptLog[idx] = event;
  } else {
    transcriptLog.push(event);
  }
  const trimmed = transcriptLog.slice(-MAX_LOG_ENTRIES);
  await chrome.storage.session.set({ transcriptLog: trimmed });
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "START_CAPTURE") {
    startCapture(message.tabId)
      .then((state) => sendResponse({ ok: true, state }))
      .catch((err) => sendResponse({ ok: false, error: String(err) }));
    return true; // keep the message channel open for the async response
  }

  if (message?.type === "STOP_CAPTURE") {
    stopCapture()
      .then(() => sendResponse({ ok: true }))
      .catch((err) => sendResponse({ ok: false, error: String(err) }));
    return true;
  }

  if (message?.type === "GET_CAPTURE_STATE") {
    getCaptureState().then(sendResponse);
    return true;
  }

  if (message?.type === "GET_TRANSCRIPT_LOG") {
    chrome.storage.session.get("transcriptLog").then(({ transcriptLog = [] }) => {
      sendResponse(transcriptLog);
    });
    return true;
  }

  if (message?.type === "TRANSCRIPT_EVENT") {
    // Persist so a reopened popup can restore history that arrived while it
    // was closed — see the service-worker-lifetime note above; popup.js's
    // own in-memory log is wiped every time the popup closes regardless.
    queueAppendToLog(message.data);
    return false; // popup.js's own listener also receives this event live
  }

  return false;
});
