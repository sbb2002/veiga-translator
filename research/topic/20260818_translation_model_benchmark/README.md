# 번역 모델 벤치마크 (2026-08-18, 소급 기록)

## 배경

이 폴더는 `research/` 폴더 체계를 만들기 전(2026-08-18)에 `docs/eval/`에 직접 작성됐던
번역 모델 벤치마크를 **소급해서 research 양식으로 옮겨 적은 것**이다(2026-08-22, 사용자
요청 — 재실험 없이 기존 결과를 정리만 함). 원본 문서는 그대로 `docs/eval/`에 남아있고,
이 폴더는 같은 내용을 `research/README.md`의 보고서 5절 구조로 재정리한 버전이다.

## 진행 단계

| 단계 | 내용 | 상태 |
|---|---|---|
| 1 | Qwen2.5-7B-Instruct 베이스라인 평가 (120클립, EVAL.md 방법론) | **완료** (원본: `docs/eval/EVAL_REPORT_2026-08-18.md`) |
| 2 | Qwen3-14B / Gemma-3-12b-it / EXAONE-3.5-7.8B 비교 벤치마크 | **완료** (원본: `docs/eval/MODEL_BENCHMARK_PLAN.md`) |
| 3 | Gemma-3-12b-it 채택 여부 상세 평가(사람 채점 포함) | **완료** — 채택 확정 (원본: `docs/eval/EVAL_REPORT_gemma-3-12b-it_2026-08-18.md`) |

## 산출물

- `report/01-translation-model-benchmark.md` — 위 세 원본 문서를 종합한 단일 보고서.
