// offscreen.js — the only extension context allowed to touch AudioContext/
// getUserMedia under MV3. Turns a tabCapture streamId into a live audio
// pipeline: MediaStream -> re-routed to <audio> for normal playback, and in
// parallel -> AudioWorklet -> resample to 16kHz mono PCM16 -> WebSocket.

const BACKEND_WS_URL = "ws://localhost:8000/ws/audio";
const TARGET_SAMPLE_RATE = 16000;

let audioContext = null;
let mediaStream = null;
let workletNode = null;
let ws = null;

function floatTo16BitPCM(float32) {
  const out = new Int16Array(float32.length);
  for (let i = 0; i < float32.length; i++) {
    const s = Math.max(-1, Math.min(1, float32[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

// Simple linear-interpolation resampler. Called a few times a second on
// ~0.3s chunks, so a naive implementation is plenty fast.
function resampleLinear(float32, fromRate, toRate) {
  if (fromRate === toRate) return float32;
  const ratio = fromRate / toRate;
  const outLength = Math.round(float32.length / ratio);
  const out = new Float32Array(outLength);
  for (let i = 0; i < outLength; i++) {
    const srcPos = i * ratio;
    const i0 = Math.floor(srcPos);
    const i1 = Math.min(i0 + 1, float32.length - 1);
    const frac = srcPos - i0;
    out[i] = float32[i0] * (1 - frac) + float32[i1] * frac;
  }
  return out;
}

function connectWebSocket() {
  ws = new WebSocket(BACKEND_WS_URL);
  ws.binaryType = "arraybuffer";

  ws.onopen = () => {
    ws.send(JSON.stringify({ type: "start_session", sample_rate: TARGET_SAMPLE_RATE }));
  };

  ws.onmessage = (event) => {
    // Backend sends JSON text frames: {"type": "partial"|"final", "text": ...}
    // Relay as-is to any listening extension page (popup, later the side
    // panel / content script).
    try {
      const data = JSON.parse(event.data);
      chrome.runtime.sendMessage({ type: "TRANSCRIPT_EVENT", data }).catch(() => {});
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
    },
    video: false,
  });

  // Re-route captured audio back out so the source tab isn't silently
  // muted for the user while we're capturing it.
  const playbackEl = document.getElementById("playback");
  playbackEl.srcObject = mediaStream;

  audioContext = new AudioContext();
  await audioContext.audioWorklet.addModule("audio-worklet-processor.js");

  const sourceNode = audioContext.createMediaStreamSource(mediaStream);
  workletNode = new AudioWorkletNode(audioContext, "pcm-chunker");

  // Keep the graph alive via a silent connection to destination — a worklet
  // node with no path to destination can be deprioritized/stopped by Chrome.
  const silentGain = audioContext.createGain();
  silentGain.gain.value = 0;
  sourceNode.connect(workletNode).connect(silentGain).connect(audioContext.destination);

  workletNode.port.onmessage = (event) => {
    const { sampleRate: nativeSampleRate, chunk } = event.data;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;

    const resampled = resampleLinear(chunk, nativeSampleRate, TARGET_SAMPLE_RATE);
    const pcm16 = floatTo16BitPCM(resampled);
    ws.send(pcm16.buffer);
  };

  connectWebSocket();
}

function stopCapture() {
  if (ws) {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "stop_session" }));
    }
    ws.close();
    ws = null;
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
  }
});
