// audio-worklet-processor.js — runs on the dedicated audio rendering thread.
// Accumulates incoming 128-sample render quanta into fixed-size chunks
// (CHUNK_SECONDS worth of samples, at whatever the AudioContext's native
// sample rate is) and posts each finished chunk to the main thread.
//
// Resampling to the 16kHz the backend expects happens on the main thread
// (offscreen.js) after receiving the chunk — cheap to do a few times a
// second, and keeps this processor free of resampling-filter complexity.

const CHUNK_SECONDS = 0.3;

class PCMChunkerProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._chunkSize = Math.round(sampleRate * CHUNK_SECONDS);
    this._buffer = new Float32Array(this._chunkSize);
    this._writeIndex = 0;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0) return true;

    // Downmix to mono by averaging channels (tab audio is usually stereo).
    const channelCount = input.length;
    const frameCount = input[0].length;

    for (let i = 0; i < frameCount; i++) {
      let sample = 0;
      for (let ch = 0; ch < channelCount; ch++) {
        sample += input[ch][i];
      }
      sample /= channelCount;

      this._buffer[this._writeIndex++] = sample;

      if (this._writeIndex >= this._chunkSize) {
        // Transfer ownership of the underlying buffer to the main thread
        // (zero-copy) and allocate a fresh one to keep filling.
        this.port.postMessage(
          { sampleRate, chunk: this._buffer },
          [this._buffer.buffer]
        );
        this._buffer = new Float32Array(this._chunkSize);
        this._writeIndex = 0;
      }
    }

    return true; // keep the processor alive
  }
}

registerProcessor("pcm-chunker", PCMChunkerProcessor);
