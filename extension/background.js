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

// A chrome.sidePanel-based persistent UI was tried and reverted 2026-08-19:
// starting capture from a click *inside* a side panel reliably failed with
// "Extension has not been invoked for the current page (see activeTab
// permission)" — activeTab granted by the toolbar-icon click that opens the
// panel does not carry over to a later, separate gesture on a button inside
// it, and (once the manifest declares "side_panel") Chrome opens the panel
// natively on icon click without ever firing chrome.action.onClicked, so
// there's no reliable hook to grab that gesture another way either.
//
// A classic default_popup was tried next (start capture via a button click
// *inside* the popup, which Chrome does accept for activeTab/tabCapture),
// but the user wanted the toolbar icon itself to start capture immediately
// with no intermediate popup step. So: manifest has no "default_popup" at
// all now, which makes chrome.action.onClicked fire directly on the icon
// click — that click IS the activeTab-granting gesture, so startCapture(tab.id)
// below can use it straightaway. A separate chrome.windows.create popup-type
// window is opened right after, and stays open no matter what else the user
// clicks (unlike an action popup, which Chrome always closes on blur).
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
  console.log("[background] startCapture: begin, tabId=", tabId);
  const current = await getCaptureState();
  console.log("[background] startCapture: current state=", current);
  if (current.active) {
    // Already capturing (possibly a stale popup that lost track of this
    // after the service worker was recycled) — never silently layer a
    // second capture on top. Only a real STOP_CAPTURE should tear it down.
    console.log("[background] startCapture: already active, returning early");
    return current;
  }

  // Clear the log at session START rather than at stop: finals drained by
  // the backend after a stop still land in the log, and the user can review
  // the transcript after stopping until the next session begins.
  await chrome.storage.session.remove("transcriptLog");

  console.log("[background] startCapture: ensuring offscreen document...");
  await ensureOffscreenDocument();
  console.log("[background] startCapture: offscreen document ready, requesting streamId...");
  const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tabId });
  console.log("[background] startCapture: got streamId, sending INIT_CAPTURE...");
  await chrome.runtime.sendMessage({ type: "INIT_CAPTURE", streamId });
  console.log("[background] startCapture: INIT_CAPTURE sent, done");

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

const VIEWER_URL = "popup.html?detached=1";

async function getViewerWindowId() {
  const { viewerWindowId } = await chrome.storage.session.get("viewerWindowId");
  return viewerWindowId ?? null;
}

async function openOrFocusViewerWindow() {
  const existingId = await getViewerWindowId();
  if (existingId != null) {
    try {
      await chrome.windows.update(existingId, { focused: true });
      return; // still open — just bring it forward, don't spawn a second one
    } catch {
      // User closed it since last time; fall through and create a fresh one.
    }
  }
  const win = await chrome.windows.create({
    url: chrome.runtime.getURL(VIEWER_URL),
    type: "popup",
    width: 420,
    height: 620,
    focused: true,
  });
  await chrome.storage.session.set({ viewerWindowId: win.id });
}

// The toolbar icon click itself both starts capture (if idle) and opens/
// focuses the persistent viewer window — no popup step in between, per the
// user's explicit request.
chrome.action.onClicked.addListener(async (tab) => {
  console.log("[background] action clicked, tab=", tab.id);
  try {
    const state = await getCaptureState();
    if (!state.active) {
      await startCapture(tab.id);
    }
  } catch (err) {
    console.error("[background] action.onClicked startCapture failed:", err);
  }
  await openOrFocusViewerWindow();
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "START_CAPTURE") {
    // popup.js sends this from a click inside the action popup — the
    // gesture Chrome accepts for activeTab/tabCapture (see the comment
    // above getCaptureState).
    startCapture(message.tabId)
      .then((state) => sendResponse({ ok: true, state }))
      .catch((err) => {
        console.error("[background] startCapture failed:", err);
        sendResponse({ ok: false, error: String(err) });
      });
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
