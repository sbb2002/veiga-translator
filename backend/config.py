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

# Sentence-completion correction (CLAUDE.md "Streaming / sentence-finalization
# strategy": silence is the primary trigger, punctuation/context analysis is a
# separate correction stage — kept as its own tunable, not folded into
# VAD_SILENCE_MS). Once VAD_SILENCE_MS is reached, finalize immediately only
# if the last partial transcript looks like a complete sentence
# (backend/sentence_completion.py); otherwise wait up to this much extra
# silence for the speaker to resume before force-finalizing anyway — bounds
# the added latency instead of waiting indefinitely for a "complete" signal
# that may never come (hesitation-heavy live-stream speech routinely trails
# off without a clean sentence-final form).
FINALIZE_GRACE_MS = 400

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
#   llama-server/llama-server.exe -m backend/models/google_gemma-3-12b-it-Q4_K_M.gguf --port 8080 -ngl 999 -c 4096
#
# Swapped from Qwen2.5-7B-Instruct to Gemma-3-12b-it per
# docs/MODEL_BENCHMARK_PLAN.md — beats the baseline on every human-graded
# axis with grammar-constrained decoding on (chrF++ 24.64->28.69, S1 rate
# 50.8%->43.3%) and zero Latin-script leakage in that config. See
# docs/EVAL_REPORT_gemma-3-12b-it_2026-08-18.md for the full comparison.
LLAMA_SERVER_URL = "http://127.0.0.1:8080"
LLAMA_SERVER_MODEL = "gemma-3-12b-it"  # cosmetic — llama-server serves whatever -m it was launched with
LLAMA_SERVER_TIMEOUT_S = 15.0
LLAMA_FAST_MAX_TOKENS = 64  # provisional translation: short, literal is fine
LLAMA_FINAL_MAX_TOKENS = 200  # finalized translation: full natural sentence

# Short-term context memory (EVAL_REPORT_2026-08-18.md §5-E-1, cheapest first
# step before any summarization layer): how many previous *final* (JA, KO)
# sentence pairs to carry into the next final translation call, oldest
# first. Helps the model resolve dropped subjects/pronouns and continue a
# speaker's self-correction/reversal across a VAD split, instead of only
# ever seeing the single immediately-prior sentence.
FINAL_CONTEXT_HISTORY_SIZE = 3
