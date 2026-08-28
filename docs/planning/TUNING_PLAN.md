# 전사/번역 튜닝 계획 (작성: 2026-08-27)

## 배경 — 왜 튜닝인가

2026-08 STT/번역 모델 서베이(`research/topic/20260826_stt_model_survey_gpu_full/`,
`research/topic/20260826_translation_model_survey/`)의 결론:

- **기존 모델도 나쁘지 않았고, 교체 후보도 통계적으로 비슷했다.** STT는
  `large-v3-turbo` → `Qwen3-ASR-1.7B-hf`로 바꿨지만 품질은 정량·정성 모두 동급
  (교체 이유는 "유창한 환각을 명시적 오류로 바꿔 탐지 쉽게" — fail-loud). 번역은
  `gemma-3-12b-it` 유지(의미 충실도 동급 클리크 3개 중 뉘앙스 1위 + 유창한 환각 0%).
- 즉 **모델 스왑으로는 더 못 짜낸다.** 남은 지렛대는 디코딩 파라미터 → 오디오/
  세그먼테이션 → 후처리 필터 → (마지막) 파인튜닝.

이 문서는 그 지렛대들을 싼 것부터 나열한다. 항목 ID는 STT는 `TS-*`, 번역은 `TT-*`,
공통은 `TC-*`. 우선순위 기준은 `IMPROVEMENT_BACKLOG.md`와 동일(P0/P1/P2).

### 분야 현황 (2026-08 유사 시스템 조사)

whisper_streaming/WhisperLive/RealtimeSTT/obs-localvocal 및 IWSLT 동시통역
연구를 훑은 결과, **우리가 겪는 문제(환각, 세그먼테이션 오절단, 확신에 찬 partial
오역, 고유명사·전문어, 유창한 환각, 음악 구간)는 이 분야에서 아직 "해결"되지
않았다.** "모델 스왑은 정체" 결론이 분야 전반과 일치한다. 실제 이득은 raw 모델이
아니라 네 갈래에서 나온다 — 이 문서 항목에 대응시키면:

| 분야에서 검증된 접근 | 우리 항목 |
|---|---|
| **LocalAgreement**(연속 업데이트에서 안정 접두사만 commit) — 오픈소스 Whisper 스트리밍의 partial 안정화 정석 | 신규 `TC-2c` |
| **Contextual biasing / shallow fusion**(bias list로 디코딩 편향) — Whisper `initial_prompt` 해킹의 정석 버전 | `TS-1a` 확장, 신규 `TS-1e` |
| **Reference-free QE 게이팅**(CometKiwi 등으로 의심 번역 플래그 → 재번역/폴백) — 우리 임베딩 유사도 게이트의 상위 호환 | 신규 `TT-3d` |
| **Re-translation UX**(불안정 텍스트를 띄우고 나중에 수정) — Google/MS/Zoom 실시간 자막 방식 | 이미 우리 설계(final이 partial을 in-place 교체). 추가 작업 없음, 방향이 맞다는 확인 |

## 전제 — 측정 루프 없이는 추측이다 (P0)

| ID | 항목 | 우선순위 |
|---|---|---|
| TC-0a | **레퍼런스 정리** — 평가셋 `data/eval_set_2026-08-18.jsonl` / `data/` 150쌍의 `ja_ref` 중 오전사분(특히 패션·뷰티 네일 전문어 `プレップ`·`ベースジェル`·`地詰め`, `[100]`~`[102]`)을 사람이 고친다. 지금 상태로는 번역 측정 상한이 깎여 있다. | P0 |
| TC-0b | **튜닝 측정 하네스** — 변경 1건마다 chrF++/CER(`score_*.py`) + 정성 루브릭(`qualitative_eval.py`, 의미 충실도/유창성/뉘앙스 or 자연스러움/충실도) + 라이브 캡처(`data/sessions/`) 3종을 자동으로 돌려 델타를 낸다. 없으면 나머지 항목 전부 근거 없이 만짐. | P0 |

---

## STT 튜닝

### TS-1. 디코딩/추론 파라미터 (학습 없음) — P1

현재 엔진 `Qwen3ASREngine`(transformers 생성). faster-whisper는 폴백.

| ID | 레버 | 대응하는 관찰된 실패 |
|---|---|---|
| TS-1a | **context/hotword 프롬프트** — Qwen3-ASR는 자유 텍스트 컨텍스트를 받는다. 세션 글로서리(게임 아이템명, 스트리머/시청자 이름, 도메인 어휘)를 주입. | 고유명사·전문어 오인식 (`ロウダイ`, `日生さん`, `御茶ノ水`) |
| TS-1b | `repetition_penalty` / `no_repeat_ngram_size` | granite류 "あああ" 루프, Whisper 반복 환각 |
| TS-1c | `num_beams` / `temperature` (조건부: 게임 카테고리만 beam↑) | beam=1 불안정 |
| TS-1d | faster-whisper 폴백 시 `condition_on_previous_text` on/off, `initial_prompt`/`hotwords` | 문맥↑ vs 환각↑ 트레이드오프 |
| TS-1e | **Contextual biasing (정석 버전)** — TS-1a의 프롬프트 주입은 Whisper 계열에선 짧은 오디오에서 환각을 늘리는 해킹. 분야 표준은 디코딩 시 bias list로 로짓 편향(shallow fusion) 또는 n-best 재순위. Qwen3-ASR/faster-whisper가 이걸 노출하지 않으므로 현실적으로는 (a) n-best/다회 전사 불일치를 저신뢰 신호로 쓰고 (b) 그 구간만 글로서리로 교정 — 아래 `TS-2e`와 합류 | 고유명사·전문어를 프롬프트 환각 부작용 없이 |

### TS-2. 후처리 필터 (학습 없음) — P1

| ID | 레버 | 비고 |
|---|---|---|
| TS-2a | **`HallucinationGate`(임베딩 유사도) 임계값 스윕** | Qwen3-ASR는 per-segment confidence가 없어 `WHISPER_*_THRESHOLD` logprob 필터가 죽어 있음 — 이 게이트가 유일한 방어선 |
| TS-2b | **스톡 프레이즈 블록리스트** — 긴 오디오에 대해 `ご視聴ありがとうございました` / `thank you` / `うん。` 단독 출력이면 하드 드롭 | 정성 게임 카테고리 최악의 원인 중 하나 |
| TS-2c | **글로서리 퍼지 교정** — 출력을 `backend/glossary.py` 항목과 근사 매칭해 `米だわね`→`米田` 류 근접 오류 수정 | glossary는 이미 NFKC 매칭 보유, 퍼지 레이어만 추가 |
| TS-2d | N-best / 2회 전사 후 불일치 = 저신뢰 플래그 | 비용↑, P2 |
| TS-2e | **신뢰도 유도 LLM 부분 교정** (사용자 제안 2026-08-27) — 종결된 문장을 어절/어구로 쪼개고, 저신뢰 구간만 LLM이 맥락+글로서리로 검토·수정한 뒤 문장을 자연스럽게 재조립. 고신뢰 구간은 동결(freeze). 선행연구: LLM 기반 ASR 생성 오류교정(HyPoradise/GER, Whispering-LLaMA), contextual spelling correction(고유명사 특화). **알려진 결과**: 약한 ASR엔 WER 10~40% 상대 개선, 강한 ASR엔 미미하거나 악화(LLM이 맞는 희귀어를 흔한 그럴듯한 말로 바꿈 = 유창한 환각의 교정 단계 재현). **동결 + 구간 한정**이 그 악화를 줄이는 핵심이라 제안 방향은 맞음. **우리 제약**: ① 프로덕션 Qwen3-ASR는 어절 확률을 안 줌 → `TS-2d` 불일치나 LocalAgreement 불안정도를 신뢰 프록시로 대체 필요 ② 일본어 어절 분리에 형태소 분석기(fugashi) 의존 ③ 재조립 문장에 `TT-3d` QE 게이트 필수(과교정 방어) ④ final 경로에 LLM 왕복 1회 추가 → 지연. `TT-1`(번역 프롬프트) 정체 시 착수 | P2, `TS-1e`/`TS-2d`/`TT-3d`와 묶어서 |

### TS-3. 모델 학습 — P2 (마지막)

| ID | 레버 | 선행 조건 |
|---|---|---|
| TS-3a | Qwen3-ASR / Whisper LoRA 파인튜닝 (도메인: 일본어 유튜브 라이브, 게임 실황) | `data/sessions/` 캡처 + `data/flagged_segments.jsonl` + 사람 교정으로 최소 수 시간치 라벨 구축. 현재 150쌍으로는 부족 |

---

## 번역 튜닝

### TT-1. 프롬프트 엔지니어링 (학습 없음) — P1, 가장 큰 지렛대

| ID | 레버 | 대응하는 관찰된 실패 |
|---|---|---|
| TT-1a | **시스템 프롬프트** — 화자 말투(반말 캐주얼 → 한국어도 캐주얼, 존댓말 금지), "번역만·설명 금지", "한국어만 출력", "고유명사 유지", disfluent/미완성 입력 처리 규칙 | EXAONE-3.5 어조 변경·회피, EXAONE-4.0 해설문 대체 |
| TT-1b | **few-shot 예시** — 실패 유형별 큐레이션 JA→KO 3~5쌍(disfluency 정리, 말장난, 캐주얼 어조) | 어조 드리프트, 회피 |
| TT-1c | **이전 문장 컨텍스트** — `_final_history` 최근 N개 final을 프롬프트에 주입 | 대명사·화제 연속성, 단편에 대한 "확신에 찬 오역" |
| TT-1d | **글로서리 주입** — `glossary.py`의 JA→KO 매핑을 프롬프트에 직접 | 뉘앙스(`推し`→`최애`), 고유명사 |

### TT-2. 디코딩 파라미터 — P1

| ID | 레버 | 비고 |
|---|---|---|
| TT-2a | `temperature`↓, `top_p`, `repeat_penalty`, `min_p` | 더 literal/안정 |
| TT-2b | `config.LLAMA_FINAL_MAX_TOKENS` | 낮으면 긴 문장 잘림 |
| TT-2c | **GBNF 문법 조이기** — 한자/라틴 명시적 차단 | Qwen3 계열 한자 유출 대비(향후 교체 시). **선행: 서베이 빌드의 grammar-hang 버그 → 프로덕션 빌드가 `verify_contract` 통과하는지 확인**(`IMPROVEMENT_SPECS.md` T 항목 전제) |

### TT-3. 후처리 필터 (학습 없음) — P1

| ID | 레버 | 비고 |
|---|---|---|
| TT-3a | **스크립트 순수성 게이트** — 한자/가나/라틴 포함 출력 리젝트 후 재시도 | 한자 유출 + JA echo + 영문 메타 누출 동시 차단 |
| TT-3b | **JA-echo 감지기** — 출력이 입력과 문자 겹침 높으면 플래그/재시도 | Gemma echo 실패(120개 중 7개) 직접 대응 |
| TT-3c | 길이 sanity 체크 (KO 출력이 예상 대비 과도하게 짧/긺 → 재시도) | |
| TT-3d | **Reference-free QE 게이트** — CometKiwi 등 무참조 품질추정으로 (JA원문, KO번역) 쌍의 신뢰도를 점수화, 임계 미만이면 재번역/폴백. `TS-2a`의 임베딩 유사도 게이트를 일반화한 것. Unbabel 등이 대규모 배포한 "유창한 오역" 탐지 방식. `TS-2e` 재조립 결과 검증에도 재사용 | 유창한 환각 탐지. 모델 상시 로드 비용↑ → P2 |

### TT-4. 모델 학습 — P2 (마지막)

| ID | 레버 | 비고 |
|---|---|---|
| TT-4a | Gemma LoRA/QLoRA 파인튜닝 (도메인 JA→KO: 캐주얼 스트리머체, disfluency, 글로서리 준수) | 12B QLoRA는 현 GPU에서 가능. 평가셋 120쌍 확장 필요 |
| TT-4b | 2단계 파이프라인(NMT 초벌 → LLM 다듬기) | `report/02`의 다음 후보였으나 `report/04`에서 NLLB 단독이 나빠 위험. TT-1 정체 시에만 |

---

## TC — 전사·번역 공통

### TC-1. 오디오 프런트엔드 — P1

| ID | 레버 | 비고 |
|---|---|---|
| TC-1a | **VAD 튜닝** — silero `threshold`, `min_silence_duration_ms`, `speech_pad_ms`, `min_speech_duration_ms` | 침묵이 문장 종료 1차 트리거라 전사 품질 + 문장 경계 품질을 동시에 좌우. 공격적 → 문장 잘림, 느슨 → run-on |
| TC-1b | 버퍼/청크 — `PARTIAL_UPDATE_INTERVAL_S`, `MAX_UTTERANCE_SECONDS`, `sentence_completion.has_strong_sentence_boundary` 종결부호 스캔 범위 | |
| TC-1c | **노이즈/음악 억제** — STT 앞단 DeepFilterNet/RNNoise, 조건부 Demucs 보컬 분리 | 게임 BGM·음악 블리딩 = 게임 카테고리 정성 최악(의미 충실도 ~1.8)의 주원인. `IMPROVEMENT_BACKLOG.md` M1과 연계 |
| TC-1d | 라우드니스 정규화, 리샘플 품질 | |

### TC-2. 세그먼테이션 = 번역 품질 상한 — P1

| ID | 레버 | 비고 |
|---|---|---|
| TC-2a | 침묵 임계값 + `has_strong_sentence_boundary` 튜닝 | 잘못 끊긴 단편은 번역이 근본적으로 안 됨 — TC-1a와 같은 knob이지만 번역 지렛대로도 관리 |
| TC-2b | 경계 확장 시 문장 전체 재번역 | 현 "final이 partial 대체" 설계 그대로. 재번역 트리거 조건만 점검 |
| TC-2c | **LocalAgreement commit 정책** — partial STT를 매 갱신 통째로 신뢰하지 말고, **연속 2회 갱신에서 일치하는 접두사만 확정(commit)**하고 나머지는 미확정으로 유지. whisper_streaming의 핵심 기법. 효과: ① 불안정한 환각 어절이 애초에 확정 안 됨 ② `has_strong_sentence_boundary`가 확정 접두사에만 걸리므로 오절단↓ ③ deprecated된 live partial 번역을 "확정 접두사"에만 돌리면 다시 켤 여지 | partial 안정화 + 선제분할 신뢰도. `PARTIAL_UPDATE_INTERVAL_S` 주기와 상호작용 |

---

## 착수 순서 (싼 것부터)

0. **TC-0a·TC-0b** — 레퍼런스 정리 + 측정 하네스. 나머지 전부의 전제.
1. **TT-1** (프롬프트 + few-shot + 글로서리 + 이전 문장 컨텍스트) — 가장 싸고 효과 클 것
2. **TS-1 + TS-2** (Qwen3-ASR context 프롬프트 + repetition_penalty + 스톡 프레이즈 블록리스트 + HallucinationGate 임계값)
3. **TC-1a·TC-2** (VAD/세그먼테이션) — 전사·번역 공통. **`TC-2c` LocalAgreement
   commit 정책**을 여기서 우선 시도(분야에서 검증된 partial 안정화, 구현 부담 중간)
4. **TC-1c** (노이즈/음악 억제 프런트엔드, M1 연계)
5. **TT-2 + TT-3** (디코딩 파라미터 + 스크립트 순수성/JA-echo 필터 + `TT-3d` QE 게이트)
6. **`TS-1e`+`TS-2d`+`TS-2e`+`TT-3d` 묶음** — 신뢰도 프록시(n-best/LocalAgreement
   불안정도) → 저신뢰 구간 LLM 부분교정 → QE 검증. P2, 위 단계들이 정체할 때만.
7. 그다음에야 **TT-4a / TS-3a** (LoRA 파인튜닝) — 번역부터(데이터 확보가 더 현실적)

## 레퍼런스

- STT 서베이 + 정성: `research/topic/20260826_stt_model_survey_gpu_full/report/`
  (`01-full-results.md`, `02-qualitative-eval.md`)
- 번역 서베이 + 정성: `research/topic/20260826_translation_model_survey/report/`
  (`02-results.md`, `03-ci-overlap-and-qualitative-check.md`, `04-qualitative-eval-full.md`)
- 기존 개선 항목: `docs/planning/IMPROVEMENT_BACKLOG.md` / `IMPROVEMENT_SPECS.md`
  (R/Q/S/T/D 항목, M1 음악 구간). 이 문서는 그 위에 "모델은 고정, 파라미터/전후처리
  튜닝" 레이어를 추가한 것.
- 평가 방법론: `docs/eval/EVAL.md`
- 유사 시스템 갭 실험: `research/topic/20260827_vad_stt_survey/` (세그먼테이션 스택 vs 통청취 STT 갭 측정)

### 외부 참고 (2026-08 조사, `TS-1e`/`TS-2e`/`TC-2c`/`TT-3d` 근거)

- **whisper_streaming** (Macháček et al., IWSLT 2023) — LocalAgreement/LocalAgreement-2
  commit 정책. `TC-2c` 원출처.
- **WhisperLive**(Collabora), **RealtimeSTT**(KoljaB), **obs-localvocal**(Locaal AI) —
  로컬 VAD+Whisper+partial/final(+번역) 오픈소스. 우리와 거의 동일 구조, 같은 문제
  겪음.
- **HyPoradise** (Chen et al., NeurIPS 2023), **Whispering-LLaMA** — LLM 기반 ASR
  n-best 생성 오류교정. `TS-2e` 선행. 강한 ASR엔 과교정 위험.
- **Contextual Spelling Correction / CLAS** (Google) — 고유명사 특화 ASR 교정 +
  contextual biasing. `TS-1e` 선행. NE 슬라이스에선 실제로 잘 작동.
- **CometKiwi / reference-free QE**, NMT 환각 연구(Guerreiro et al. 2023) — `TT-3d` 근거.
- **IWSLT Simultaneous Speech Translation**, **SimulEval**, **SeamlessStreaming(EMMA)** —
  동시통역 지연/품질 트레이드오프. wait-k / AlignAtt 등 세그먼테이션 정책.
