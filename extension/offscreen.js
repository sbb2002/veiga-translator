// offscreen.js — the only extension context allowed to touch AudioContext/
// getUserMedia under MV3. Turns a tabCapture streamId into a live audio
// pipeline: MediaStream -> re-routed to <audio> for normal playback, and in
// parallel -> 16kHz AudioContext + AudioWorklet -> mono PCM16 -> WebSocket.
//
// Multi-tab (2026-08-20): one offscreen document still exists for the whole
// extension (Chrome allows only one), but it now holds one independent
// SessionState per captured tabId in `sessions` instead of a single set of
// module-level audioContext/mediaStream/ws variables. Nothing about a given
// tab's pipeline (reconnect backoff, drain-on-stop, etc.) changed — only the
// "one set of variables" -> "one Map entry per tabId" indirection.

const BACKEND_WS_URL = "ws://localhost:8000/ws/audio";
const TARGET_SAMPLE_RATE = 16000;

/** @typedef {{
 *   audioContext: AudioContext | null,
 *   mediaStream: MediaStream | null,
 *   workletNode: AudioWorkletNode | null,
 *   playbackEl: HTMLAudioElement | null,
 *   ws: WebSocket | null,
 *   captureActive: boolean,
 *   paused: boolean,
 *   reconnectDelayMs: number,
 *   reconnectTimer: number | null,
 *   wsForceCloseTimer: number | null,
 * }} SessionState */

/** @type {Map<number, SessionState>} */
const sessions = new Map();

function newSessionState() {
  return {
    audioContext: null,
    mediaStream: null,
    workletNode: null,
    playbackEl: null,
    ws: null,
    captureActive: false,
    // Pause (2026-08-20): distinct from stopCapture()'s full teardown below.
    // A button inside the overlay panel can never re-acquire tabCapture's
    // streamId (that call requires the toolbar-icon gesture specifically —
    // confirmed live 2026-08-19), so a "stop then start again" model made
    // resuming from the overlay permanently impossible. Pausing instead
    // just gates whether captured audio actually gets sent — the
    // MediaStream/AudioContext/WebSocket all stay alive and require no
    // privileged API to resume, so the overlay's own button can fully
    // control it. Known trade-off: the backend's in-progress utterance
    // buffer (if speech was mid-sentence at pause time) doesn't get any
    // "silence" signal during the pause, so a very short pause mid-sentence
    // can read as a seamless continuation rather than a real gap — accepted
    // as a minor edge case rather than adding cross-pause flush logic.
    paused: false,
    reconnectDelayMs: 1000,
    reconnectTimer: null,
    wsForceCloseTimer: null,
  };
}

function floatTo16BitPCM(float32) {
  const out = new Int16Array(float32.length);
  for (let i = 0; i < float32.length; i++) {
    const s = Math.max(-1, Math.min(1, float32[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

function connectWebSocket(tabId, session) {
  const ws = new WebSocket(BACKEND_WS_URL);
  session.ws = ws;
  ws.binaryType = "arraybuffer";

  ws.onopen = () => {
    session.reconnectDelayMs = 1000; // R2: reset backoff on successful connection
    ws.send(JSON.stringify({ type: "start_session", sample_rate: TARGET_SAMPLE_RATE }));
  };

  ws.onclose = (event) => {
    // A stopped session's socket can close late (after the backend finishes
    // draining) — if a new session already replaced session.ws, this close
    // belongs to the superseded socket and must not touch the live one.
    if (session.ws !== null && event.target !== session.ws) return;
    session.ws = null;
    if (session.wsForceCloseTimer) {
      clearTimeout(session.wsForceCloseTimer);
      session.wsForceCloseTimer = null;
    }
    if (!session.captureActive) return; // deliberate stop — don't resurrect
    // Backend went away mid-capture (e.g. uvicorn --reload restart).
    // Audio keeps flowing and is dropped by the readyState guard until
    // we're back; retry with capped exponential backoff.
    session.reconnectTimer = setTimeout(() => connectWebSocket(tabId, session), session.reconnectDelayMs);
    session.reconnectDelayMs = Math.min(session.reconnectDelayMs * 2, 10000);
  };

  ws.onmessage = (event) => {
    // Backend sends JSON text frames. Three families: "partial"/"final" are
    // the streaming caption pipeline (relayed as TRANSCRIPT_EVENT, and
    // persisted into that tab's transcript log by background.js);
    // "chat_translation" is the one-shot reverse-direction (draft,
    // 2026-08-20) reply to a TRANSLATE_CHAT request below — relayed
    // separately so it never lands in the persisted transcript log (it has
    // no segment_id and isn't part of the caption stream); "context_summary"
    // (2026-08-20) is the periodic one-line "what's being talked about"
    // update — also relayed separately and persisted as a single latest
    // value (not a log) by background.js. All are tagged with tabId so
    // background.js and the right overlay panel can route them to the
    // correct tab's session.
    try {
      const data = JSON.parse(event.data);
      if (data.type === "chat_translation") {
        chrome.runtime.sendMessage({ type: "CHAT_TRANSLATION", tabId, data }).catch(() => {});
      } else if (data.type === "context_summary") {
        chrome.runtime.sendMessage({ type: "CONTEXT_SUMMARY", tabId, data }).catch(() => {});
      } else {
        chrome.runtime.sendMessage({ type: "TRANSCRIPT_EVENT", tabId, data }).catch(() => {});
      }
    } catch {
      // Non-JSON message — ignore for Stage 1.
    }
  };

  ws.onerror = (err) => {
    console.error(`[offscreen] WebSocket error (tab ${tabId})`, err);
  };
}

async function startCapture(tabId, streamId) {
  if (sessions.has(tabId)) {
    // Already capturing this tab — background.js is supposed to guard
    // against this (see its captureSessions persistence note), but this
    // offscreen document is the last line of defense against ever
    // double-stacking a second getUserMedia/AudioContext/WebSocket on top
    // of a live one for the same tab. Other tabs are unaffected either way.
    console.warn(`[offscreen] startCapture called while tab ${tabId} is already capturing — ignoring`);
    return;
  }

  const session = newSessionState();
  sessions.set(tabId, session);

  session.mediaStream = await navigator.mediaDevices.getUserMedia({
    audio: {
      mandatory: {
        chromeMediaSource: "tab",
        chromeMediaSourceId: streamId,
      },
      // 2026-08-19: tried disabling these (echoCancellation/autoGainControl/
      // noiseSuppression) to fix a perceived slight volume drop on capture
      // start, but this backfired badly — autoGainControl off left real
      // captured audio too quiet, so faster-whisper's no_speech_prob spiked
      // and nearly every segment got classified as the "ご視聴..." outro
      // hallucination and dropped (see backend_run.log). Reverted; the
      // playback element and the STT pipeline share the same MediaStream
      // track, so processing constraints here affect both at once — the
      // volume complaint needs a different fix (e.g. a gain node on the
      // playback path only, not touching the STT-facing track).
    },
    video: false,
  });

  // Re-route captured audio back out so the source tab isn't silently
  // muted for the user while we're capturing it. One element per tab now
  // (multi-tab capture) instead of the single static #playback element.
  const playbackEl = document.createElement("audio");
  playbackEl.autoplay = true;
  document.body.appendChild(playbackEl);
  playbackEl.srcObject = session.mediaStream;
  session.playbackEl = playbackEl;

  // 16kHz context: Chrome resamples the captured stream down to the context
  // rate with a proper anti-aliased resampler, so the worklet already emits
  // backend-ready 16kHz samples. (The previous manual linear-interpolation
  // resampler had no low-pass filter — content above 8kHz aliased into the
  // audible band, i.e. avoidable noise in the STT input.)
  session.audioContext = new AudioContext({ sampleRate: TARGET_SAMPLE_RATE });
  await session.audioContext.audioWorklet.addModule("audio-worklet-processor.js");

  const sourceNode = session.audioContext.createMediaStreamSource(session.mediaStream);
  session.workletNode = new AudioWorkletNode(session.audioContext, "pcm-chunker");

  // Keep the graph alive via a silent connection to destination — a worklet
  // node with no path to destination can be deprioritized/stopped by Chrome.
  const silentGain = session.audioContext.createGain();
  silentGain.gain.value = 0;
  sourceNode.connect(session.workletNode).connect(silentGain).connect(session.audioContext.destination);

  session.workletNode.port.onmessage = (event) => {
    const { chunk } = event.data;
    if (session.paused) return;
    if (!session.ws || session.ws.readyState !== WebSocket.OPEN) return;
    session.ws.send(floatTo16BitPCM(chunk).buffer);
  };

  session.captureActive = true; // R2: enable reconnection attempts
  connectWebSocket(tabId, session);
}

function stopCapture(tabId) {
  const session = sessions.get(tabId);
  if (!session) return;
  sessions.delete(tabId);

  // R2/R3: suppress reconnect attempts and drain final text from backend
  session.captureActive = false;
  if (session.reconnectTimer) {
    clearTimeout(session.reconnectTimer);
    session.reconnectTimer = null;
  }

  if (session.ws) {
    if (session.ws.readyState === WebSocket.OPEN) {
      // R3: send stop_session but leave socket open so backend can drain queued
      // finalize work and emit "final" events before closing. Server closes when done.
      session.ws.send(JSON.stringify({ type: "stop_session" }));
      // Safety timer: a hung backend can't leak the socket indefinitely.
      // Close over THIS socket, not session.ws — a new session for this tab
      // could in principle replace it before the timer fires.
      const draining = session.ws;
      session.wsForceCloseTimer = setTimeout(() => {
        draining.close(); // no-op if already closed
      }, 12000);
    } else {
      // Connecting or already closed — clean up immediately.
      session.ws.close();
      session.ws = null;
    }
  }

  if (session.workletNode) {
    session.workletNode.port.onmessage = null;
    session.workletNode.disconnect();
    session.workletNode = null;
  }
  if (session.audioContext) {
    session.audioContext.close();
    session.audioContext = null;
  }
  if (session.mediaStream) {
    session.mediaStream.getTracks().forEach((t) => t.stop());
    session.mediaStream = null;
  }
  if (session.playbackEl) {
    session.playbackEl.srcObject = null;
    session.playbackEl.remove();
    session.playbackEl = null;
  }
}

chrome.runtime.onMessage.addListener((message) => {
  if (message?.type === "INIT_CAPTURE") {
    startCapture(message.tabId, message.streamId).catch((err) => {
      console.error(`[offscreen] startCapture failed (tab ${message.tabId})`, err);
    });
  } else if (message?.type === "STOP_CAPTURE") {
    stopCapture(message.tabId);
  } else if (message?.type === "PAUSE_CAPTURE") {
    const session = sessions.get(message.tabId);
    if (session) session.paused = true;
  } else if (message?.type === "RESUME_CAPTURE") {
    const session = sessions.get(message.tabId);
    if (session) session.paused = false;
  } else if (message?.type === "FLAG_SEGMENT") {
    const session = sessions.get(message.tabId);
    if (session?.ws && session.ws.readyState === WebSocket.OPEN) {
      session.ws.send(
        JSON.stringify({
          type: "flag_segment",
          segment_id: message.segmentId,
          flagged: message.flagged,
          text: message.text,
          translation: message.translation,
          audio_rms: message.audio_rms,
          no_speech_prob: message.no_speech_prob,
          avg_logprob: message.avg_logprob,
        })
      );
    }
  } else if (message?.type === "TRANSLATE_CHAT") {
    // Draft (2026-08-20): outgoing KO->JA chat-reply translation — see
    // popup.js and backend/audio_session.py::translate_chat. Only makes
    // sense while that tab's capture WS is open (translation needs the
    // live broadcast context), same precondition as FLAG_SEGMENT above.
    const session = sessions.get(message.tabId);
    if (session?.ws && session.ws.readyState === WebSocket.OPEN) {
      session.ws.send(
        JSON.stringify({
          type: "translate_chat",
          request_id: message.requestId,
          text: message.text,
        })
      );
    }
  }
});
