"""Measure Groq prompt-cache hit rate for the real FINAL translation prompt.

Sends real session finals (data/sessions/*.jsonl) one at a time, sequentially,
with the production FINAL_SYSTEM_PROMPT, and records usage.cached_tokens.
Never bursts: each request waits until the rolling 60s token total plus the
next request's worst-case size stays under 0.8 * the account's TPM limit
(read from the x-ratelimit-limit-tokens response header, or --tpm). A cumulative
cap (--daily-tokens * 0.8) and --max-requests bound the whole run. Any 429
aborts the run instead of retrying.

Usage (key is read from the environment only, never printed):
    set GROQ_API_KEY=...            (cmd)   /   $env:GROQ_API_KEY="..."  (PowerShell)
    python scripts/measure_groq_cache.py --max-requests 30 --daily-tokens 200000
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import sys
import time
from collections import deque
from pathlib import Path

import httpx
import tiktoken

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.translation import prompts  # noqa: E402

URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "openai/gpt-oss-120b"
MAX_COMPLETION = 256  # counts against TPM at request time; keep it small and bounded
SAFETY = 0.8

enc = tiktoken.get_encoding("o200k_base")


def load_finals(pattern: str) -> list[tuple[str, str]]:
    pairs = []
    for f in sorted(glob.glob(pattern)):
        for line in open(f, encoding="utf-8"):
            e = json.loads(line)
            if e.get("type") == "final" and e.get("text"):
                pairs.append((e["text"], e.get("translation", "")))
    return pairs


def build_user_message(pairs: list[tuple[str, str]], i: int) -> str:
    # Mirrors LlamaServerEngine.translate(): [PREVIOUS SENTENCE]/[PREVIOUS TRANSLATION]
    # from the last 3 finals, then [TEXT TO TRANSLATE].
    hist = pairs[max(0, i - 3) : i]
    text = pairs[i][0]
    if not hist:
        return text
    ja = "\n".join(f"{n}. {a}" for n, (a, _) in enumerate(hist, 1))
    ko = "\n".join(f"{n}. {b}" for n, (_, b) in enumerate(hist, 1))
    return f"[PREVIOUS SENTENCE]\n{ja}\n\n[PREVIOUS TRANSLATION]\n{ko}\n\n[TEXT TO TRANSLATE]\n{text}"


def main() -> None:
    ap = argparse.ArgumentParser()
    # data/sessions/ is gitignored; in this setup the real logs live in the profile/eval
    # worktree outside the repo (../lt-wt/eval/data/sessions/) — pass that via --sessions.
    ap.add_argument("--sessions", default="data/sessions/*.jsonl")
    ap.add_argument("--max-requests", type=int, default=30)
    ap.add_argument("--tpm", type=int, default=0, help="override TPM limit (0 = read from headers)")
    ap.add_argument("--daily-tokens", type=int, default=200_000, help="account TPD limit; run stops at 0.8x of it")
    ap.add_argument("--out", default="groq_cache_measure.csv")
    args = ap.parse_args()

    key = os.environ.get("GROQ_API_KEY")
    if not key:
        sys.exit("GROQ_API_KEY is not set in the environment")

    pairs = load_finals(args.sessions)
    if len(pairs) < 4:
        sys.exit(f"not enough finals found for {args.sessions!r}")
    system_prompt = prompts.FINAL_SYSTEM_PROMPT

    window: deque[tuple[float, int]] = deque()  # (time, total_tokens used)
    used_total = 0
    tpm = args.tpm
    rows = []
    last_sent = None
    client = httpx.Client(timeout=60)

    for i in range(1, min(len(pairs), args.max_requests + 1)):
        user = build_user_message(pairs, i)
        est = len(enc.encode(system_prompt)) + len(enc.encode(user)) + 20 + MAX_COMPLETION

        if used_total + est > args.daily_tokens * SAFETY:
            print("stopping: cumulative budget (0.8 x daily) reached")
            break
        if tpm:
            while True:
                now = time.time()
                while window and now - window[0][0] > 60:
                    window.popleft()
                if sum(t for _, t in window) + est <= tpm * SAFETY:
                    break
                time.sleep(max(1.0, 60 - (now - window[0][0]) + 0.5))

        gap = None if last_sent is None else time.time() - last_sent
        resp = client.post(
            URL,
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.0,
                "max_completion_tokens": MAX_COMPLETION,
                "reasoning_effort": "low",
            },
        )
        last_sent = time.time()
        if resp.status_code == 429:
            sys.exit(f"429 received — aborting without retry (retry-after={resp.headers.get('retry-after')})")
        resp.raise_for_status()
        if not tpm:
            tpm = int(resp.headers.get("x-ratelimit-limit-tokens", "0")) or 8000
            print(f"TPM limit from headers: {tpm} (safe ceiling {int(tpm * SAFETY)}/min)")
        usage = resp.json()["usage"]
        total = usage["total_tokens"]
        window.append((last_sent, total))
        used_total += total
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
        reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", "")
        rows.append(
            {
                "idx": i,
                "gap_s": None if gap is None else round(gap, 1),
                "prompt_tokens": usage["prompt_tokens"],
                "cached_tokens": cached,
                "completion_tokens": usage["completion_tokens"],
                "reasoning_tokens": reasoning,
                "total_tokens": total,
            }
        )
        print(rows[-1], flush=True)
        # Pace the next request from the actual spend, not only the estimate
        if tpm:
            time.sleep(max(0.0, total / (tpm * SAFETY) * 60 - (time.time() - last_sent)))

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    p = sum(r["prompt_tokens"] for r in rows)
    c = sum(r["cached_tokens"] for r in rows)
    hits = sum(1 for r in rows if r["cached_tokens"] > 0)
    print(f"requests={len(rows)} prompt={p} cached={c} ({c / p * 100:.1f}%) hit_requests={hits} total_used={used_total}")


if __name__ == "__main__":
    main()
