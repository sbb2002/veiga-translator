"""Central config for Stage 1 (capture + STT verification).

Kept as plain module-level constants for now — no env-var/dotenv layer yet
since this runs on one personal machine. Revisit if Stage 2/3 need per-
environment overrides.
"""

# Audio
SAMPLE_RATE = 16000  # must match TARGET_SAMPLE_RATE in extension/offscreen.js

# STT (faster-whisper)
# "small" was too error-prone on fast/slangy live-stream Japanese (see
# docs/PRD.md translation-quality notes — a Q4->Q8 translation-model upgrade
# made ~no difference on garbled sentences, confirming the errors originate
# in the transcript, not the translation). Testing "medium" next.
# int8_float16 (quantized) instead of plain float16: keeps VRAM/compute down
# per the "don't hog the GPU" constraint while accelerating inference.
WHISPER_MODEL_SIZE = "medium"
WHISPER_DEVICE = "cuda"
WHISPER_COMPUTE_TYPE = "int8_float16"
WHISPER_LANGUAGE = "ja"

# Fast (provisional) vs quality (final) decoding settings — see stt/base.py's `fast` flag.
WHISPER_FAST_BEAM_SIZE = 1
WHISPER_FINAL_BEAM_SIZE = 5

# Drop any Whisper segment whose no_speech_prob is >= this — filters out the
# classic hallucinated-outro-phrase failure mode on silent/quiet audio.
WHISPER_NO_SPEECH_THRESHOLD = 0.6

# VAD / segmentation
VAD_SILENCE_MS = 600  # pause length that triggers "final" (utterance-end candidate)
VAD_FRAME_SAMPLES = 512  # silero-vad requires fixed frame sizes at 16kHz; 512 samples = 32ms
VAD_SPEECH_THRESHOLD = 0.5  # speech-probability cutoff
MAX_UTTERANCE_SECONDS = 10  # hard cap: force-finalize even without silence (run-on speech safety net)

# Partial re-transcription cadence: re-run STT on the in-progress buffer at most this often,
# to avoid re-transcribing on every single 0.3s chunk.
PARTIAL_UPDATE_INTERVAL_S = 0.6

# Don't run STT at all until the in-progress utterance has at least this much
# audio — Whisper is noticeably more prone to hallucinating (including
# glossary hotwords into audio that never said them) on very short/near-empty
# buffers, which showed up as bad partials right after starting a capture.
MIN_PARTIAL_AUDIO_SECONDS = 0.8

# Translation (llama-server — an OpenAI-compatible /v1/chat/completions server
# running the same llama.cpp engine Ollama wraps, without Ollama's overhead
# for our single-stream use case; see docs/PRD.md benchmarking notes).
# Start it separately, e.g.:
#   llama-server/llama-server.exe -m backend/models/Qwen2.5-7B-Instruct-Q4_K_M.gguf --port 8080 -ngl 999 -c 4096
LLAMA_SERVER_URL = "http://127.0.0.1:8080"
LLAMA_SERVER_MODEL = "qwen2.5-7b-instruct"  # cosmetic — llama-server serves whatever -m it was launched with
LLAMA_SERVER_TIMEOUT_S = 15.0
LLAMA_FAST_MAX_TOKENS = 64  # provisional translation: short, literal is fine
LLAMA_FINAL_MAX_TOKENS = 200  # finalized translation: full natural sentence
