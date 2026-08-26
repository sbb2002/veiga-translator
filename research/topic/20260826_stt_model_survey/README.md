# HF ASR 후보 모델 서베이 (CPU 환경)

## 배경

HuggingFace에 새로 올라온 오픈소스 ASR 모델 9종이 현재 채택 모델
(large-v3-turbo, `20260822_stt_transcription_eval`)의 대안이 될 수 있는지
사용자가 조사를 요청했다(2026-08-26). 개발 환경이 현재 **CPU만 사용 가능**이라,
GPU가 필요한 모델은 계획서만 작성하고 실행하지 않는다는 조건.

## 진행 단계

| 단계 | 내용 | 상태 |
|---|---|---|
| 1 | 9개 후보 모델 CPU 실행 가능성 + 일본어 지원 조사 | **완료** — [`report/01-model-scoping.md`](report/01-model-scoping.md) — 3개(granite-speech-4.1-2b, Qwen3-ASR-0.6B/1.7B-hf)만 CPU+일본어 조건 충족, 나머지 6개(canary 3종, parakeet 2종, granite-speech-5.0-470m)는 일본어 미지원으로 제외(GPU 필요 여부와 무관) |
| 2 | 3개 후보 + large-v3-turbo(baseline) 25쌍(5카테고리x5) CPU 파일럿 | **완료** — [`report/02-pilot-results.md`](report/02-pilot-results.md) — **Qwen3-ASR-0.6B-hf가 turbo와 동급 품질에 RTF 3.5배 빠름**, 유력 후보 |
| 3 | 편향 재확인(무작위 재표본) + 150쌍 확장 + 정성 채점 | 사용자 결정 대기 |

## 산출물

- `report/01-model-scoping.md` — 9개 모델 CPU/일본어 지원 스코핑, 제외 근거.
- `report/02-pilot-results.md` — 4개 방법(turbo/granite-speech-4.1-2b/
  qwen3-asr-0.6b/qwen3-asr-1.7b) 25쌍 CPU 파일럿 정량 비교 + 겪은 이슈 기록.
- `src/common.py` — 데이터셋 로더(파일럿 서브셋 지원) + 정규화, 4개 방법 공유.
- `src/transcribe_turbo.py`, `src/transcribe_granite.py`,
  `src/transcribe_qwen3_asr.py` — 방법별 전사 스크립트.
- `src/score_quantitative.py`, `src/judge_qualitative.py` — 채점 스크립트
  (`--method` 인자로 out/ 서브디렉터리 선택, 4개 방법 공유).
- `out/turbo/`, `out/granite-speech-4.1-2b/`, `out/qwen3-asr-0.6b/`,
  `out/qwen3-asr-1.7b/` — 방법별 전사·채점 결과.
