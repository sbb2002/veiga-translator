# 오픈소스 번역 모델 서베이 — LLM 8종 + NMT 2종

## 배경

`research/topic/20260818_translation_model_benchmark`에서 4개 모델(Qwen2.5-7B/
Qwen3-14B/Gemma-3-12b-it/EXAONE-3.5-7.8B)을 비교해 Gemma-3-12b-it를 채택했지만,
Qwen3-14B가 순수 품질 1위였음에도 GBNF grammar 비호환으로 미채택됐고 32B급
후보는 애초 VRAM 부족으로 실행조차 안 됐다. 사용자 요청(2026-08-26)으로 오픈소스
번역 모델 전반(범용 LLM + 번역 전용 LLM + 전용 NMT)을 재조사해 "모든 모델을 다
시도"하기로 했다.

## 방법

- 데이터셋: `data/eval_set_2026-08-18.jsonl`(120클립, `docs/eval/EVAL.md` 방법론) —
  전체 사용, 서브셋 없음. `ja_ref`를 직접 번역해 `ko_ref`와 비교(STT 오류 배제 조건,
  기존 벤치마크와 동일 관례).
- 지표: **chrF++**(`sacrebleu.corpus_chrf`, word_order=2, `EVAL.md` §3.1과 동일) +
  **RTF**(신규 — `번역 소요시간 합 / 원본 오디오 길이 합`, STT 서베이와 동일 정의).
  95% CI는 페어드 부트스트랩(500회 재표본, STT 서베이와 동일 방법론).
- **10개 방법 전부 grammar(`_KOREAN_ONLY_GRAMMAR`) 없이(vanilla) 실행** — 이유는
  `report/02-results.md`의 "겪은 이슈" 참고(grammar가 llama-server를 멈추게 하는
  버그를 발견했고, 애초 이번 서베이 목적이 "순수 번역 모델 성능 비교"라 grammar를
  뺀 게 방법론적으로도 맞음, 사용자 확인).

## 후보 10개(제외 1개, 보류 1개) — 상세는 `report/01-model-scoping.md`

| 구분 | 후보 |
|---|---|
| 범용 LLM(기존 3개, 재실행) | Qwen2.5-7B-Instruct, Gemma-3-12b-it, EXAONE-3.5-7.8B |
| 범용 LLM(기존 1개, 재실행) | Qwen3-14B(grammar off, CI 신규 확보 — 기존 실행분은 원본 데이터 유실로 CI 불가했음) |
| 범용 LLM(신규) | Qwen3-32B(Q3_K_S), EXAONE-4.0-32B(Q3_K_S), Llama-3-8B-Instruct |
| 번역 전용 LLM(신규) | Seed-X-Instruct-7B(raw completion, 전용 스크립트) |
| 전용 NMT(신규) | NLLB-200-3.3B, MADLAD-400-3B-MT |
| 제외 | TowerInstruct — 일본어 미지원(지원 언어에 일본어 없음) |
| 보류 | ALMA-R/X-ALMA — 언어 지원 확인 안 됨, 다음 라운드 후보 |

## 결과 요약

자세한 내용과 해석은 [`report/02-results.md`](report/02-results.md) 참고.

핵심: **Gemma-3-12b-it(현재 프로덕션)가 chrF++ 점추정치 1위(31.10)지만 Qwen3-14B
(30.76)/Qwen3-32B(30.17)와 CI가 겹쳐 통계적 확정 우위는 아니다.** 번역 전용
모델(Seed-X)과 전용 NMT(NLLB/MADLAD)는 예상과 반대로 전부 범용 LLM보다 확실히
나빴다 — MADLAD-400은 반복 루프로 사실상 전멸(118/120 거의 0점), EXAONE-4.0-32B는
번역 대신 설명문을 내는 지시 따르기 실패(65/120), Seed-X는 커뮤니티 양자화판
불안정 추정. **현재 프로덕션을 교체할 근거는 이번에도 없음.**

상위 4개(Gemma-3-12b-it/Qwen3-14B/Qwen3-32B/EXAONE-3.5-7.8B)는 CI가 서로 겹쳐
지표로 우열을 못 가린다 — `report/03-ci-overlap-and-qualitative-check.md`에서
①어느 쌍이 실제로 겹치는지 pairwise로 확인하고 ②20세그먼트를 직접 대조해 정성
비교했다. 결론: 4개는 정량은 동급이지만 정성적으로 서로 다른 실패 패턴을 가진다
— Gemma는 드물게 번역을 아예 안 하고 원문을 echo, Qwen3 계열(14B/32B)은 한자
유출이 반복(`20260818` 토픽에서 이미 확인된 약점 재현), EXAONE-3.5는 스크립트는
가장 깨끗하지만 어려운 구간을 회피/얼버무리는 경향.

**정성 평가 전량화(2026-08-27, `report/04-qualitative-eval-full.md`)**: `report/03`의
20세그먼트 서술을 STT 서베이와 동일하게 점수화(120세그먼트 × 10방법 × 세 축
1~5: 의미 충실도 / 유창성 / 뉘앙스 이전, 사람 수동 채점). 핵심:
- **의미 충실도 통계적 동급 클리크는 4개가 아니라 3개** — Gemma-3-12b-it(4.10) /
  Qwen3-14B(4.12) / Qwen3-32B(4.12). EXAONE-3.5-7.8B(3.83)는 세 모델 대비 페어드
  차 CI가 0을 제외해 유의미하게 낮다(어려운 구간 회피가 점수로 드러남).
- **Gemma가 뉘앙스 이전 1위**(3.92) + **유창한 환각률 0%** — 틀리면 일본어 원문을
  echo하므로(fail-loud) 탐지가 쉽다. Qwen3-14B/EXAONE-3.5는 틀려도 유창성 4점대
  유지(유창한 오역, 탐지 어려움).
- **뉘앙스 이전 축은 의미 충실도와 r=0.96** — 사실상 독립 축이 아니다.
- 프로덕션 Gemma-3-12b-it 교체 근거는 정성으로도 없음.

## 산출물

- `report/01-model-scoping.md` — 10개 후보 스코핑, 제외/보류 근거.
- `report/02-results.md` — chrF++/RTF 정량 결과, 세그먼트별 진단, 겪은 이슈, 결론.
- `report/03-ci-overlap-and-qualitative-check.md` — 상위 4개 CI pairwise 겹침 확인
  + 20세그먼트 정성 비교(모델별 실패 패턴).
- `report/04-qualitative-eval-full.md` — 전체 120세그먼트 × 10방법 수동 정성 채점
  (의미 충실도 / 유창성 / 뉘앙스 이전 1~5), 카테고리별·hard 그룹별 표, 축 간 상관,
  유창성 분해.
- `src/qualitative_eval.py` — 정성 전량 로드/집계/자체검증.
- `out/qualitative_sample.txt`, `out/qualitative_scores.json` — 대조표 + 점수.
- `src/common.py` — 데이터셋 로더(120클립, duration_s 교차 참조).
- `src/translate_llm.py` — GGUF LLM 8종 공용(llama-server 필요, grammar/repeat_penalty
  옵션, LLAMA_SERVER_TIMEOUT_S 상향 패치 포함).
- `src/translate_seedx.py` — Seed-X 전용(raw completion, `/completion` 엔드포인트).
- `src/translate_nmt.py` — NLLB-200/MADLAD-400 공용(transformers, `reazonspeech`
  conda env 필요 — torch 2.6 요구사항).
- `src/score_chrf.py` — chrF++ + 라틴 문자 유출 채점.
- `src/analyze_ci_and_plot.py` — 페어드 부트스트랩 95% CI + `fig/*.png` 생성.
- `src/sample_top4_qualitative.py` — 상위 4개 정성 비교용 무작위 샘플 추출(seed=7).
- `out/<method>/` — 10개 방법 전사·채점 결과. `out/ci_summary.json` — CI.
  `out/top4_qualitative_sample.txt` — 상위 4개 20세그먼트 대조 전문.
- `fig/chrf.png`, `fig/rtf.png`.

## 레퍼런스

- 이전 번역 벤치마크(4개 모델, grammar 켬, CI 부분적): `research/topic/20260818_translation_model_benchmark/`.
- STT 서베이(같은 방법론의 원형): `research/topic/20260826_stt_model_survey_gpu_full/`.
