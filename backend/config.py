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
# run-on segments) that the user lost confidence in it; moved to large-v3,
# which stood as an untested working hypothesis until the first formal
# quantitative benchmark (research/topic/20260822_stt_transcription_eval/
# report/02-largev3-vs-kotoba-whisper.md, 150-segment CER/chrF++/BLEU/ROUGE-L
# + LLM-judged qualitative pass, no app-level gating): large-v3-turbo scored
# within noise of large-v3 on every metric (CER 0.292 vs 0.289) while running
# ~11.4x faster (RTF 0.068 vs 0.753) — moved to large-v3-turbo 2026-08-22 to
# ease GPU contention with concurrent GPU use (e.g. gaming) per the user's
# real-world complaint. "turbo" is faster-whisper's built-in alias for the
# mobiuslabsgmbh/faster-whisper-large-v3-turbo CTranslate2 conversion (same
# encoder as large-v3, fewer decoder layers).
# int8_float16 (quantized) instead of plain float16: keeps VRAM/compute down
# per the "don't hog the GPU" constraint while accelerating inference.
WHISPER_MODEL_SIZE = "large-v3-turbo"
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

# vanilla (backend branch reset, 2026-08-26): singing detector disabled and
# call sites commented out (backend/audio_session.py, backend/main.py) — see
# docs/planning/IMPROVEMENT_BACKLOG.md M1 for the full history and the
# rebuild starting point. Uncomment this whole block plus those call sites
# to restore.
#
# # Per-utterance singing detector (backend/music_gate.py) — ON HOLD
# # 2026-08-26 (user call): still unreliable live (catches maybe half of real
# # singing, occasionally false-positives on normal speech right after a song
# # ends) and its pitch analysis was adding latency to EVERY utterance's
# # finalize, not just singing ones — contributing to the "transcript shows
# # but translation is slow/missing" complaints piling up alongside an
# # unrelated llama-server duplicate-process issue. Master switch: when False,
# # AudioSession skips calling MusicGate.pitch_stats() entirely (zero added
# # latency) and every final is emitted with music_suspected=False. Flip back
# # on to resume iterating once translation stability itself isn't in
# # question. See music_gate.py's module docstring for the detection
# # approach's own history.
# SINGING_DETECTION_ENABLED = False
#
# # 2026-08-25 (user direction): purpose changed from "catch background music
# # playing under speech" to "detect when the speaker themselves is singing",
# # via pitch (F0) tracking instead of the old syllable-rate-modulation
# # heuristic (which routinely missed sung lyrics — they carry their own
# # syllable-rate energy modulation similar to plain speech). See
# # music_gate.py's module docstring for the full history/rationale.
# #
# # Demucs vocal separation (2026-08-25) runs ahead of pitch tracking so a
# # loud background instrumental under normal talking doesn't corrupt the
# # pitch read — see music_gate.py's docstring for why raw-audio pitch
# # tracking alone wasn't enough. "htdemucs" is the standard 4-stem model
# # (drums/bass/other/vocals); ~100-200ms per utterance once warmed up
# # (MusicGate.warmup(), called once at startup — see main.py).
# DEMUCS_MODEL_NAME = "htdemucs"
#
# PITCH_FRAME_MS = 30.0
# PITCH_HOP_MS = 15.0
# PITCH_MIN_HZ = 70.0  # below typical adult male speaking/singing fundamental
# PITCH_MAX_HZ = 500.0  # above typical adult female singing fundamental (falsetto excluded)
# PITCH_VOICED_ENERGY_FLOOR = 0.01  # frame RMS below this treated as unvoiced/silent, skipped
# PITCH_VOICING_THRESHOLD = 0.35  # normalized autocorrelation peak below this treated as unvoiced/noisy
# PITCH_MIN_VOICED_FRAMES = 6  # need at least this many voiced frames in an utterance to judge at all
# # Median-filter window (frames) applied to the F0 track before computing
# # stats — kills isolated single-frame octave-jump errors (a well-known
# # autocorrelation pitch-tracking failure mode) that otherwise blow up the
# # range estimate for perfectly normal speech. See MusicGate.pitch_stats.
# PITCH_MEDIAN_FILTER_FRAMES = 5
#
# # Session-adaptive singing baseline: compares each utterance's pitch stats
# # against a rolling model of how THIS speaker normally talks, rather than a
# # fixed number — singing register/range varies a lot by voice, so a
# # one-size-fits-all threshold either misses quiet/narrow-range singing or
# # false-positives on speakers with naturally expressive, wide-ranging
# # speech intonation.
# ADAPTIVE_SINGING_ENABLED = True
# ADAPTIVE_SINGING_EMA_ALPHA = 0.15
# ADAPTIVE_SINGING_MIN_SAMPLES = 8
# # Only feed the "how does this speaker normally talk" baseline from
# # utterances whose OWN pitch range is already narrow enough to be
# # unambiguous plain talking — keeps early singing (before enough samples
# # exist to judge adaptively) from contaminating the baseline it would be
# # compared against.
# # Provisional (2026-08-25): a single real conversational clip
# # (data/wav/일상,소통) measured through this exact pipeline came back at
# # ~13-15 semitones of range for plain talking — this autocorrelation
# # pitch tracker is prone to octave jumps (a well-known failure mode of
# # simple autocorrelation pitch tracking), which inflates apparent range.
# # 5.0 (this constant's original value) would have rejected essentially all
# # real speech from ever bootstrapping the baseline. Needs live-session
# # tuning once more real data accumulates.
# #
# # Update (2026-08-26, live capture w/ median filter applied): a real
# # streamer's plain conversational Japanese consistently measured 13-18
# # semitones through this exact pipeline (6 samples) — well above 10, so the
# # baseline was never bootstrapping at all and every utterance fell back to
# # FIXED_SINGING_RANGE_SEMITONES, which was itself too low and false-
# # positived on normal talk about half the time. Raised both this and the
# # fixed fallback below to actually sit above the observed normal-talk
# # range for this pipeline.
# ADAPTIVE_SINGING_BOOTSTRAP_RANGE_MAX_SEMITONES = 18.0
# # Flag as singing when EITHER the utterance's pitch range exceeds the
# # learned baseline range by this multiple, OR its median pitch sits at
# # least this many semitones away from the learned baseline median — a
# # sustained single high/low note can have a narrow range of its own but
# # still sit far outside how the speaker normally talks.
# ADAPTIVE_SINGING_RANGE_RATIO = 1.8
# ADAPTIVE_SINGING_MEDIAN_DEVIATION_SEMITONES = 5.0
# # Fixed fallback used before ADAPTIVE_SINGING_MIN_SAMPLES is reached —
# # deliberately wide so early-session judging doesn't false-positive on
# # normal expressive speech before a real baseline exists. See the
# # provisional-calibration note above — raised alongside the bootstrap floor.
# FIXED_SINGING_RANGE_SEMITONES = 20.0

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

# S5 (2026-08-25, docs/planning/IMPROVEMENT_BACKLOG.md): session-adaptive
# VAD_SILENCE_MS / FINALIZE_GRACE_MS. The two constants above stay as-is —
# they're now the fixed fallback used until enough samples accumulate, and
# the reference point the EMA-adapted values are scaled/clamped around. This
# adapts to the CURRENT SESSION's observed rhythm as a whole, not per
# speaker (that needs diarization — Goal priority §2차 목표, out of scope).
# See AudioSession._effective_silence_ms/_effective_grace_ms.
ADAPTIVE_VAD_ENABLED = True

# EMA smoothing factor for both rolling stats below — kept small so one or
# two odd utterances can't swing the adapted threshold; a genuine shift in
# the speaker's rhythm still shows up within a few dozen sentences.
ADAPTIVE_VAD_EMA_ALPHA = 0.2

# Don't start adapting a given threshold until this many valid samples for
# its underlying stat have been observed — before that, use the fixed
# default above for that threshold specifically (silence/grace adapt
# independently, on their own sample counts).
ADAPTIVE_VAD_MIN_SAMPLES = 5

# The natural inter-utterance silence gap is measured directly — wall-clock
# time between the last speech frame of one utterance and the first speech
# frame of the next (see AudioSession._process_frame) — deliberately NOT
# read from _UtteranceState.silence_ms at finalize time, which is capped at
# essentially the trigger threshold itself and so carries almost no
# information about the speaker's actual rhythm.
ADAPTIVE_SILENCE_TARGET_RATIO = 0.7  # trigger at this fraction of the observed average gap
ADAPTIVE_SILENCE_MIN_MS = 350
ADAPTIVE_SILENCE_MAX_MS = 1200
# A raw gap at least this long is assumed unrelated to sentence rhythm (ad
# break, scene change, speaker stepped away) and is excluded from the
# rolling stat entirely rather than dragging it upward.
ADAPTIVE_PAUSE_OUTLIER_MS = 8000

# Speech rate (transcribed characters / spoken duration, sampled per final —
# see AudioSession._do_finalize) scales the grace period: a slower/more
# hesitant speaker gets more room before being forced to finalize, a brisk
# speaker less. ADAPTIVE_RATE_BASELINE_CPS is the pace FINALIZE_GRACE_MS's
# 200ms default was implicitly tuned around — no live-captured baseline yet
# (same caveat as MUSIC_GATE_* above), revisit once flagged-segment-style
# data exists for this too.
ADAPTIVE_RATE_BASELINE_CPS = 7.0
ADAPTIVE_GRACE_MIN_MS = 100
ADAPTIVE_GRACE_MAX_MS = 500

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

# UI context-summary line (2026-08-20, change-detection redesign 2026-08-25):
# how many finalized segments to wait between *checking* whether the topic
# has moved on. A cheap SAME/CHANGED classification call runs at this cadence
# instead of unconditionally regenerating the summary every time — a speaker
# staying on the same subject for many sentences in a row shouldn't churn the
# summary line just because N more finals arrived. The full summarize_context
# call (more expensive: CONTEXT_SUMMARY_HISTORY_SIZE lines of context) only
# fires when that check comes back CHANGED. Both calls are also skipped
# outright while a previous check/summary is still in flight (see
# audio_session.py), so a slow GPU can't pile up overlapping requests
# regardless of this value. User-requested cadence was "every 3-5 finals";
# 4 is the midpoint.
CONTEXT_CHECK_EVERY_N_FINALS = 4

# How many recent finalized (JA, KO) pairs feed the summary itself once a
# change check triggers a regeneration — wider than FINAL_CONTEXT_HISTORY_SIZE
# (which is tuned for per-sentence translation continuity, not topic gist)
# so the summary reflects the actual current topic rather than just the last
# couple of sentences.
CONTEXT_SUMMARY_HISTORY_SIZE = 10
