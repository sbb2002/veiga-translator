# method-3: large-v3-turbo 정량/정성 평가 (method-1/2와 동일 조건)

## 무엇인가

method-1(large-v3)·method-2(kotoba-whisper-v2.0-faster)와 정확히 같은 150쌍
데이터셋·같은 지표·같은 정규화로, STT 모델만 `large-v3-turbo`(faster-whisper 내장
별칭, 실제로는 `mobiuslabsgmbh/faster-whisper-large-v3-turbo`)로 교체해 비교한다 —
사용자 요청(2026-08-22). 이 앱(백엔드/확장)은 건드리지 않는다는 원칙도 동일.

## 어떻게 실행했는지

`method-1/README.md`와 절차 동일, 모델만 다름:

1. `python transcribe.py` — `faster_whisper.WhisperModel("large-v3-turbo", ...)`로
   150개 클립 전사 → `out/method-3/transcripts.jsonl`.
2. `python score_quantitative.py` — 동일 지표(CER/chrF++/BLEU-char/ROUGE-L) →
   `out/method-3/quant_results.csv`, `quant_summary.json`.
3. `python judge_qualitative.py` — 동일 llama-server 판정 프롬프트 →
   `out/method-3/qual_results.jsonl`.

## 비교 시 유의점

- turbo는 large-v3의 디코더 레이어를 4개로 줄인 경량화 버전 — 인코더는 large-v3와
  동일. kotoba-whisper(distil, 별도 파인튜닝)와는 경량화 방식 자체가 다르므로 속도/
  품질 트레이드오프 양상도 다를 수 있음.
