"""Central config for Stage 1 (capture + STT verification).

Kept as plain module-level constants for now — no env-var/dotenv layer yet
since this runs on one personal machine. Revisit if Stage 2/3 need per-
environment overrides.
"""

# Audio
SAMPLE_RATE = 16000  # must match TARGET_SAMPLE_RATE in extension/offscreen.js

# STT (faster-whisper)
# "small" was too error-prone on fast/slangy live-stream Japanese (see
# docs/planning/PRD.md translation-quality notes — a Q4->Q8 translation-model upgrade
# made ~no difference on garbled sentences, confirming the errors originate
# in the transcript, not the translation). "medium" then showed enough
# hallucination/garbling on live capture (2026-08-19, data/flagged_segments.jsonl
# — stock-phrase hallucinations, mangled repeated-word passages, oversized
# run-on segments) that the user lost confidence in it; moved to large-v3.
# int8_float16 (quantized) instead of plain float16: keeps VRAM/compute down
# per the "don't hog the GPU" constraint while accelerating inference.
WHISPER_MODEL_SIZE = "large-v3"
WHISPER_DEVICE = "cuda"
WHISPER_COMPUTE_TYPE = "int8_float16"
WHISPER_LANGUAGE = "ja"

# Fast (partial) vs quality (final) decoding settings — see stt/base.py's `fast` flag.
WHISPER_FAST_BEAM_SIZE = 1
WHISPER_FINAL_BEAM_SIZE = 5

# Drop a Whisper segment only when BOTH no_speech_prob is >= this AND
# avg_logprob is <= WHISPER_AVG_LOGPROB_THRESHOLD (see
# stt/faster_whisper_engine.py) — no_speech_prob alone was measurably
# dropping real short/energetic speech, not just silence.
WHISPER_NO_SPEECH_THRESHOLD = 0.6
# Above this, drop the segment unconditionally — no avg_logprob check needed.
# Live-capture labeling on 2026-08-19 (data/flagged_segments.jsonl) showed
# hallucinated stock phrases (outro thank-yous etc.) at no_speech_prob
# 0.76-0.90 with avg_logprob only -0.45 to -0.77 (i.e. confident enough to
# never cross WHISPER_AVG_LOGPROB_THRESHOLD either) — an initial guess of 0.9
# here still missed a 0.898 and 0.824 case from that same session. Real
# short/energetic speech in the same data topped out around 0.55; the
# historically-cited high end for real speech (0.87, see
# WHISPER_NO_SPEECH_THRESHOLD above) and this hallucination family's range
# do overlap, so this can't be a perfectly clean cut. Briefly lowered to
# 0.66 the same day, then reverted back to 0.6 (matches
# WHISPER_NO_SPEECH_THRESHOLD, so the soft/AND-avg_logprob check above never
# actually gets a chance to run) per user call while live-watching —
# revisit again against a larger flagged_segments.jsonl sample as more
# accumulates.
WHISPER_NO_SPEECH_HARD_THRESHOLD = 0.6

# Embedding-similarity "Bag of Hallucinations" gate (backend/hallucination_gate.py)
# — added 2026-08-19 after labeling proved WHISPER_NO_SPEECH_HARD_THRESHOLD
# alone can't separate the "ご視聴ありがとうございました" family from real
# short speech; their no_speech_prob/avg_logprob ranges genuinely overlap.
# See hallucination_gate.py's docstring for the research basis and how the
# threshold below was picked.
HALLUCINATION_GATE_ENABLED = True
HALLUCINATION_GATE_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
HALLUCINATION_GATE_SIM_THRESHOLD = 0.78
# Whisper's own average per-token log-probability for a segment; -1.0
# mirrors OpenAI Whisper's reference decoding heuristic for "low
# confidence" output. Genuine speech, even short exclamations, tends to
# decode well above this; true silence/garbage tends to fall below it.
WHISPER_AVG_LOGPROB_THRESHOLD = -1.0

# Independent of Whisper's own confidence signals above: the RMS amplitude of
# the raw audio buffer itself, checked BEFORE trusting any STT result. Whisper
# hallucinates memorized training-data phrases (YouTube outro boilerplate,
# etc.) confidently enough — high avg_logprob, low no_speech_prob — that no
# text-confidence signal reliably catches every phrase in that family, and a
# growing regex blocklist of "known hallucinated phrases" doesn't generalize
# to the next one. Actual audio loudness is a fact about the input, not the
# model's self-assessment, so it can't be fooled the same way: near-silent
# audio (VAD false-positive on background noise/music, or a trailing-silence
# tail — see audio_session.py) gets its STT result discarded outright,
# regardless of what text Whisper produced for it. Float32 PCM normalized to
# [-1, 1]; genuine speech (even quiet) sits well above this floor.
AUDIO_RMS_SILENCE_FLOOR = 0.006

# VAD / segmentation
VAD_SILENCE_MS = 600  # pause length that triggers "final" (utterance-end candidate)
VAD_FRAME_SAMPLES = 512  # silero-vad requires fixed frame sizes at 16kHz; 512 samples = 32ms
VAD_SPEECH_THRESHOLD = 0.5  # speech-probability cutoff
MAX_UTTERANCE_SECONDS = 10  # hard cap: force-finalize even without silence (run-on speech safety net)

# Music/speech discriminator gating VAD (backend/music_gate.py) — un-deferred
# 2026-08-19 after live capture with background music underneath speech kept
# producing outro-phrase hallucinations even past the RMS/no_speech_prob
# gates in audio_session.py, because BGM keeps the raw audio "loud" during
# stretches where nobody's actually talking, and VAD itself can mistake
# rhythmic music for speech. No calibration data yet (this is a first cut,
# see music_gate.py's docstring for the signal-processing approach) — revisit
# both knobs once flagged_segments.jsonl accumulates BGM-heavy examples.
    # DISABLED 2026-08-19 (same day it was added): live capture showed it
    # dropping genuine Japanese speech, not just background music — likely
    # the 3-5.5Hz "syllable rate" band, calibrated against English
    # (syllable-timed), doesn't transfer to Japanese's mora-timed rhythm,
    # and/or the 2s rolling window blends a preceding music-only stretch
    # into a freshly-started utterance's judgment, diluting its peak. Losing
    # real speech is a worse regression than the residual BGM hallucination
    # this was meant to fix (Goal priority §1차 목표), so off until this can
    # be recalibrated against real captured audio instead of synthetic test
    # signals — see music_gate.py's docstring for what was and wasn't
    # validated.
MUSIC_GATE_ENABLED = False
MUSIC_GATE_WINDOW_S = 2.0  # how much rolling context before the gate starts judging
# The tallest bin in the 3-5.5Hz (syllable-rate) band must be at least this
# many times the median modulation-spectrum bin elsewhere to count as a real
# speech-rate peak; below it, treat the window as music. Conservative (high)
# on purpose per the "don't drop real speech on an ambiguous read" bias —
# only a window with no credible syllable-rate peak at all gets suppressed.
MUSIC_GATE_MODULATION_RATIO_THRESHOLD = 3.0

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
FINALIZE_GRACE_MS = 200

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
# for our single-stream use case; see docs/planning/PRD.md benchmarking notes).
# Start it separately, e.g.:
#   llama-server/llama-server.exe -m backend/models/google_gemma-3-12b-it-Q4_K_M.gguf --port 8080 -ngl 999 -c 4096
#
# Swapped from Qwen2.5-7B-Instruct to Gemma-3-12b-it per
# docs/eval/MODEL_BENCHMARK_PLAN.md — beats the baseline on every human-graded
# axis with grammar-constrained decoding on (chrF++ 24.64->28.69, S1 rate
# 50.8%->43.3%) and zero Latin-script leakage in that config. See
# docs/eval/EVAL_REPORT_gemma-3-12b-it_2026-08-18.md for the full comparison.
LLAMA_SERVER_URL = "http://127.0.0.1:8080"
LLAMA_SERVER_MODEL = "gemma-3-12b-it"  # cosmetic — llama-server serves whatever -m it was launched with
LLAMA_SERVER_TIMEOUT_S = 15.0
# Fast/partial translation calls run inline with the audio-processing
# path (until Q2 in docs/planning/IMPROVEMENT_SPECS.md moves them off it), so a
# stalled LLM call must give up quickly — the next partial cycle retries
# with a fresher buffer anyway. Finals run on the background queue and can
# afford the longer LLAMA_SERVER_TIMEOUT_S above.
LLAMA_FAST_TIMEOUT_S = 3.0
LLAMA_FAST_MAX_TOKENS = 64  # partial translation: short, literal is fine
LLAMA_FINAL_MAX_TOKENS = 200  # final translation: full natural sentence

# On session close (stop_session / client disconnect), wait up to this long
# for queued finalize work to drain before cancelling the worker — otherwise
# the last spoken sentences never get their "final". Bounded so a hung
# translation server can't stall shutdown (its own per-request timeouts are
# shorter, so a healthy-but-slow drain fits comfortably).
CLOSE_DRAIN_TIMEOUT_S = 10.0

# Short-term context memory (EVAL_REPORT_2026-08-18.md §5-E-1, cheapest first
# step before any summarization layer): how many previous *final* (JA, KO)
# sentence pairs to carry into the next final translation call, oldest
# first. Helps the model resolve dropped subjects/pronouns and continue a
# speaker's self-correction/reversal across a VAD split, instead of only
# ever seeing the single immediately-prior sentence.
FINAL_CONTEXT_HISTORY_SIZE = 3
