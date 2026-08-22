"""LLM-assisted qualitative pass: for each (ja_ref, hyp) pair, ask a judge
model whether the STT hypothesis preserves the reference's meaning.

Calls llama-server's OpenAI-compatible endpoint directly (already running,
independent process — started via `llama-server/llama-server.exe -m
backend/models/google_gemma-3-12b-it-Q4_K_M.gguf --port 8080`). This does
NOT touch the live-translator app (no backend/ import, no uvicorn, no
websocket session) — it's the same judge model used purely as a generic
text-comparison tool here.

3-way verdict (일치/부분일치/불일치), matching the pass/fail-with-reason style
used for translation grading in docs/eval/EVAL.md, adapted for a same-
language (ja vs ja) meaning-preservation check. Output is spot-checked by
the user afterward (this script only pre-labels).

Usage: python judge_qualitative.py [--limit N]
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import httpx

OUT_DIR = Path(__file__).resolve().parents[2] / "out" / "method-1"
TRANSCRIPTS_PATH = OUT_DIR / "transcripts.jsonl"
OUT_PATH = OUT_DIR / "qual_results.jsonl"

LLAMA_SERVER_URL = "http://127.0.0.1:8080"
TIMEOUT_S = 30.0

_SYSTEM_PROMPT = """당신은 일본어 음성인식(STT) 품질 평가자입니다. REFERENCE(정답 전사)와
HYPOTHESIS(모델이 인식한 결과), 두 일본어 문장을 비교해 의미가 얼마나 보존됐는지 판정합니다.

판정 기준:
- "일치": 핵심 의미가 REFERENCE와 동일함 (표기/조사 등 사소한 차이는 무시).
- "부분일치": 일부 정보가 달라지거나 누락됐지만 전체 맥락은 통함.
- "불일치": 핵심 의미가 달라졌거나 완전히 다른 내용임.

반드시 아래 JSON 형식 한 줄로만 답하세요. 다른 텍스트를 추가하지 마세요.
{"verdict": "일치|부분일치|불일치", "reason": "판정 근거를 한국어 한 문장으로"}
"""

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def judge_one(client: httpx.Client, ja_ref: str, hyp: str) -> dict:
    user_msg = f"REFERENCE: {ja_ref}\nHYPOTHESIS: {hyp}"
    resp = client.post(
        f"{LLAMA_SERVER_URL}/v1/chat/completions",
        json={
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            "temperature": 0.0,
            "max_tokens": 150,
        },
        timeout=TIMEOUT_S,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    match = _JSON_RE.search(content)
    if not match:
        return {"verdict": "판정실패", "reason": f"JSON 파싱 실패: {content!r}"}
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {"verdict": "판정실패", "reason": f"JSON 파싱 실패: {content!r}"}
    return {
        "verdict": parsed.get("verdict", "판정실패"),
        "reason": parsed.get("reason", ""),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    records = []
    with TRANSCRIPTS_PATH.open(encoding="utf-8") as f:
        for line in f:
            records.append(json.loads(line))
    if args.limit:
        records = records[: args.limit]

    OUT_PATH.parent.mkdir(exist_ok=True)
    with httpx.Client() as client, OUT_PATH.open("w", encoding="utf-8") as out_f:
        for i, r in enumerate(records, 1):
            verdict = judge_one(client, r["ja_ref"], r["hyp"])
            row = {
                "seg_id": r["seg_id"],
                "category": r["category"],
                "ja_ref": r["ja_ref"],
                "hyp": r["hyp"],
                **verdict,
            }
            out_f.write(json.dumps(row, ensure_ascii=False) + "\n")
            out_f.flush()
            if i % 10 == 0 or i == len(records):
                print(f"[{i}/{len(records)}] {r['seg_id']} -> {verdict['verdict']}")

    print(f"done -> {OUT_PATH}")


if __name__ == "__main__":
    main()
