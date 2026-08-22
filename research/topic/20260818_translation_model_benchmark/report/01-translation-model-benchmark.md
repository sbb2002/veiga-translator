# 01. 번역 모델 벤치마크 — Qwen2.5-7B 베이스라인 vs Qwen3-14B vs Gemma-3-12b-it vs EXAONE-3.5-7.8B

> 소급 기록(2026-08-22): 이 보고서는 2026-08-18에 `docs/eval/`에서 진행된 작업을 재실험 없이
> research 양식으로 재정리한 것이다. 원본은 `docs/eval/EVAL_REPORT_2026-08-18.md`,
> `docs/eval/MODEL_BENCHMARK_PLAN.md`, `docs/eval/EVAL_REPORT_gemma-3-12b-it_2026-08-18.md`
> 세 문서에 나뉘어 있다.

## 배경

번역 파이프라인 초기 채택 모델은 Qwen2.5-7B-Instruct Q4_K_M이었다. 2026-08-18 첫 정식
평가(`docs/eval/EVAL.md` 방법론)에서, STT 오류를 완전히 배제하고 정답 전사(`ja_ref`)를
직접 번역기에 넣어도 chrF++가 24.25에 그쳐("잘 되는 MT" 기준 40~60+에 크게 못 미침),
번역 엔진 자체의 품질 한계가 STT 개선보다 먼저 해결해야 할 병목으로 확인됐다. 이에 따라
다른 개선(오디오 게인 정규화, glossary 등)보다 번역 모델 교체를 먼저 검증하기로 했다.

원래 계획은 Qwen3-32B/EXAONE-4.0-32B/Gemma-3-27B-it(각 19~20GB) 비교였으나, 실행
머신이 RTX 4080 SUPER(16GB)여서 Q4_K_M으로도 안 들어가 더 작은 사이즈(Qwen3-14B,
Gemma-3-12b-it, EXAONE-3.5-7.8B)로 대체해 실행했다.

## 방법

### 용어 정리

- **평가셋**: `data/eval_set_2026-08-18.jsonl` — `data/wav`/`data/json`의 5개 카테고리
  150클립 중 **단일 화자**(`li_total_speaker_num == "1"`) **120클립**만 사용(2차 목표인
  다중 화자는 범위 밖). `normal`(94)/`hard`(26) 그룹 태그, `has_proper_noun` 플래그 포함.
- **hyp_ja**: CPU 환경(faster-whisper `medium` int8)으로 미리 한 번 뽑아 모든 모델
  벤치마크에서 재사용한 동일 STT 가설 — 번역 모델 비교는 이 값을 고정 입력으로 삼아
  **STT 변수를 완전히 통제**한 순수 A/B다.
- **hyp_ko_from_ref**: `ja_ref`(정답 전사)를 STT 없이 직접 번역기에 넣은 결과 — STT
  오류를 완전히 배제한 "번역 엔진 자체 순수 실력" 측정용.
- **chrF++**: `sacrebleu.corpus_chrf(hyp, [ref], word_order=2)`.
- **사람(LLM) 채점**: `docs/eval/EVAL.md` §3.2 — 의미충실도/자연스러움/존댓말일치/
  스크립트순수성(1~5점) + S1(핵심 의미 파손) 자동 실패 조건. 전 세그먼트를 Claude가 직접
  채점.
- **S1 원인 분리**: `docs/eval/EVAL.md` §3.4 — S1 세그먼트마다 STT 출력이 `ja_ref`와
  일치하는지 확인해 "STT 전파" vs "번역 자체" 원인으로 분리.

### 실험 방법

1. `scripts/build_eval_set.py`로 120클립 참조셋 구성.
2. `scripts/run_eval.py`로 고정된 `hyp_ja`를 각 모델(llama-server로 교체 기동)에 통과시켜
   `hyp_ko`(실제 파이프라인 조건) 생성.
3. `scripts/eval_stt_propagation.py`로 `ja_ref`를 직접 번역해 `hyp_ko_from_ref` 생성(STT
   영향 배제 조건).
4. 모델별로 아래를 반복:
   1. Qwen2.5-7B-Instruct(베이스라인, grammar 켬) — `docs/eval/EVAL_REPORT_2026-08-18.md`.
   2. Qwen3-14B — grammar(`_KOREAN_ONLY_GRAMMAR`)를 켠 채로는 chrF++가 6.5~8.7까지
      붕괴(아래 "중요 발견" 참고)해 **grammar를 끈 채(`use_grammar=False`)** 실행.
   3. Gemma-3-12b-it(grammar 켬) — `docs/eval/EVAL_REPORT_gemma-3-12b-it_2026-08-18.md`.
      `hyp_ja`는 베이스라인과 동일 값 재사용, 번역 모델만 바뀐 순수 A/B.
   4. EXAONE-3.5-7.8B-Instruct(grammar 켬, 한국어 네이티브 특화로 선정했으나 결과는
      베이스라인보다 낮음).
5. `sacrebleu.corpus_chrf`로 `hyp_ko`/`hyp_ko_from_ref` 각각 chrF++ 산출 + Claude가 전
   세그먼트 사람 채점.

### 평가 방법

- **1차 선정 기준**: `hyp_ko_from_ref`(정답 전사 기준) chrF++가 가장 높은 모델 우선.
- 동률/근소 차이면 자연스러움·존댓말일치 점수로 타이브레이크.
- 최종 후보는 `hyp_ko`(실제 STT 출력 기준)로도 확인 — 정답 전사에서는 잘하지만 STT
  오류에 유난히 취약한 모델이 있을 수 있음을 대비.
- S1율(세그먼트 100개당 S1 건수)을 핵심 KPI로 병행.

## 결과

### 정량 — 정답 전사 직접 번역 chrF++ (STT 영향 배제, 전체 120세그먼트)

| 모델 | 전체 chrF++ | normal | hard | grammar 호환 |
|---|---|---|---|---|
| Qwen2.5-7B-Instruct (베이스라인) | 24.64 | 25.46 | 22.99 | 정상 |
| **Qwen3-14B** | **31.44** | 31.45 | 31.32 | **비호환**(꺼야 동작) |
| **Gemma-3-12b-it** | 28.69 | 30.04 | 26.05 | 정상 |
| EXAONE-3.5-7.8B-Instruct | 22.37 | 21.69 | 23.89 | 정상(베이스라인보다 낮음) |

순위: **Qwen3-14B > Gemma-3-12b-it > Qwen2.5-7B(베이스라인) > EXAONE-3.5-7.8B**.

### Qwen3-14B — grammar 비호환 상세

`_KOREAN_ONLY_GRAMMAR`(한글만 허용하는 GBNF)를 켠 채 Qwen3-14B에 적용하면 chrF++가
6.5~8.7까지 붕괴한다. 원인: Qwen3의 chat template이 기본적으로 "thinking" 모드라
`<think>` 프리앰블을 내려 하는데, grammar가 `<` 포함 비한글 문자를 전부 막아버려 모델이
사고 과정을 낼 곳이 없어져 **시스템 프롬프트의 한글 예시 단어를 그대로 베껴 쓰는 형태로
새어나온다.** `/no_think`(공식 스위치)로 thinking을 꺼도 grammar를 다시 켜면 동일하게
붕괴 — thinking mode와는 별개로 grammar 자체가 Qwen3 계열과 근본적으로 안 맞는 것으로
보인다.

grammar를 끈 상태에서는 라틴 문자 유출 0건이었으나, 240개 출력 중 10건(4.2%)에서
히라가나/간체자/번체자 등 다른 비한글 문자가 소량 섞여 나왔다(`蠟`, `燭`, `ぶ` 등). 즉
**Qwen3-14B는 순수 번역 품질 1위지만, 실전 채택하려면 이 스크립트 순수성 문제를 grammar
없이 해결하는 방법(더 강한 프롬프트 지침/후처리 필터/Qwen3 호환 grammar 재설계)을 먼저
찾아야 한다** — 지금 상태로는 `EVAL.md` §3.2의 스크립트 순수성 자동 S1 조건에 정기적으로
걸린다.

### Gemma-3-12b-it — 베이스라인(Qwen2.5-7B) 대비 상세 비교

동일 `hyp_ja`(STT 가설) 조건에서 재계산한 chrF++:

| 그룹 | chrF++ (hyp_ja→hyp_ko, 실제 파이프라인) | chrF++ (ja_ref→hyp_ko_from_ref, STT 오류 배제) |
|---|---|---|
| normal (94) | 23.20 | 30.04 |
| hard (26) | 22.30 | 26.05 |
| 전체 (120) | **22.86** | **28.69** |

사람(LLM) 채점 4개 항목:

| 지표 | 베이스라인 (Qwen2.5-7B) | Gemma-3-12b-it | 변화 |
|---|---|---|---|
| 의미충실도 | 2.72 | 3.02 | **+0.30** |
| 자연스러움 | 2.85 | 3.57 | **+0.72** |
| 존댓말일치 | 2.74 | 3.22 | **+0.48** |
| S1율 | 50.8% | 43.3% | **-7.5%p** |
| chrF++ (전체, hyp_ko) | 17.3 | 22.86 | +5.56 |
| chrF++ (전체, ja_ref 직접번역) | 24.25 | 28.69 | +4.44 |
| S1 원인 중 STT 전파 비율 | 69% | 82.7% | +13.7%p |

전 항목에서 개선. 특히 자연스러움(+0.72)과 S1율(-7.5%p) 개선폭이 크다. S1 원인 중 "STT
전파" 비율이 69%→82.7%로 늘어난 것은 **번역 엔진 자체가 스스로 만드는 실패가 줄고, 남은
실패 대부분이 (동일하게 유지된) STT 오류의 전파라는 뜻** — 번역 모델 자체 신뢰도가
높아졌다는 신호로 해석됐다. 라틴 문자 유출은 0건(스크립트순수성 5.00 유지, grammar 제약
정상 작동).

### 반복 오류 패턴 — 베이스라인과 Gemma 대조

| 패턴 | 베이스라인(Qwen2.5-7B) | Gemma-3-12b-it |
|---|---|---|
| A. STT 무출력(저음량 소스 `7112`) | 23건, S1의 38% | 동일 재현(STT 동일이므로 번역 모델 교체로 해결 불가) |
| B. 고유명사 동음이의 오인식 전파 | マッターホルン→"정말 내려갈 거예요?", 千恵子先生→"체코 선생님" | 동일 사례가 다른 방식으로 그대로 재현 — `glossary.json`이 비어있어 두 모델 다 해결 못 함 |
| C. 번역기 자체 오역 | 寿司→"오징어"(반복 재현), いただきます→의미 반전 | 9건(베이스라인 19건에서 감소, 완전히 사라지진 않음) — 流れ星→"나래별"(없는 단어 생성), 米田→"미다"/"요미타"(같은 이름 세그먼트마다 다르게 틀림) |
| D. 조기 종결(사람이 이미 자른 클립이라 이 평가셋에서는 측정 불가) | 실사용 관찰만 | 동일 |

## 결론

- **Gemma-3-12b-it 채택 확정** — 사람 채점 4개 항목 전부·chrF++ 두 조건 모두에서
  베이스라인 대비 일관되게 개선(S1율 50.8%→43.3%). 이 결정이 현재 `backend/config.py`의
  `LLAMA_SERVER_MODEL`로 반영돼 있다.
- **Qwen3-14B는 순수 품질 1위(chrF++ 31.44)지만 미채택** — 이유는 품질이 아니라 grammar
  (`_KOREAN_ONLY_GRAMMAR`)와의 근본적 비호환. grammar 없이 돌리면 스크립트 순수성이
  100%가 아니라는 것(4.2% 비한글 유출)도 추가 걸림돌. **이 비호환만 해결하면 gemma보다
  높은 품질(chrF++ +2.75)로 교체 가능한, 이미 검증된 후보로 남아있다** — 2026-08-22
  현재까지도 미착수.
- EXAONE-3.5-7.8B는 "한국어 네이티브 특화"라는 선정 이유와 반대로 베이스라인보다도
  낮은 성적(22.37) — 미채택.
- 채택 후에도 **"완벽한 번역"(1차 목표)에는 한참 못 미침**(S1율 43.3%, ja_ref 직접번역
  chrF++ 28.69도 "잘 되는 MT" 기준 40~60+에 크게 못 미침) — 모델 교체는 필요조건 중
  하나만 만족시켰을 뿐이고, STT 쪽 개선(오디오 게인 정규화, glossary 채우기)이 여전히
  최우선 과제로 남았다(S1의 82.7%가 STT 전파로 확인됨 — 번역 모델을 바꾼 지금은 오히려
  STT 개선의 레버리지가 더 커졌다는 뜻).
- Qwen3-32B/EXAONE-4.0-32B/Gemma-3-27B-it(원래 계획했던 더 큰 사이즈)는 16GB VRAM
  제약으로 **한 번도 벤치마크된 적 없다** — 2026-08-22 STT를 large-v3-turbo로 교체하며
  GPU 여유가 커졌으므로, 재검토할 만한 미착수 후보로 남아있다.

## 레퍼런스

- `docs/eval/EVAL_REPORT_2026-08-18.md` — 베이스라인(Qwen2.5-7B) 상세 평가, 반복 오류
  패턴 A~D 전체, 개선 계획 A~E.
- `docs/eval/MODEL_BENCHMARK_PLAN.md` — Qwen3-14B/Gemma-3-12b-it/EXAONE-3.5-7.8B 비교
  실행 기록, Qwen3-14B grammar 비호환 상세, 원래 계획(32B급 3종) 및 미실행 사유.
- `docs/eval/EVAL_REPORT_gemma-3-12b-it_2026-08-18.md` — Gemma-3-12b-it 채택 결정 상세
  리포트(§6에 2026-08-19 실사용 로그 기반 추가 발견 포함).
- 원본 산출물: `data/eval_set_2026-08-18.jsonl`(참조셋),
  `data/eval_set_2026-08-18_results.jsonl`/`_graded.jsonl`/`_results_refmt.jsonl`(베이스라인),
  `data/bench_*.jsonl`(모델별 벤치마크 결과).
- `scripts/build_eval_set.py`, `scripts/run_eval.py`, `scripts/eval_stt_propagation.py`.
