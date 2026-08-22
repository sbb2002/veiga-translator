# method-1: large-v3 정량/정성 평가 (앱 파이프라인 없이, 모델 단독)

## 무엇인가

`data/wav/<category>/<contentsIdx>/*.wav` + `data/json/.../*.json`(150쌍, 5개 카테고리
x 30개)를 이용해 현재 채택된 STT 모델(faster-whisper large-v3, final-pass 설정)의
전사 품질을 정량(다중 지표) + 정성(LLM 채점 보조)으로 평가한다.

**사용자 지시(2026-08-22): 이 앱(백엔드/확장)을 건드리지 않고 전사 모델만 사용** —
`backend/` import 없이 `faster_whisper.WhisperModel`을 직접 호출한다. VAD, glossary
hotwords, hallucination gate 등 앱의 게이팅 레이어는 전부 배제하고 모델 자체의
원본 출력만 본다.

## 어떻게 실행했는지

1. `python common.py` — 데이터셋 로딩 자가 점검(150개, 카테고리별 30개 확인).
2. `python transcribe.py` — `data/wav`의 150개 클립을 large-v3(cuda, int8_float16,
   beam=5, condition_on_previous_text=False — 클립이 서로 독립적이므로)로 전사해
   `out/transcripts.jsonl`에 저장(참조 텍스트·카테고리·소요시간 포함).
3. `python score_quantitative.py` — `out/transcripts.jsonl`을 `ja_ref`와 비교해
   CER/chrF++/BLEU(char)/ROUGE-L(char LCS)을 세그먼트별 + 카테고리별 + 전체로 산출.
   `out/quant_results.csv`(세그먼트별), `out/quant_summary.json`(집계).
4. `python judge_qualitative.py` — 이미 떠 있는 llama-server(gemma, 8080)를 순수
   텍스트 비교 도구로만 사용해 `(ja_ref, hyp)` 쌍마다 일치/부분일치/불일치 + 근거를
   산출, `out/qual_results.jsonl`. **주의**: llama-server가 8080에서 먼저 떠 있어야
   함(`llama-server/llama-server.exe -m backend/models/google_gemma-3-12b-it-Q4_K_M.gguf
   --port 8080 -ngl 999 -c 4096`) — 앱(backend uvicorn)은 안 띄워도 됨.

정규화는 두 단계 모두 동일하게 `common.normalize_ja`(NFKC + `docs/eval/EVAL.md` §2.1
구두점/공백 제거)를 적용해, 지표 간 비교가 같은 텍스트 기준이 되도록 했다.
