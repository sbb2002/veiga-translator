// offscreen.js — the only extension context allowed to touch AudioContext/
// getUserMedia under MV3. Turns a tabCapture streamId into a live audio
// pipeline: MediaStream -> re-routed to <audio> for normal playback, and in
// parallel -> 16kHz AudioContext + AudioWorklet -> mono PCM16 -> WebSocket.

const BACKEND_WS_URL = "ws://localhost:8000/ws/audio";
const TARGET_SAMPLE_RATE = 16000;

let audioContext = null;
let mediaStream = null;
let workletNode = null;
let ws = null;

// R2/R3 state: capture activity tracking and WebSocket reconnection
let captureActive = false;
let reconnectDelayMs = 1000;
let reconnectTimer = null;
let wsForceCloseTimer = null;

function floatTo16BitPCM(float32) {
  const out = new Int16Array(float32.length);
  for (let i = 0; i < float32.length; i++) {
    const s = Math.max(-1, Math.min(1, float32[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

function connectWebSocket() {
  ws = new WebSocket(BACKEND_WS_URL);
  ws.binaryType = "arraybuffer";

  ws.onopen = () => {
    reconnectDelayMs = 1000; // R2: reset backoff on successful connection
    ws.send(JSON.stringify({ type: "start_session", sample_rate: TARGET_SAMPLE_RATE }));
  };

  ws.onclose = (event) => {
    // A stopped session's socket can close late (after the backend finishes
    // draining) — if a new session already replaced `ws`, this close belongs
    // to the superseded socket and must not touch the live one.
    if (ws !== null && event.target !== ws) return;
    ws = null;
    if (wsForceCloseTimer) { clearTimeout(wsForceCloseTimer); wsForceCloseTimer = null; }
    if (!captureActive) return; // deliberate stop — don't resurrect
    // Backend went away mid-capture (e.g. uvicorn --reload restart).
    // Audio keeps flowing and is dropped by the readyState guard until
    // we're back; retry with capped exponential backoff.
    reconnectTimer = setTimeout(connectWebSocket, reconnectDelayMs);
    reconnectDelayMs = Math.min(reconnectDelayMs * 2, 10000);
  };

  ws.onmessage = (event) => {
    // Backend sends JSON text frames. Two families: "partial"/"final" are
    // the streaming caption pipeline (relayed as TRANSCRIPT_EVENT, and
    // persisted into the transcript log by background.js); "chat_translation"
    // is the one-shot reverse-direction (draft, 2026-08-20) reply to a
    // TRANSLATE_CHAT request below — relayed separately so it never lands in
    // the persisted transcript log (it has no segment_id and isn't part of
    // the caption stream).
    try {
      const data = JSON.parse(event.data);
      if (data.type === "chat_translation") {
        chrome.runtime.sendMessage({ type: "CHAT_TRANSLATION", data }).catch(() => {});
      } else {
        chrome.runtime.sendMessage({ type: "TRANSCRIPT_EVENT", data }).catch(() => {});
      }
    } catch {
      // Non-JSON message — ignore for Stage 1.
    }
  };

  ws.onerror = (err) => {
    console.error("[offscreen] WebSocket error", err);
  };
}

async function startCapture(streamId) {
  if (audioContext) {
    // Already capturing — background.js is supposed to guard against this
    // (see its captureState persistence note), but this offscreen document
    // is the last line of defense against ever double-stacking a second
    // getUserMedia/AudioContext/WebSocket on top of a live one.
    console.warn("[offscreen] startCapture called while already capturing — ignoring");
    return;
  }

  mediaStream = await navigator.mediaDevices.getUserMedia({
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
  // muted for the user while we're capturing it.
  const playbackEl = document.getElementById("playback");
  playbackEl.srcObject = mediaStream;

  // 16kHz context: Chrome resamples the captured stream down to the context
  // rate with a proper anti-aliased resampler, so the worklet already emits
  // backend-ready 16kHz samples. (The previous manual linear-interpolation
  // resampler had no low-pass filter — content above 8kHz aliased into the
  // audible band, i.e. avoidable noise in the STT input.)
  audioContext = new AudioContext({ sampleRate: TARGET_SAMPLE_RATE });
  await audioContext.audioWorklet.addModule("audio-worklet-processor.js");

  const sourceNode = audioContext.createMediaStreamSource(mediaStream);
  workletNode = new AudioWorkletNode(audioContext, "pcm-chunker");

  // Keep the graph alive via a silent connection to destination — a worklet
  // node with no path to destination can be deprioritized/stopped by Chrome.
  const silentGain = audioContext.createGain();
  silentGain.gain.value = 0;
  sourceNode.connect(workletNode).connect(silentGain).connect(audioContext.destination);

  workletNode.port.onmessage = (event) => {
    const { chunk } = event.data;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(floatTo16BitPCM(chunk).buffer);
  };

  captureActive = true; // R2: enable reconnection attempts
  connectWebSocket();
}

function stopCapture() {
  // R2/R3: suppress reconnect attempts and drain final text from backend
  captureActive = false;
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }

  if (ws) {
    if (ws.readyState === WebSocket.OPEN) {
      // R3: send stop_session but leave socket open so backend can drain queued
      // finalize work and emit "final" events before closing. Server closes when done.
      ws.send(JSON.stringify({ type: "stop_session" }));
      // Safety timer: a hung backend can't leak the socket indefinitely.
      // Close over THIS socket, not the module variable — a new session may
      // have replaced `ws` by the time the timer fires.
      const draining = ws;
      wsForceCloseTimer = setTimeout(() => {
        draining.close(); // no-op if already closed
      }, 12000);
    } else {
      // Connecting or already closed — clean up immediately.
      ws.close();
      ws = null;
    }
  }

  if (workletNode) {
    workletNode.port.onmessage = null;
    workletNode.disconnect();
    workletNode = null;
  }
  if (audioContext) {
    audioContext.close();
    audioContext = null;
  }
  if (mediaStream) {
    mediaStream.getTracks().forEach((t) => t.stop());
    mediaStream = null;
  }
  const playbackEl = document.getElementById("playback");
  if (playbackEl) playbackEl.srcObject = null;
}

chrome.runtime.onMessage.addListener((message) => {
  if (message?.type === "INIT_CAPTURE") {
    startCapture(message.streamId).catch((err) => {
      console.error("[offscreen] startCapture failed", err);
    });
  } else if (message?.type === "STOP_CAPTURE") {
    stopCapture();
  } else if (message?.type === "FLAG_SEGMENT") {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(
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
    // sense while a capture session's WS is open (translation needs the
    // live broadcast context), same precondition as FLAG_SEGMENT above.
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(
        JSON.stringify({
          type: "translate_chat",
          request_id: message.requestId,
          text: message.text,
        })
      );
    }
  }
});
