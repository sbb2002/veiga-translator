"""Parity check: HallucinationGate sentence-transformers backend vs onnx backend.

Usage (from repo root):  python scripts/verify_gate_parity.py

Embeds the same texts with both backends and reports
  (a) the minimum per-text cosine similarity between the two embeddings, and
  (b) every text where is_known_hallucination() verdicts disagree.
Texts = known_hallucinations.json phrases + unique "text" fields from
data/flagged_segments.jsonl + a fixed set of ordinary Japanese sentences.

This script needs torch/sentence-transformers (the reference backend), so it is
a dev-only check, not part of the runtime path.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import config  # noqa: E402
from backend.hallucination_gate import HallucinationGate  # noqa: E402

PHRASES_PATH = ROOT / "backend" / "known_hallucinations.json"
FLAGGED_PATH = ROOT / "data" / "flagged_segments.jsonl"

GENERAL_JA = [
    "今日はとても暑いですね。",
    "明日の天気はどうなるでしょうか。",
    "この店のラーメンは本当に美味しいです。",
    "じゃあ、次の話題に移りましょう。",
    "昨日、友達と一緒に映画を見に行きました。",
    "えっと、それはちょっと難しいと思います。",
    "新しいゲームが来月発売されるそうです。",
    "すみません、駅はどこにありますか。",
    "そろそろ時間なので、今日はここまでにします。",
    "皆さんの質問にお答えしていきたいと思います。",
]


def load_texts() -> list[str]:
    phrases = json.loads(PHRASES_PATH.read_text(encoding="utf-8"))["phrases"]
    flagged: list[str] = []
    seen: set[str] = set()
    for line in FLAGGED_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        text = json.loads(line).get("text", "")
        if text and text not in seen:
            seen.add(text)
            flagged.append(text)
    texts = []
    for t in phrases + flagged + GENERAL_JA:
        if t not in texts:
            texts.append(t)
    return texts


def build_gate(backend: str) -> HallucinationGate:
    config.HALLUCINATION_GATE_BACKEND = backend
    return HallucinationGate()


def main() -> int:
    texts = load_texts()
    print(f"texts: {len(texts)}")

    st_gate = build_gate("sentence-transformers")
    onnx_gate = build_gate("onnx")

    st_emb = st_gate._model.encode(texts, normalize_embeddings=True)
    onnx_emb = onnx_gate._model.encode(texts, normalize_embeddings=True)
    cos = np.sum(st_emb * onnx_emb, axis=1)
    worst = int(np.argmin(cos))
    print(f"(a) cosine min = {cos.min():.6f}  (mean {cos.mean():.6f}, worst text: {texts[worst]!r})")

    mismatches = []
    for t in texts:
        a = st_gate.is_known_hallucination(t)
        b = onnx_gate.is_known_hallucination(t)
        if (a is None) != (b is None) or a != b:
            mismatches.append((t, a, b))
    print(f"(b) verdict mismatches = {len(mismatches)}")
    for t, a, b in mismatches:
        print(f"    text={t!r}  st={a!r}  onnx={b!r}")

    ok = cos.min() >= 0.999 and not mismatches
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
