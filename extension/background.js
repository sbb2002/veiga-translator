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
//
// Multi-tab (2026-08-20): every piece of state below is keyed by tabId — one
// tab capturing does not stop or interfere with another. See docs/planning/UI.md
// "실제 활용 예시" and the multi-tab-capture branch plan.

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
// below can use it straightaway. An in-page overlay panel is shown right after
// (content_script.js), layered into that tab's own DOM so it can never lose
// focus/drop behind the tab the way a detached OS window did (unlike an
// action popup, which Chrome always closes on blur, the overlay stays put no
// matter what else the user clicks). This also means each tab's activeTab
// grant only ever covers that tab, which is exactly what multi-tab capture
// needs — no extra permission required.
async function getCaptureState(tabId) {
  const { captureSessions = {} } = await chrome.storage.session.get("captureSessions");
  return captureSessions[tabId] ?? { active: false, paused: false, tabId };
}

async function setCaptureState(tabId, state) {
  const { captureSessions = {} } = await chrome.storage.session.get("captureSessions");
  captureSessions[tabId] = state;
  await chrome.storage.session.set({ captureSessions });
}

async function clearCaptureState(tabId) {
  const { captureSessions = {} } = await chrome.storage.session.get("captureSessions");
  delete captureSessions[tabId];
  await chrome.storage.session.set({ captureSessions });
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

// Video/channel metadata scrape (2026-08-25): asks content_script.js to
// read ytInitialPlayerResponse off the page — same "inject on demand if not
// already there" pattern as showOverlay below, since a freshly (re)loaded
// extension won't have content_script.js running yet on an already-open
// tab. Only ever succeeds on youtube.com (manifest's content_scripts match
// pattern) — anywhere else this just fails and startCapture proceeds with
// null metadata, same as before this existed.
async function scrapeMetadata(tabId) {
  try {
    return await chrome.tabs.sendMessage(tabId, { type: "SCRAPE_METADATA" });
  } catch {
    try {
      await chrome.scripting.executeScript({ target: { tabId }, files: ["content_script.js"] });
      return await chrome.tabs.sendMessage(tabId, { type: "SCRAPE_METADATA" });
    } catch (err) {
      console.warn("[background] scrapeMetadata failed:", err);
      return null;
    }
  }
}

// Mid-session metadata refresh (2026-08-25): content_script.js's
// 'yt-navigate-finish' listener reports here when the tab switches to a new
// video/live within the same SPA session (e.g. the previous stream ended
// and a new one started). Only meaningful while a capture is actually
// running for this tab — sender.tab.id lets us key off that without the
// content script needing to know or pass its own tabId.
async function updateVideoMetadata(tabId, metadata) {
  if (!metadata?.channelName) return;
  const current = await getCaptureState(tabId);
  if (!current.active) return; // no capture running here — nothing to update
  const next = {
    ...current,
    title: metadata.videoTitle ?? current.title,
    url: metadata.url ?? current.url,
    channelName: metadata.channelName ?? null,
    channelAvatarUrl: metadata.channelAvatarUrl ?? null,
    videoTitle: metadata.videoTitle ?? null,
    streamStartedAt: metadata.streamStartedAt ?? null,
  };
  await setCaptureState(tabId, next);
  // offscreen.js updates its live session.meta and forwards a
  // "metadata_update" control message to the backend over the already-open
  // websocket (see there) — new [BROADCASTER] hint, new session-log entry.
  await chrome.runtime
    .sendMessage({
      type: "METADATA_UPDATE",
      tabId,
      channelName: metadata.channelName ?? null,
      videoTitle: metadata.videoTitle ?? null,
      videoId: metadata.videoId ?? null,
      isLive: metadata.isLive ?? null,
      streamStartedAt: metadata.streamStartedAt ?? null,
      title: next.title,
      url: next.url,
    })
    .catch(() => {});
  // popup.js's overlay (may be a different iframe instance than the one
  // that was open at capture start) picks this up live for the debug
  // metadata line — same broadcast pattern as CONTEXT_SUMMARY/VOLUME_LEVEL.
  await chrome.runtime
    .sendMessage({
      type: "VIDEO_META_UPDATED",
      tabId,
      channelName: metadata.channelName ?? null,
      channelAvatarUrl: metadata.channelAvatarUrl ?? null,
      videoTitle: metadata.videoTitle ?? null,
      streamStartedAt: metadata.streamStartedAt ?? null,
    })
    .catch(() => {});
}

async function startCapture(tabId, tab) {
  console.log("[background] startCapture: begin, tabId=", tabId);
  const current = await getCaptureState(tabId);
  console.log("[background] startCapture: current state=", current);
  if (current.active) {
    // Already capturing this tab (possibly a stale window that lost track of
    // this after the service worker was recycled) — never silently layer a
    // second capture on top. Only a real STOP_CAPTURE should tear it down.
    // Other tabs' sessions are untouched either way (state is per-tabId).
    console.log("[background] startCapture: already active, returning early");
    return current;
  }

  // Clear this tab's log (and stale context summary) at session START
  // rather than at stop: finals drained by the backend after a stop still
  // land in the log, and the user can review the transcript after stopping
  // until the next session begins.
  const { transcriptLogs = {} } = await chrome.storage.session.get("transcriptLogs");
  delete transcriptLogs[tabId];
  await chrome.storage.session.set({ transcriptLogs });
  const { contextSummaries = {} } = await chrome.storage.session.get("contextSummaries");
  delete contextSummaries[tabId];
  await chrome.storage.session.set({ contextSummaries });

  console.log("[background] startCapture: ensuring offscreen document...");
  await ensureOffscreenDocument();
  console.log("[background] startCapture: offscreen document ready, requesting streamId...");
  const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tabId });
  console.log("[background] startCapture: got streamId, sending INIT_CAPTURE...");
  // Snapshot title/url/favicon now, while we have the Tab object from the
  // activeTab-granting gesture — avoids needing the broader "tabs"
  // permission just to label the overlay panel (and the backend's session
  // log, see main.py's "start_session" handling) later.
  const title = tab?.title ?? `탭 #${tabId}`;
  const url = tab?.url ?? null;
  const startedAt = Date.now();
  const meta = (await scrapeMetadata(tabId).catch(() => null)) ?? {};
  await chrome.runtime.sendMessage({
    type: "INIT_CAPTURE",
    tabId,
    streamId,
    title,
    url,
    startedAt,
    channelName: meta.channelName ?? null,
    channelAvatarUrl: meta.channelAvatarUrl ?? null,
    videoTitle: meta.videoTitle ?? null,
    videoId: meta.videoId ?? null,
    isLive: meta.isLive ?? null,
    streamStartedAt: meta.streamStartedAt ?? null,
  });
  console.log("[background] startCapture: INIT_CAPTURE sent, done");

  const next = {
    active: true,
    paused: false,
    tabId,
    title,
    url,
    favIconUrl: tab?.favIconUrl ?? null,
    startedAt,
    channelName: meta.channelName ?? null,
    channelAvatarUrl: meta.channelAvatarUrl ?? null,
    videoTitle: meta.videoTitle ?? null,
    streamStartedAt: meta.streamStartedAt ?? null,
  };
  await setCaptureState(tabId, next);
  return next;
}

// True end-of-session teardown (tabCapture stream released, websocket
// closed) — only reachable via the tab actually closing (see
// chrome.tabs.onRemoved below). The overlay panel's own button never calls
// this: see pauseCapture/resumeCapture for why a real stop+start cycle from
// inside the overlay can't work at all (no privileged gesture available
// there), which is the whole reason pause exists as a separate, resumable
// state instead of reusing stop for it.
async function stopCapture(tabId) {
  await chrome.runtime.sendMessage({ type: "STOP_CAPTURE", tabId }).catch(() => {});
  const next = { active: false, paused: false, tabId };
  await setCaptureState(tabId, next);
  return next;
}

// Pause/resume (2026-08-20): keeps the tabCapture MediaStream/WebSocket
// alive in offscreen.js and just gates whether audio actually gets sent —
// see offscreen.js's SessionState.paused comment for the full rationale.
// Unlike start/stop, both directions are safe to trigger from a plain
// message with no user-gesture requirement, so the overlay panel's own
// button (popup.js) can fully control this.
async function pauseCapture(tabId) {
  await chrome.runtime.sendMessage({ type: "PAUSE_CAPTURE", tabId }).catch(() => {});
  const current = await getCaptureState(tabId);
  const next = { ...current, paused: true };
  await setCaptureState(tabId, next);
  return next;
}

async function resumeCapture(tabId) {
  await chrome.runtime.sendMessage({ type: "RESUME_CAPTURE", tabId }).catch(() => {});
  const current = await getCaptureState(tabId);
  const next = { ...current, paused: false };
  await setCaptureState(tabId, next);
  return next;
}

// TRANSCRIPT_EVENT arrives in bursts (partial/final for several segments
// close together); appendToLog does read-modify-write on
// chrome.storage.session, so concurrent unserialized calls race and the
// loser silently overwrites the winner's update (e.g. a "final" flip gets
// clobbered back to "partial"). Chain calls through one promise to force
// them to run one at a time, in arrival order — one shared chain is enough
// even with multiple tabs (each write still only touches its own tabId
// bucket; the point is ordering per bucket, not parallelism across tabs).
let logChain = Promise.resolve();
function queueAppendToLog(tabId, event) {
  logChain = logChain.then(() => appendToLog(tabId, event)).catch(() => {});
}

async function appendToLog(tabId, event) {
  const { transcriptLogs = {} } = await chrome.storage.session.get("transcriptLogs");
  const log = transcriptLogs[tabId] ?? [];
  const idx = log.findIndex((e) => e.segment_id === event.segment_id);
  if (idx >= 0) {
    log[idx] = event;
  } else {
    log.push(event);
  }
  transcriptLogs[tabId] = log.slice(-MAX_LOG_ENTRIES);
  await chrome.storage.session.set({ transcriptLogs });
}

// Overlay panel lives inside the captured page's own DOM (content_script.js)
// instead of a separate chrome.windows.create window — a detached OS window
// dropped behind the tab the instant the user clicked the video, which the
// user flagged as the actual blocker to daily use. SHOW_OVERLAY is idempotent
// on the content-script side (a no-op if the panel is already built), so
// there's no window-id bookkeeping to maintain here anymore.
async function showOverlay(tabId) {
  try {
    await chrome.tabs.sendMessage(tabId, { type: "SHOW_OVERLAY", tabId });
  } catch {
    // No content script listening yet — typical right after the extension
    // itself was (re)loaded while the tab was already open, since the
    // manifest's static content_scripts only auto-inject on navigation.
    // activeTab is already granted by this same click, so inject on demand.
    await chrome.scripting.executeScript({ target: { tabId }, files: ["content_script.js"] });
    await chrome.tabs.sendMessage(tabId, { type: "SHOW_OVERLAY", tabId });
  }
}

// The toolbar icon click itself both starts capture (if this tab isn't
// already being captured) and shows that tab's own overlay panel — no popup
// step in between, per the user's explicit request. Clicking the icon on a
// different tab while another tab is already capturing starts a second,
// fully independent session instead of being ignored.
chrome.action.onClicked.addListener(async (tab) => {
  console.log("[background] action clicked, tab=", tab.id);
  try {
    const state = await getCaptureState(tab.id);
    if (!state.active) {
      await startCapture(tab.id, tab);
    }
  } catch (err) {
    console.error("[background] action.onClicked startCapture failed:", err);
  }
  await showOverlay(tab.id);
});

// A tab that's being captured can simply be closed by the user — there's no
// audio left to capture, so tear down its session rather than leaving an
// orphaned capture running in offscreen.js forever. The overlay panel needs
// no explicit cleanup here: it lives in that tab's own DOM, so it's gone the
// instant the tab is.
chrome.tabs.onRemoved.addListener(async (tabId) => {
  const state = await getCaptureState(tabId);
  if (state.active) {
    await stopCapture(tabId);
  }
  await clearCaptureState(tabId);
});

// Auto-stop when a captured tab navigates OFF YouTube (2026-08-29). The
// tabCapture stream survives navigation, so without this the same backend
// session log would keep filling with whatever unrelated audio the tab plays
// next — a different site's content in one file makes the per-session logs
// unusable. Same-tab SPA navigation within YouTube (video -> video) also
// fires this, but with a youtube.com URL, so it's left running; the backend
// rolls its log to a fresh file on the video change instead (see main.py's
// metadata_update handling). Restarting after leaving requires the toolbar
// click again — tabCapture needs that user gesture and can't self-start.
function isYouTubeUrl(url) {
  try {
    const u = new URL(url);
    if (u.protocol !== "http:" && u.protocol !== "https:") return true; // about:blank etc. — not "a site"
    return u.hostname === "youtube.com" || u.hostname.endsWith(".youtube.com");
  } catch {
    return true; // unparseable — don't tear down a session on a transient
  }
}

chrome.tabs.onUpdated.addListener(async (tabId, changeInfo) => {
  if (!changeInfo.url || isYouTubeUrl(changeInfo.url)) return;
  const state = await getCaptureState(tabId);
  if (!state.active) return;
  console.log("[background] captured tab left YouTube (%s) — stopping capture", changeInfo.url);
  await stopCapture(tabId);
  await clearCaptureState(tabId);
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === "VIDEO_METADATA_UPDATED") {
    // From content_script.js's 'yt-navigate-finish' listener — sender.tab.id
    // is this tab's own id, so the content script never needs to embed one.
    const tabId = sender?.tab?.id;
    if (tabId != null) updateVideoMetadata(tabId, message.metadata).catch(() => {});
    return false;
  }

  if (message?.type === "START_CAPTURE") {
    // popup.js sends this from a click inside the action popup — the
    // gesture Chrome accepts for activeTab/tabCapture (see the comment
    // above getCaptureState).
    startCapture(message.tabId, message.tab)
      .then((state) => sendResponse({ ok: true, state }))
      .catch((err) => {
        console.error("[background] startCapture failed:", err);
        sendResponse({ ok: false, error: String(err) });
      });
    return true; // keep the message channel open for the async response
  }

  if (message?.type === "STOP_CAPTURE") {
    stopCapture(message.tabId)
      .then(() => sendResponse({ ok: true }))
      .catch((err) => sendResponse({ ok: false, error: String(err) }));
    return true;
  }

  if (message?.type === "PAUSE_CAPTURE") {
    pauseCapture(message.tabId)
      .then((state) => sendResponse({ ok: true, state }))
      .catch((err) => sendResponse({ ok: false, error: String(err) }));
    return true;
  }

  if (message?.type === "RESUME_CAPTURE") {
    resumeCapture(message.tabId)
      .then((state) => sendResponse({ ok: true, state }))
      .catch((err) => sendResponse({ ok: false, error: String(err) }));
    return true;
  }

  if (message?.type === "GET_CAPTURE_STATE") {
    getCaptureState(message.tabId).then(sendResponse);
    return true;
  }

  if (message?.type === "GET_TRANSCRIPT_LOG") {
    chrome.storage.session.get("transcriptLogs").then(({ transcriptLogs = {} }) => {
      sendResponse(transcriptLogs[message.tabId] ?? []);
    });
    return true;
  }

  if (message?.type === "TRANSCRIPT_EVENT") {
    // Persist so a reopened overlay panel can restore history that arrived
    // while it was closed — see the service-worker-lifetime note above;
    // popup.js's own in-memory log is wiped every time its iframe is torn
    // down regardless. Tagged with tabId by offscreen.js so it lands in the
    // right tab's bucket.
    queueAppendToLog(message.tabId, message.data);
    return false; // every popup.js instance's own listener also receives this live
  }

  if (message?.type === "GET_CONTEXT_SUMMARY") {
    chrome.storage.session.get("contextSummaries").then(({ contextSummaries = {} }) => {
      sendResponse(contextSummaries[message.tabId] ?? "");
    });
    return true;
  }

  if (message?.type === "CONTEXT_SUMMARY") {
    // Only ever one current value per tab (not a log), so no ordering
    // concerns like queueAppendToLog above — plain read-modify-write is
    // fine since context_summary events are already throttled server-side
    // (config.CONTEXT_SUMMARY_EVERY_N_FINALS) to arrive minutes apart.
    chrome.storage.session.get("contextSummaries").then(async ({ contextSummaries = {} }) => {
      contextSummaries[message.tabId] = message.data.text;
      await chrome.storage.session.set({ contextSummaries });
    });
    return false; // every popup.js instance's own listener also receives this live
  }

  return false;
});
