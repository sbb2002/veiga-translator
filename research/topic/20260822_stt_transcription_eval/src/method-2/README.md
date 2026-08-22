# method-2: kotoba-whisper-v2.0-faster 정량/정성 평가 (method-1과 동일 조건)

## 무엇인가

method-1(large-v3, 현재 앱 채택 모델)과 정확히 같은 150쌍 데이터셋·같은 지표·같은
정규화로, STT 모델만 `kotoba-tech/kotoba-whisper-v2.0-faster`(HuggingFace, CTranslate2
변환판 distil-whisper 계열 일본어 특화 모델)로 교체해 비교한다 — 사용자 요청
(2026-08-22). 이 앱(백엔드/확장)은 건드리지 않는다는 원칙도 동일하게 적용.

## 어떻게 실행했는지

`method-1/README.md`와 절차 동일, 모델만 다름:

1. `python transcribe.py` — `faster_whisper.WhisperModel("kotoba-tech/kotoba-whisper-v2.0-faster", ...)`
   로 150개 클립 전사 → `out/method-2/transcripts.jsonl`. HF 저장소 ID를 바로 넘기면
   huggingface_hub가 자동 다운로드/캐시함(최초 실행 시 네트워크 필요).
2. `python score_quantitative.py` — 동일 지표(CER/chrF++/BLEU-char/ROUGE-L) →
   `out/method-2/quant_results.csv`, `quant_summary.json`.
3. `python judge_qualitative.py` — 동일 llama-server 판정 프롬프트 →
   `out/method-2/qual_results.jsonl`.

## 비교 시 유의점

- beam_size=5, condition_on_previous_text=False 등 STT 호출 파라미터는 method-1과
  동일하게 유지 — 모델 교체 외 변수를 통제.
- kotoba-whisper는 distil 계열이라 large-v3 대비 인코더/디코더 구조가 다름 — 속도
  비교 시 `stt_elapsed_s`도 함께 참고할 것.
