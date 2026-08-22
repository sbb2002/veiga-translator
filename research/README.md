# research (live-translator)

이 폴더는 새 알고리즘·모델·설정을 검증하는 연구 기록 전용 공간이다. `bandori-playlist-maker`의
`research` 브랜치 기록법을 벤치마크했다(2026-08-22) — 다만 이 저장소는 규모가 작아 별도 git
브랜치 대신 **현재 브랜치의 `research/` 폴더**로 둔다.

## 폴더 구성

폴더명은 `<연구시작일 YYYYMMDD>_<연구명>` 형식이다.

| 폴더 | 주제 | 상태 |
|---|---|---|
| [`topic/20260818_translation_model_benchmark/`](topic/20260818_translation_model_benchmark/README.md) | 번역 모델 벤치마크(Qwen2.5-7B 베이스라인 vs Qwen3-14B vs Gemma-3-12b-it vs EXAONE-3.5-7.8B) — `docs/eval/`에서 소급 이관 | **완료** — Gemma-3-12b-it 채택 |
| [`topic/20260822_stt_transcription_eval/`](topic/20260822_stt_transcription_eval/README.md) | 전사(STT) 품질 평가 — large-v3 vs kotoba-whisper vs large-v3-turbo 3-way 비교 | **완료** — large-v3-turbo 채택, 앱 반영 완료 |

각 폴더의 배경·방법·결과는 폴더 안의 `README.md`를 참조. 새 주제를 시작하면 위 표에 행을 추가한다.

## 주제 폴더 표준 구조

```
topic/<주제>/
├── src/           # 방법론별 소스코드 (method-N/README.md에 의도+실행법)
├── fig/           # 플롯 산출물
├── report/        # 방법론/실험 단위 소규모 보고서 (아래 공통 구조를 따름)
├── ref/           # 참고자료
└── paper.md       # 연구 종결 시 report/를 취합한 최종 요약
```

## 보고서 공통 작성 구조

`report/*.md`, `paper.md`는 아래 5개 절을 이 순서로 따른다:

1. **배경** — 왜 이 실험을 시작했는지.
2. **방법** — 항목(조건·방법론)별로 절을 나눠, 각각 ①용어 정리 ②실험 방법(순서 불렛) ③평가
   방법(판정 기준·score 정의)을 포함.
3. **결과** — 표/플롯 + 해석.
4. **결론** — 종합 결론.
5. **레퍼런스** — 본문에 못 담은 상세 내용의 출처 경로 (없으면 생략).

## 새 연구 주제를 시작할 때

`topic/` 아래에 새 폴더를 추가하는 것으로 시작한다. 결과가 채택되면 `backend/`·`extension/`
코드에 정식 반영하고, 이 폴더의 결과 문서는 근거 기록으로 그대로 둔다.
