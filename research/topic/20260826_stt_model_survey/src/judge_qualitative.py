"""LLM-assisted qualitative pass: for each (ja_ref, hyp) pair, ask a judge
model (llama-server/gemma, already running at :8080, pure text-comparison
tool — no backend/ import) whether the hypothesis preserves the reference's
meaning. Same 3-way verdict scheme as 20260822_stt_transcription_eval.
Shared across methods here via --method (out/<method>/ subdir).

Usage: python judge_qualitative.py --method turbo [--limit N]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx

OUT_ROOT = Path(__file__).resolve().parents[1] / "out"
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
    parser.add_argument("--method", required=True, help="out/<method>/ subdir name")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    out_dir = OUT_ROOT / args.method
    records = [json.loads(line) for line in (out_dir / "transcripts.jsonl").open(encoding="utf-8")]
    if args.limit:
        records = records[: args.limit]

    out_path = out_dir / "qual_results.jsonl"
    with httpx.Client() as client, out_path.open("w", encoding="utf-8") as out_f:
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

    print(f"done -> {out_path}")


if __name__ == "__main__":
    main()
