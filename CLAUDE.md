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
- **`docs/`** — `PRD.md` and any research/benchmark notes.

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

### STT / translation engine — deliberately not pinned yet

No specific model is chosen. The user wants to benchmark speed/quality candidates before deciding
(candidates mentioned: whisper.cpp / faster-whisper for STT, possibly something above Whisper-tier if
it benchmarks better; Ollama for translation, possibly something faster-serving if it benchmarks
better — e.g. a llama.cpp server or vLLM). **Design consequence**: keep the STT engine and the
translation engine behind swappable interfaces in the backend rather than hardcoding a specific
model/library, so a benchmark winner can be dropped in without a rewrite.

## Build order (do not skip ahead)

Per `docs/PRD.md` §8, this is being built as a sequential, independently-verifiable pipeline. Land
and manually verify each stage before starting the next:

1. **DONE.** Tab-audio capture (extension, 0.3s chunks) → backend receives audio → Japanese
   transcript printed/visible somewhere simple, already distinguishing provisional vs finalized
   text (no translation, no real UI yet). Verified manually against a real Japanese YouTube live
   stream via the unpacked extension + `uvicorn backend.main:app`.
2. Add translation: provisional text gets simple/literal translation, finalized text gets natural
   translation — still shown somewhere simple.
3. Add the real UI: overlay captions on the video + the side panel, both streaming live with the
   provisional/finalized visual distinction.

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

Extension: no build step. `chrome://extensions` → enable Developer Mode → "Load unpacked" →
select `extension/`. Requires Chrome 109+ (`chrome.offscreen`). After editing extension files,
reload the extension from that page.

No lint/test tooling is set up yet — this is Stage 1 of a from-scratch build (see "Build order"
below); add commands here once a linter/test runner is actually introduced.
