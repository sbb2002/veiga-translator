# 전사(STT) 품질 평가

## 배경

`docs/eval/EVAL.md`에 STT/번역 분리 채점 방법론은 이미 설계돼 있지만, 지금까지 large-v3
STT에 대해 정식 정량 평가(CER 등)를 돌린 적이 없다. 현재 모델 선택(large-v3)과 각종
게이팅 임계값들은 모두 라이브 정성 관찰로 정해진 "작업 가설" 상태다(`CLAUDE.md` STT 항목
참고). 정량 평가를 시작하기 전에, 지금 실제로 뭐가 돌고 있는지(모델·설정·파이프라인 각
레이어의 책임과 현재 값)를 먼저 정확히 문서화한다 — 이후 실험(설정 변경 A/B, 데이터셋
채점)의 기준선이 된다.

## 진행 단계

| 단계 | 내용 | 상태 |
|---|---|---|
| 1 | 현재 모델/파이프라인 레이어 기술 (실험 없음, 코드 근거 문서화) | **완료** — [`report/01-current-model-and-pipeline.md`](report/01-current-model-and-pipeline.md) |
| 2 | 평가 데이터셋(`ja_ref` 포함) 구축 | **완료** — `data/wav`+`data/json` 기존 코퍼스 재사용(150쌍, 5카테고리) |
| 3 | large-v3 정량(CER/chrF++/BLEU/ROUGE-L)+정성(LLM 채점) 평가 | **완료** — `src/method-1/` |
| 4 | kotoba-whisper-v2.0-faster 동일 조건 비교 | **완료** — `src/method-2/` |
| 5 | large-v3-turbo 동일 조건 비교 | **완료** — `src/method-3/` |
| 6 | 3-way 종합 리포트 | **완료** — [`report/02-largev3-vs-kotoba-whisper.md`](report/02-largev3-vs-kotoba-whisper.md) — **결론: large-v3-turbo가 품질 동급·속도 11.4배로 최선** |

## 산출물

- `report/01-current-model-and-pipeline.md` — 현재 STT 모델·파이프라인 레이어 기술.
- `report/02-largev3-vs-kotoba-whisper.md` — large-v3 vs kotoba-whisper-v2.0-faster vs
  large-v3-turbo 3-way 정량/정성 비교.
- `src/method-1/`, `src/method-2/`, `src/method-3/` — 각 모델 평가 스크립트+결과(`out/`).
