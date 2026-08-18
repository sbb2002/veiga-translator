"""Translate-only benchmark for a candidate translation model, per
docs/MODEL_BENCHMARK_PLAN.md. Reuses the existing STT hypotheses
(hyp_ja) from a prior run_eval.py pass so only the translation step is
re-run — start llama-server with the candidate model first.

Produces chrF++ for both translate(hyp_ja) and translate(ja_ref) (the
latter is the STT-free comparison point used to pick a model, per
EVAL_REPORT_2026-08-18.md §5-C).

Usage (llama-server must already be running with the candidate model):
    python scripts/bench_translation_model.py <model_label> [results.jsonl] [--vanilla]

--vanilla disables both the Korean-only grammar mask and repeat_penalty
(script-purity enforcement fully off) — for the vanilla-vs-grammar
comparison per model requested after the Qwen3-14B grammar incompatibility
was found (see docs/MODEL_BENCHMARK_PLAN.md). Output goes to
data/bench_<label>_vanilla.jsonl instead of data/bench_<label>.jsonl so it
never collides with a grammar-on run of the same label.

Example:
    llama-server/llama-server.exe -m backend/models/Qwen_Qwen3-14B-Q4_K_M.gguf --port 8080 -ngl 999 -c 4096
    python scripts/bench_translation_model.py qwen3-14b
    python scripts/bench_translation_model.py qwen3-14b data/eval_set_2026-08-18_results.jsonl --vanilla
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

import sacrebleu

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from backend.translation.llama_server_engine import LlamaServerEngine  # noqa: E402

_LATIN_RE = re.compile(r"[A-Za-z]")
# Broader than Latin-only: anything outside Hangul/digits/whitespace/basic
# punctuation, i.e. every script the grammar mask would have blocked
# (Latin, Kana, CJK Han, Cyrillic, ...) — needed to see the full effect of
# turning script-purity enforcement off in the vanilla comparison.
_NON_KOREAN_RE = re.compile(
    r"[^가-힣ᄀ-ᇿ㄰-㆏0-9 .,!?~'\"()\- -⁯\n]"
)


async def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/bench_translation_model.py <model_label> [results.jsonl]")
        sys.exit(1)
    vanilla = "--vanilla" in sys.argv
    positional = [a for a in sys.argv[1:] if a != "--vanilla"]
    label = positional[0]
    in_path = (
        Path(positional[1])
        if len(positional) > 1
        else sorted(ROOT.glob("data/eval_set_*_results.jsonl"))[0]
    )
    records = [json.loads(line) for line in in_path.read_text(encoding="utf-8").splitlines()]
    print(f"[{label}] Loaded {len(records)} segments from {in_path.name} (vanilla={vanilla})")

    # Qwen3's chat template defaults to hybrid "thinking" mode, which fights
    # the Korean-only grammar (the model can't emit its <think> preamble
    # under the character-set constraint and leaks reasoning-style prose
    # into the visible content instead of a clean translation). "/no_think"
    # in the system prompt is Qwen3's documented switch to disable it.
    no_think_hint = "/no_think" if "qwen3" in label.lower() else None

    # The Korean-only grammar mask was tuned against Qwen2.5's failure modes.
    # Manual A/B testing isolated the actual breakage on Qwen3-14B to
    # repeat_penalty, not the grammar mask itself: grammar alone (no
    # repeat_penalty) still produces a usable translation wrapped in
    # preamble ("옳은 번역을 제공해 드리겠습니다...  <translation>"); adding
    # repeat_penalty on top collapses it into verbatim-echoing the system
    # prompt instead of translating at all. Keep grammar on for every
    # candidate (uniform script-purity enforcement) but drop repeat_penalty
    # for Qwen3 specifically.
    use_grammar = not vanilla
    use_repeat_penalty = (not vanilla) and "qwen3" not in label.lower()

    async def _translate_safe(text: str) -> str:
        # Vanilla (no grammar) runs can make the model emit degenerate
        # mixed-script garbage that llama-server's own chat-template output
        # parser rejects with a 500 ("does not match the expected
        # peg-native format") — a server-side parsing failure, unrelated to
        # our script-purity concern. Treat it as a translation failure
        # (empty hyp) rather than aborting the whole 120-segment run.
        try:
            result = await mt.translate(
                text,
                fast=False,
                glossary_hint=no_think_hint,
                use_grammar=use_grammar,
                use_repeat_penalty=use_repeat_penalty,
            )
            return result.text
        except Exception as e:  # noqa: BLE001
            print(f"  !! translate failed: {e}")
            return ""

    mt = LlamaServerEngine(timeout_s=120.0)
    for i, r in enumerate(records):
        r["hyp_ko"] = await _translate_safe(r["hyp_ja"])
        r["hyp_ko_from_ref"] = await _translate_safe(r["ja_ref"])
        print(f"[{i + 1}/{len(records)}] {r['id']}")
    await mt.aclose()

    out_path = ROOT / "data" / f"bench_{label}{'_vanilla' if vanilla else ''}.jsonl"
    with out_path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"Wrote -> {out_path}")

    report(label, records)


def report(label: str, records: list[dict]) -> None:
    groups = ["normal", "hard", "all"]
    print(f"\n=== [{label}] chrF++ (STT hyp vs ja_ref direct) ===")
    print(f"{'group':<8}{'n':<5}{'hyp_ja->ko':<12}{'ref_ja->ko':<12}{'latin_leak'}")
    for g in groups:
        rows = [r for r in records if g == "all" or r["group"] == g]
        chrf_hyp = sacrebleu.corpus_chrf(
            [r["hyp_ko"] for r in rows], [[r["ko_ref"] for r in rows]], word_order=2
        ).score
        chrf_ref = sacrebleu.corpus_chrf(
            [r["hyp_ko_from_ref"] for r in rows], [[r["ko_ref"] for r in rows]], word_order=2
        ).score
        latin_leaks = sum(1 for r in rows if _LATIN_RE.search(r["hyp_ko_from_ref"]))
        non_ko_leaks = sum(1 for r in rows if _NON_KOREAN_RE.search(r["hyp_ko_from_ref"]))
        print(
            f"{g:<8}{len(rows):<5}{chrf_hyp:<12.2f}{chrf_ref:<12.2f}"
            f"latin={latin_leaks} non_ko={non_ko_leaks}"
        )


if __name__ == "__main__":
    asyncio.run(main())
