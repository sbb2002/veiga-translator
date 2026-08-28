# 연구설계 — VAD-STT 파이프라인 vs 통청취 STT 갭 측정 (2026-08-27)

인터뷰(2026-08-27, Claude ↔ 사용자)로 확정한 설계. 실제 실행은 GPU 환경에서 별도로
진행하며, 스크립트 작성은 이 문서 승인 후 별도 요청 시 착수한다.

---

## 1. 목적과 위치

### 1.1 배경
- 20260826 STT 서베이(`research/topic/20260826_stt_model_survey_gpu_full/`)와 번역 서베이는
  **오디오 클립 전구간을 한 번에 청취한 뒤 전사**하는 방식으로 바닐라 모델 성능을 쟀다.
- 실제 앱은 그 사이에 스택이 있다: 0.3초 청크화 → 512-sample 프레임 / SileroVAD →
  침묵 기반 발화 종단 판정(+ 문맥 보정, 선제 분할, hard cap) → partial 재전사 트랙 +
  finalize 큐 → RMS 게이트 → HallucinationGate → beam=5 재전사.
- 이 스택의 **휴리스틱 전처리 레이어들(발화 종단 시점 판정 등)은 실사용 감각으로 넣은
  것이고, 연구로 효과가 입증된 적이 없다.**

### 1.2 이번 실험이 하는 것
그 스택을 통째로 통과시킨 전사결과 **A**를, 통청취 전사 **B**, 라벨 **C**와 대조해
**집계 갭(aggregate gap)**을 측정한다. 1차 목표(단일화자 완벽 번역)의 전제인 "전사 품질"이
실제 파이프라인에서 얼마나, 어느 카테고리에서, 어떤 실패 양상으로 깎이는지 수치화한다.

- 1차 목표: VAD-STT(A) 품질이 통청취 STT(B) 품질에 **통계적으로 도달**하는지 판정.

### 1.3 범위 밖 (후속, 이번 결과를 보고 착수 결정)
갭이 유의하면 전처리 레이어를 **하나씩 분리해 조절하는 ablation**을 후속 토픽으로 진행한다.
이번은 스택 전체의 집계 효과만 본다. 후속 착수 여부·순서는 이 실험 결과를 보고 논의한다.
관여 레이어 목록은 §9 부록에 후속 체크리스트로 정리.

---

## 2. 측정 대상 파이프라인

```
오디오 클립(16kHz mono)
  → 0.3s PCM16 청크
  → AudioSession.feed_audio
  → 512-sample(32ms) 프레임 / SileroVAD (VAD_SPEECH_THRESHOLD=0.5)
  → [partial 트랙] MIN_PARTIAL_AUDIO_SECONDS 이후 PARTIAL_UPDATE_INTERVAL_S마다
       RMS 게이트 → STT fast=True(greedy) → "partial" 이벤트 (일본어만, 번역 없음)
  → [finalize 판정] 매 프레임:
       past_hard_cap (duration >= MAX_UTTERANCE_SECONDS)
       or past_grace_deadline (silence_ms >= 유효침묵 + 유효grace)
       or (past_silence_threshold and looks_complete(last_partial_text))
       or (silence_ms == 0 and has_strong_sentence_boundary(partial_text))  # 선제 분할
  → finalize 큐 (FIFO, 발화 순서 보장)
  → RMS 게이트 (AUDIO_RMS_SILENCE_FLOOR)
  → HallucinationGate (임베딩 유사도, SIM_THRESHOLD=0.78)
  → (turbo만) no_speech_prob / avg_logprob 필터 — Qwen3-ASR엔 inert
  → STT fast=False, beam=5 재전사
  → "final" 이벤트 수집  ← 이게 A의 원재료
```

- **STT-only.** 번역 / 글로서리 / broadcaster hint / context 요약은 이번 범위 밖. llama-server
  불필요. `_do_finalize`의 번역 호출 지점은 하네스에서 호출되지 않도록 처리(또는 no-op
  TranslationEngine 주입).
- **프로덕션 `backend/config.py` 기본값 그대로.** 특히 `ADAPTIVE_VAD_ENABLED=True`
  (실제 앱이 돌리는 그 거동을 잰다). 설정은 1개로 고정 — 범위가 갭 측정이므로.
- SileroVAD는 stateful RNN, 클립마다 `vad.reset()` (프로덕션의 세션당 reset과 동일).
  긴 라이브 세션이 축적하는 클립 간 적응은 없음 — B도 마찬가지라 비교엔 공정.

---

## 3. 데이터

- `data/wav/` 150쌍, 5카테고리 × 30 (`20260826_stt_model_survey_gpu_full/src/common.py`의
  `CATEGORIES = ["게임", "여행", "음식,요리", "일상,소통", "패션,뷰티"]`).
- `common.Segment` 로더 재사용. 단, 서베이 transcripts에 박힌 절대 `wav_path`
  (`C:\...\pyworks\live-translator\data\wav\...`)는 무시하고 카테고리 / seg_id로 재구성.
- **라벨 C** = `ja_ref`. `ko_ref`는 정성 채점 시 의미 확인용으로만 사용, 점수 대상 아님.

---

## 4. 엔진 (3개 다 파이프라인 통과)

2026-08-28 결정 — `20260826_.../report/03-fairness-review.md` §2.5의 ReazonSpeech
공정 재평가에서 정성(의미 충실도)이 turbo보다 유의미하게 높고 Qwen과 동급으로 나와
**교체 후보로 승격**. 프로덕션은 Qwen3-ASR지만 3개를 모두 파이프라인에 태워 비교한다.

| 엔진 (토큰) | 클래스 | conda env | 비고 |
|---|---|---|---|
| `qwen3-asr-1.7b` | `backend/stt/qwen3_asr_engine.py` | live-translator | 현 프로덕션. per-segment confidence 없음 → logprob 필터 inert, HallucinationGate만 |
| `turbo` | `backend/stt/faster_whisper_engine.py` (int8_float16) | live-translator | no_speech_prob / avg_logprob 필터 **활성** |
| `reazonspeech` | `src/reazonspeech_engine.py` (공식 `reazonspeech.nemo.asr` 래퍼) | **reazonspeech** (nemo_toolkit + reazonspeech) | confidence 없음(Qwen과 동일). 엔진별로 env가 달라 **1엔진/1실행**, 매칭 env에서 |

→ 각각 150클립 통과 = **A_qwen / A_turbo / A_reazonspeech, 총 450건.**
3개 엔진의 파이프라인 내 환각 필터가 서로 다르다(비대칭)는 점은 결과 해석에 명시.

**B_reazonspeech**: 통청취 비교군은 `20260826_.../out/reazonspeech-nemo-v2_fair_gpu/`
= `transcribe_reazonspeech_fair.py --device cuda --out-suffix _gpu`로 GPU에서 새로 뽑은
전량(품질은 CPU `_fair`와 동일, RTF만 GPU라 비교 가능). 커밋된 CPU `_fair`(report/03
§2.5 근거)는 그대로 둔다.

---

## 5. 오프라인 하네스 (`src/run_pipeline.py`)

### 5.1 구조
- **`backend.audio_session.AudioSession`을 직접 import한다.** 이 토픽의 **의도적 관례 예외** —
  STT 서베이 3종이 지킨 "no `backend.*` imports"를 여기선 깨야 실제 파이프라인 코드를
  테스트한다(재구현하면 사본을 테스트하는 꼴). 리포 루트에서 실행
  (`python -m research.topic.20260827_vad_stt_survey.src.run_pipeline` 또는 `PYTHONPATH=.`)
  하여 `backend.*` 절대 import 해결.
- 엔진 + `SileroVAD`(1회 로드) + 이벤트 수집 싱크(`on_event`)를 `AudioSession`에 주입.
- 클립을 0.3초 청크로 잘라 순차 `await session.feed_audio(chunk)`, 마지막에
  `await session.close()`로 finalize 큐 드레인.

### 5.2 가상 클럭
finalize 로직의 벽시계 의존(`duration_s()`, partial 스로틀, hard cap, 적응형 rate/pause
EMA)을 오디오 시간으로 대체한다. `silence_ms`는 이미 `frame_ms` 누적이라 클럭 무관.

- `unittest.mock.patch.object(backend.audio_session, "time", shim)` — `shim`은 `monotonic()`만
  제공(모듈이 `time.time()`은 안 씀). STT 엔진은 `asyncio.to_thread` 안에서 자기 `time`을
  쓰므로 영향 없음.
- **전진 방식**: 각 `feed_audio` 호출 전에 청크 오디오 길이(0.3초)만큼 가상시간 전진
  (청크 단위 양자화). `PARTIAL_UPDATE_INTERVAL_S=0.6` / `MIN_PARTIAL_AUDIO_SECONDS=0.8` /
  `MAX_UTTERANCE_SECONDS=10`은 0.3초 양자화로 최대 1청크 오차 — 허용.

### 5.3 가상 클럭 검증 (필수 선행 단계)
10클립을 (a) 클립 길이만큼 실제 `asyncio.sleep`하며 실시간 주입, (b) 가상 클럭으로
각각 돌려 A가 동일한지 확인. **합격 기준: 정규화 후 CER ≤ 0.01.** 불일치 시 프레임 단위
(32ms) 전진으로 강화(→ `AudioSession._process_frame` 래핑) 후 재확인.
결과와 차이를 `out/clock_validation.json` + 보고서에 기재.

### 5.4 산출물 (`out/<engine>/pipeline_transcripts.jsonl`)
클립당 1행: `seg_id`, `category`, `duration_s`, `n_finals`, `A`(공백 조인, 빈 문자열 허용),
`stt_elapsed_s_total`(partial + final STT 벽시계 합), `n_partial_calls`,
`finalize_reason_counts`(hard_cap / grace_expired / silence_complete / strong_boundary),
`gate_drops`(RMS / hallucination).

---

## 6. A 조립 규칙

- 한 클립이 낸 **모든 `final` 이벤트의 `text`를 발화 순서대로 공백 조인**.
- `final` 0개(RMS / HallucinationGate 드롭, 또는 무음) → **빈 문자열** (정규화 후 CER = 1.0).
- `partial`은 A에 미포함 (최종 산출물 아님).

---

## 7. B 조달

- 20260826 STT 서베이의 `out/turbo/transcripts.jsonl`, `out/qwen3-asr-1.7b/transcripts.jsonl`의
  `hyp`를 **그대로** B로 사용. RTF(B)도 서베이 값 재사용.
- **교란요인 명시(보고서 필수 항목)**: B는 전구간 1회 전사(디코딩 설정은 서베이 스크립트
  기준). A의 final은 `fast=False` beam=5. 두 경로의 디코딩 파라미터가 완전히 동일하진 않을
  수 있어 A-B 차에 세그먼테이션 외 요인이 섞일 수 있다 → "측정하려는 갭"의 일부로 간주하되
  별도로 언급하고, 여지가 크면 후속에서 B 매칭 재실행을 검토.

---

## 8. 평가

### 8.1 정량 (모든 지표 오차/CI 병기)

| 지표 | 산출 |
|---|---|
| CER, chrF++(word_order=2), BLEU-char, ROUGE-L F1 | `20260826_stt_model_survey_gpu_full/src/score_quantitative.py` 재사용. 동일 `normalize_ja` (NFKC + `、。！？「」『』・…` 및 공백 제거) |
| 의미 임베딩 코사인 | `multilingual-e5-large`, A vs C를 일본어-일본어로 |
| RTF | **RTF(A)** = Σ(파이프라인 STT 벽시계, partial + final) / Σ(오디오 길이). **RTF(B)** = 서베이 값. 구조상 A > B 예상 — 그 배수 자체가 결과의 한 축 |

- 정량 지표는 **구두점 무시**(B와 동일 정규화). 구두점 준수는 §8.2 정성에서만 평가.
- 비교: **A vs B**, **A vs C** 각각. 카테고리별 + 전체.

### 8.2 정성 (Claude 직접 채점, 전수)

- 대상: **A 300건 + B 300건** (동일 축으로 채점해야 A-B Wilcoxon 가능 — 기존 서베이
  정성은 50표본·다른 축[nat/fid]이라 재사용 불가).
- 대조: A(또는 B)와 C(라벨)를 나란히, `ko_ref`로 의미 확인.
- 축, 각 **1(매우 그렇지 않다) ~ 5(매우 그렇다)**:
  - **자연스러움·가독성** — 예: "2026년 8월" > "이천이십육년 팔월" (같은 의미라도 표기
    가독성). 숫자·단위·고유명사 표기, 읽기 흐름.
  - **문장부호 엄수성** — `?`, `!`, `.` 등을 문맥에 맞게 썼는가.
- **환각** — 세그먼트당 **0/1 플래그**(원문에 없는 내용을 그럴듯하게 생성). 엔진별 ·
  카테고리별 **환각률**로 별도 집계. (환각은 연속척도보다 빈도가 맞음 — STT 서베이의
  "유창한 환각률"과 같은 방식.)
- 집계: 엔진별 평균 + 카테고리별. A vs B는 1~5 축에 Wilcoxon, 환각률 차는 paired bootstrap.

### 8.3 통계 처리 (README 유의사항 준수)

| 항목 | 방법 |
|---|---|
| 정량 (CER 등) | 세그먼트 **paired bootstrap 2000회** → 95% CI + bootstrap p (코퍼스 지표를 재표본마다 재계산) |
| 정성 (1~5 순서형) | **Wilcoxon signed-rank test** (페어드) |
| 환각률 차 | paired bootstrap |
| 유의수준 | α = 0.05 |
| 다중비교 | 비교 패밀리(A-B, A-C × 지표 × 엔진)에 **Holm 보정** |
| **"B에 도달" 판정** | A-B 페어드 차 CI가 0 포함 **&** Holm 보정 p > 0.05 |

- 모든 결과 표에 CI(또는 오차) 병기. 두 후보 비교 시 사용한 검정과 p-value 명기.

### 8.4 산출·보고

- 결과는 **표 + 표에 대한 간략한 설명**.
- 표를 **적절한 그래프로 그려 `fig/*.png`** 저장 (막대 + CI 에러바 등, 서베이 관례).
- `report/*.md`는 결과 표·그림을 **반드시 활용**.

---

## 9. 부록 — 후속 ablation 대상 레이어 체크리스트

이번 실험은 아래 스택의 **집계** 효과만 잰다. 갭이 유의하면 하나씩 분리해 조절.

| 레이어 | 파라미터 (`config.py` 현재값) | 코드 |
|---|---|---|
| VAD speech 판정 | `VAD_SPEECH_THRESHOLD=0.5`, `VAD_FRAME_SAMPLES=512` | `backend/vad.py` |
| 침묵 기반 종단 | `VAD_SILENCE_MS=600` | `audio_session._process_frame` |
| 종단 문맥 보정 | `looks_complete` + `FINALIZE_GRACE_MS=200` | `backend/sentence_completion.py` |
| 선제 분할 | `has_strong_sentence_boundary` (버퍼 전체 `。！？` 스캔) | `backend/sentence_completion.py` |
| Hard cap | `MAX_UTTERANCE_SECONDS=10` | `audio_session._process_frame` |
| 적응형 VAD | `ADAPTIVE_VAD_ENABLED=True`, `ADAPTIVE_VAD_EMA_ALPHA=0.2`, `ADAPTIVE_VAD_MIN_SAMPLES=5`, `ADAPTIVE_SILENCE_TARGET_RATIO=0.7` (clamp 350–1200ms), `ADAPTIVE_PAUSE_OUTLIER_MS=8000`, `ADAPTIVE_RATE_BASELINE_CPS=7.0` (grace clamp 100–500ms) | `audio_session._effective_silence_ms` / `_effective_grace_ms` |
| partial 케이던스 | `PARTIAL_UPDATE_INTERVAL_S=0.6`, `MIN_PARTIAL_AUDIO_SECONDS=0.8` | `audio_session._process_frame` |
| RMS 게이트 | `AUDIO_RMS_SILENCE_FLOOR=0.006` | `audio_session._emit_partial` / `_do_finalize` |
| HallucinationGate | `HALLUCINATION_GATE_SIM_THRESHOLD=0.78`, model `paraphrase-multilingual-MiniLM-L12-v2` | `backend/hallucination_gate.py` |
| (turbo만) logprob 필터 | `WHISPER_NO_SPEECH_THRESHOLD=0.6`, `WHISPER_NO_SPEECH_HARD_THRESHOLD=0.6`, `WHISPER_AVG_LOGPROB_THRESHOLD=-1.0` | `backend/stt/faster_whisper_engine.py` (Qwen3-ASR엔 inert) |

---

## 10. 한계 (보고서 서두에 명시)

1. **평가셋이 이미 문장 단위 짧은 클립**이라 run-on / 선제 분할 / hard cap 경로를 거의
   안 건드린다(연속 다문장 발화에서 발동). 즉 이번 갭은 "VAD 앞뒤 트림 + RMS 게이트 +
   환각 게이트 + partial/final beam 차 + 세그먼테이션"의 합이지 "문장 중간 오절단"이 아니다.
   후자는 `data/sessions/` 연속 캡처라야 나오나 ground truth 없음.
2. **가상 클럭은 프로덕션이 GPU 부하로 실시간을 못 따라가는 상황을 재현 못 한다.** 현재는
   따라간다고 가정(2026-08-26 라이브 체크: 체감 지연 없음). 실시간 거동 검증은
   ground-truth 없는 연속 캡처의 몫.
3. **A-B 디코딩 파라미터 교란요인** (§7).
4. **turbo vs Qwen3-ASR 비대칭**: logprob 환각 필터가 turbo에만 활성.
5. **Claude 단독 정성 채점** (기존 서베이와 동일 한계). 1~5 축은 순서형이라 t-검정 대신
   Wilcoxon.

---

## 11. 산출물 레이아웃 (계획)

```
20260827_vad_stt_survey/
  README.md              # 짧은 요약 + 이 DESIGN.md 가리킴
  DESIGN.md              # 이 문서
  src/
    run_pipeline.py       # 오프라인 하네스 (backend.audio_session import)
    virtual_clock.py      # shim + 검증 헬퍼
    score_quantitative.py # 20260826 스크립트 재사용/래핑
    score_embedding.py    # multilingual-e5-large 코사인
    analyze_stats.py      # paired bootstrap CI + Wilcoxon + Holm + fig/*.png
    qualitative_eval.py   # 600건 점수 로드/집계/자체검증
  out/
    qwen3-asr-1.7b/pipeline_transcripts.jsonl
    turbo/pipeline_transcripts.jsonl
    quant_scores.json, embedding_scores.json
    qualitative_sample.txt, qualitative_scores.json
    clock_validation.json
  fig/*.png
  report/
    01-gap-results.md      # 정량 갭 (A-B, A-C), RTF, 카테고리별
    02-qualitative-eval.md # 정성 3축 + 환각률, A vs B
```

실행 환경: GPU 박스, 추후. 스크립트 작성은 이 문서 승인 후 별도 요청 시.
