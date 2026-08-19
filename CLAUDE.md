# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A personal-use Chrome extension that captures the audio of a browser tab (e.g. a Japanese YouTube
live stream), transcribes the Japanese speech, translates it into natural Korean, and displays the
result in near-real-time (target: 1–2s latency). Everything runs locally — no cloud APIs, no
multi-user concerns. Full product context and rationale live in `docs/PRD.md` — read it before
making architectural decisions.

This is a **new, mostly-unwritten project**. There is no existing code to reverse-engineer yet;
treat this file as the intended shape, not a description of what's already built.

## Goal priority

- **1차 목표**: 화자가 1명인 실시간 오디오를 완벽하게 번역한다.
- **2차 목표**: 화자가 2명 이상인 실시간 오디오에서, 화자별로 구분해 각각 완벽하게 번역한다
  (화자 분리/디어라이제이션이 전제 조건).

1차 목표가 충족되기 전까지 다중 화자 대응(화자 분리 등)에 설계 노력을 들이지 않는다.

## Repository note

This repo is its own independent git repository, intentionally separate from the much larger
`pyworks` repo it's nested inside of (which bundles many unrelated sibling projects). Do not assume
anything from the parent directory applies here.

## Planned architecture

```
[Browser tab audio] --chrome.tabCapture--> [extension/] --WebSocket--> [backend/ (FastAPI)]
                                                                             │
                                                                    STT (JA audio -> JA text)
                                                                             │
                                                                    Translate (JA text -> KO text)
                                                                             │
                                                        <--WebSocket streamed results-- 
                                                                             │
                                            [extension/: overlay captions + side panel]
```

- **`extension/`** — Manifest V3 Chrome extension. Uses `chrome.tabCapture` to capture tab audio
  without repeated share-permission prompts (chosen over `getDisplayMedia` for exactly this reason).
  Streams audio in **0.3s chunks** to the local backend over WebSocket and renders results two ways:
  an overlay caption on top of the video, and a separate side panel with a scrolling text log. Both
  display modes must be supported, not just one. Both display modes must visually distinguish
  **provisional** vs **finalized** text (e.g. dim/blurred vs bright/crisp) — see the streaming
  strategy below.
- **`backend/`** — Local Python FastAPI + WebSocket server. Owns the STT and translation pipeline.
  Runs on the user's machine with an NVIDIA GPU (CUDA available) — prefer GPU-accelerated inference
  paths when choosing libraries.
- **`docs/`** — `PRD.md` (product), `PIPELINE.md` (data-flow/behavior reference), `EVAL.md` +
  dated `EVAL_REPORT_*.md` (grading methodology and results), `MODEL_BENCHMARK_PLAN.md`
  (translation-model selection), `IMPROVEMENT_BACKLOG.md`/`IMPROVEMENT_SPECS.md` (known issues
  and how to implement the fixes), `HANDOFF.md` (session-to-session status + GPU verification
  checklist).

### Streaming / sentence-finalization strategy (PRD §7 — important, drives most of the backend design)

The pipeline emits two kinds of text per in-flight sentence, not one:

- **Provisional (in-progress) text**: while a sentence is still being spoken, transcribe
  word-by-word from the streaming partial STT hypothesis and translate it live. Prefer
  context-aware translation, but if that's not tractable in real time, fall back to a simple/literal
  word-for-word translation — showing *something* quickly matters more than polish here.
- **Finalized text**: once a sentence is judged complete, re-render both the transcript and the
  translation as clean, natural, context-aware Korean, and **replace** the provisional text in
  place (not append).

Sentence-completion is judged by combining two signals: silence detection as the primary trigger
(a pause of sufficient length signals a candidate sentence boundary), corrected/confirmed by
punctuation/context analysis in the STT or translation step (pure silence-based cutting alone can
chop sentences awkwardly when a speaker pauses mid-thought). Keep this as two decoupled stages in
the backend, not a single hardcoded heuristic — the silence threshold and the context-correction
logic are separate tunables that will need independent iteration.

### STT / translation engine — benchmarked and chosen (still swappable)

Current production pair, selected by benchmark rather than assumption:

- **STT**: faster-whisper `medium` (CTranslate2, CUDA, int8_float16) — `backend/stt/`.
- **Translation**: **gemma-3-12b-it Q4_K_M** served by a llama.cpp server (chosen over Ollama for
  lower single-stream overhead; the OpenAI-compatible endpoint must honor llama.cpp's `grammar`
  field — backend startup probes this via `verify_contract` and warns if it doesn't). Benchmark
  results and the decision trail: `docs/MODEL_BENCHMARK_PLAN.md`,
  `docs/EVAL_REPORT_gemma-3-12b-it_2026-08-18.md`. Qwen3-14B scored higher but is on hold due to
  incompatibility with the Korean-only GBNF grammar.

**Design principle (unchanged)**: both engines stay behind swappable interfaces
(`backend/stt/base.py`, `backend/translation/base.py`) — a new benchmark winner drops in as a new
class plus config wiring, with no changes to `audio_session.py`.

## Build order (do not skip ahead)

Per `docs/PRD.md` §8, this is being built as a sequential, independently-verifiable pipeline. Land
and manually verify each stage before starting the next.

**Pipeline plumbing (technical stages, both done):**

1. **DONE.** Tab-audio capture (extension, 0.3s chunks) → backend receives audio → Japanese
   transcript printed/visible somewhere simple, already distinguishing provisional vs finalized
   text (no translation, no real UI yet). Verified manually against a real Japanese YouTube live
   stream via the unpacked extension + `uvicorn backend.main:app`.
2. **DONE.** Translation added: provisional text gets simple/literal translation, finalized text
   gets natural translation — still shown somewhere simple (extension popup log, not a real UI).

**Current roadmap (quality/scope phases, in order):**

1. 전사/번역 품질 평가 및 개선 — single-speaker audio (Goal priority §1차 목표). Use
   `docs/EVAL.md`'s methodology once a reference dataset exists.
2. 다중화자 전사/번역 품질 평가 및 개선 — multi-speaker audio, per-speaker separation (Goal
   priority §2차 목표). Not started; do not design for this while phase 1 is open.
3. 노래가 나오는 환경에서의 전사/번역 품질 평가 및 개선 — song/music sections (PRD §11 open
   question). Deferred; current behavior (VAD/no-speech filtering incidentally skips most music) is
   left as-is for now.
4. UI 설계 및 구현 — overlay captions on the video + the side panel, both streaming live with the
   provisional/finalized visual distinction (both display modes required, not just one).

## Commands

Backend (run from repo root, so `backend.*` absolute imports resolve):
```
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload --port 8000
```
`torch` is only pulled in for `torch.hub.load("snakers4/silero-vad", ...)` in `backend/vad.py` — the
STT path (`faster-whisper`/CTranslate2) doesn't need it. First VAD load requires network access once
to fetch the hub model (cached under `~/.cache/torch/hub` afterwards); this doesn't violate the
"fully local at runtime" goal, it's a one-time model download like any other local model weight.

The translation engine is a separate llama.cpp server process (gemma-3-12b-it GGUF, port 8080 —
launch command in `README.md` §실행). Backend startup probes it (`verify_contract`) and only warns
if it's unreachable or ignores GBNF grammar — starting it after the backend is fine.

Extension: no build step. `chrome://extensions` → enable Developer Mode → "Load unpacked" →
select `extension/`. Requires Chrome 109+ (`chrome.offscreen`). After editing extension files,
reload the extension from that page.

No lint/test runner is set up yet; add commands here once one is actually introduced. The only
self-check so far: `python -m backend.glossary` (glossary NFKC matching, no GPU needed).
