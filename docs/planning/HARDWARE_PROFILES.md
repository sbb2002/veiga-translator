# 하드웨어 프로파일 계획 (프로덕션화 — 컴퓨팅 리소스 축소)

작성: 2026-10-11 / 상태: **계획 단계 (구현 없음)**. 수치는 웹 조사 + 대역폭 기반 추정이며,
"측정 필요" 표시가 없는 한 근거 링크가 있는 값만 사실로 취급한다. 추정치는 §7의 계측으로 대체한다.

## 1. 원칙

1. **품질은 고정, 지연은 실측 후 결정.** 모든 프로파일은 현재 구성(gemma-3-12b + Qwen3-ASR)의 평가 점수 이상이어야 한다.
   모델 교체는 허용(2026-10-11 사용자 승인)이며, 하한을 못 넘는 조합은 채택하지 않는다.
2. **번역은 2초 안에 안 들어와도 끝까지 수행한다.** 문장을 건너뛰거나 번역을 포기하는 열화 규칙을 두지 않는다.
   일본어 partial은 즉시 표시하고 한국어 final은 완료되는 대로 도착한다(partial 번역은 deprecated라 현재 설계와 호환).
   **실측 지연으로 프로파일별 지원 여부/표기를 나중에 결정**한다 — 오늘은 결정하지 않는다.
3. 지연은 단순 지연이 아니라 **누적**을 함께 본다. final 번역 속도가 화자의 말 속도(문장당 약 40~60 토큰 / 4~6초 가정, 측정 필요)보다
   느리면 대기열이 계속 늘어난다. 계측 항목에 "문장 종료→한국어 final 도착" 지연의 시간 경과 추이(드리프트)를 포함한다.
   (참고: 현재 코드는 `LLAMA_SERVER_TIMEOUT_S=15`, `STT_FINAL_TIMEOUT_S=15` 등 타임아웃 폴백이 있어,
   "끝까지 번역" 측정 시에는 이 폴백을 끄거나 늘려야 실제 지연이 측정된다 — 구현 단계 항목.)

## 2. 조사로 확인된 사실

| 항목 | 내용 | 출처 |
|---|---|---|
| gemma-3-12b Q4_K_M | GGUF 약 7.2~7.5GB. 총 VRAM은 컨텍스트에 따라 약 9GB(보수적 12GB). KV 캐시 q8_0으로 절반 | [1] |
| TranslateGemma | 4B/12B/27B, 일본어 포함 55개 언어. 벤더 발표상 12B가 Gemma 3 27B 기준선을 WMT24++에서 상회. **일·한 단독 수치는 못 찾음** (기술보고서 arXiv 2601.09012 확인 필요). GGUF(Q4_K_M) 커뮤니티 빌드 존재 | [2][3] |
| gemma-3 4B vs 12B | 일·한 직접 비교 자료 없음. 일반 FLORES+ 경향상 12B가 우위 → **4B 단순 축소는 품질 하락 가능성이 높음** | [4] |
| Qwen3-ASR | 공식 VRAM 수치 없음. 0.6B/1.7B 존재. 일본어 순위표에서 0.6B는 8위(점수 8.61, 지표 불명), whisper-small은 12.81 | [5] |
| whisper turbo int8 | NVIDIA GPU에서 VRAM 약 1.5GB. CPU(AMD 4750U)는 0.8배속(실시간 미달), 같은 칩 iGPU는 3.4배속 | [6][7] |
| whisper.cpp Vulkan | iGPU(Radeon 680M, Intel Arc)에서 CPU 대비 3~4배 개선 보고 (12배는 베이스라인이 느린 경우로 의심) | [8] |
| GTX 1050 Ti | 4GB GDDR5, 112GB/s, **FP16이 FP32의 1/64**, 텐서코어 없음 | [9] |
| **Pascal 지원 중단** | CUDA 13은 Pascal 컴파일 제거. PyTorch cu128 휠은 최소 sm_75, cu126이 마지막 Pascal 빌드(2.15부터 제거 예고). CTranslate2는 공식 언급 없음(미확인) | [10] |
| RTX 3080 10GB / 4080 16GB | 대역폭 760 / 736 GB/s. 12B Q4는 둘 다 적재 가능. 12B 직접 측정치는 못 찾음(추정 30~60 tok/s) | [11] |
| 노트북 CPU | 듀얼채널 DDR5 80~90GB/s 가정 시 12B Q4 상한 약 12 tok/s(실측은 50~80%), 4B는 약 35 tok/s. 실측 아님 | [12] |
| Steam 설문 2026-07 | VRAM 16GB 25.9%, 8GB 25.3%, 12GB 12.9%. 상위 GPU에 RTX 4060 Laptop(8GB), RTX 3060 | [13] |

## 3. 핵심 발견 (계획에 영향)

1. **8GB 구간 공백.** 사용자 정의 프로파일(4GB / 10GB / 16GB+) 사이에 가장 흔한 8GB GPU(4060 Laptop, 3060 Ti 등)가 빠져 있다.
   12B Q4_K_M(7.2GB) + KV + STT는 8GB에 들어가지 않는다. → **P2.5(8GB) 프로파일 추가 제안.**
2. **1050 Ti는 소프트웨어 호환성이 먼저 문제다.** CUDA 13/최신 PyTorch가 Pascal을 지원하지 않으므로
   torch ≤ 2.14 + cu126 고정 또는 torch 제거가 필요하다. FP16이 1/64라 `int8_float16`, Qwen3-ASR fp16 경로는 부적합 →
   int8/fp32 경로만 사용. 이는 보류 중이던 **"torch 제거(whisper.cpp / ONNX VAD)" 결정을 강하게 뒷받침**한다.
3. **품질 유지의 가장 유망한 경로는 TranslateGemma.** 벤더 주장대로 4B~12B가 동급 Gemma 3보다 번역 품질이 높다면
   (a) 하위 프로파일의 모델 축소 손실을 상쇄하고 (b) 상위 프로파일에서는 12B 교체만으로 "이상"을 노릴 수 있다.
   단 **GBNF 호환, 용어집/컨텍스트 프롬프트 적용 가능 여부(전용 프롬프트 템플릿), 한국어 구어체 품질**은 미확인 → 벤치마크 필수.
4. 노트북 CPU는 12B가 처리량 경계선이다. 사무용 노트북은 DDR4/단일 채널이 흔해 위 추정보다 더 느릴 가능성이 크다.

## 4. 프로파일 정의 (초안)

| 프로파일 | 대상 | STT | 번역 | 비고 |
|---|---|---|---|---|
| **P4** | RTX 4080 이상 (16GB+) | Qwen3-ASR 1.7B (현행) | gemma-3-12b Q4_K_M 전부 GPU (현행) | 기준선. 여유 VRAM으로 TranslateGemma-12B 등 상향 후보 평가 |
| **P3** | RTX 3080급 (10GB) | whisper large-v3-turbo int8 (약 1.5GB) | gemma-3-12b Q4_K_M 전부 GPU (약 8GB) | 합계 약 9.5GB로 빠듯 → KV q8, `n_ctx` 축소 필수. STT는 평가에서 동률 |
| **P2.5** (제안) | 8GB (4060 Laptop 등) | turbo int8 | 12B Q4_K_S 일부 CPU 분할 또는 TranslateGemma-4B/12B | 12B 전부 적재 불가. 분할 시 속도 측정 필요 |
| **P2** | GTX 1050 Ti (4GB) | turbo int8 또는 CPU | 4B급 GPU 적재(약 2.5GB) 또는 12B 분할 | Pascal 호환(§3-2) 선결. 이론 대역폭상 4B는 가능하나 STT와 합산 4GB는 빠듯 → STT는 CPU 분리 검토 |
| **P1** | 일반 사무용 노트북 (dGPU 없음) | whisper.cpp Vulkan iGPU 또는 CPU int8 | 4B급 CPU/Vulkan | 12B는 처리량 미달 가능. 최소 사양(RAM 16GB, 듀얼채널)을 문서로 고정하고 미달은 비지원 |

**확정된 제약 (2026-10-11 사용자 답변):** 클라우드 번역은 도입하지 않음(전부 로컬; 내가 가정으로 제시했던 것이라 철회).
프로파일은 GPU 메모리 용량 기준으로 나눈다. P1 = Intel iGPU + i5급. 모델 교체 허용.
**번역은 지연과 무관하게 끝까지 수행하고, 지연은 실측 후 결정한다(§1-2).** 따라서 오늘의 합격 기준은 지연이 아니라
"품질 ≥ 현행"이며, 지연은 프로파일별로 기록만 한다.

P1 메모리 대역폭 추정: i5 노트북 DDR4/LPDDR4x 약 40~60GB/s → 4B Q4(2.5GB) 이론 상한 16~24 tok/s, 실측 50~80%.
final 40토큰 번역에 최소 3~5초(12B는 그 이상) 예상 — 2초를 넘길 가능성이 높으므로 실측 지연 분포를 기준으로 사후 결정(추정, 측정 필요).

## 4-1. torch 제거 설계

현재 torch 사용처 (grep 확인): `vad.py`(silero torch.hub), `hallucination_gate.py`(sentence-transformers),
`stt/qwen3_asr_engine.py`(transformers), `music_gate.py`(미사용, 호출부 주석 처리됨).
faster-whisper 경로는 이미 torch 불필요.

| 대상 | 대체 | 속도 영향 |
|---|---|---|
| VAD | silero-vad 공식 ONNX + onnxruntime (CPU) | 32ms 프레임 단위 초소형 모델 → 체감 차 없음 |
| 환각 게이트 | MiniLM ONNX 변환 + onnxruntime (또는 fastembed) | 짧은 문장 임베딩 → 체감 차 없음 |
| STT | **Qwen3-ASR 제거**, whisper large-v3-turbo로 통일: whisper.cpp(`whisper-server`, llama-server와 같은 별도 프로세스 패턴) 또는 faster-whisper 유지 | turbo가 Qwen3-ASR보다 측정상 약 3배 빠름(RTF 0.033 vs 0.106) — 단 프레임워크가 아니라 모델 차이 |
| 번역 | llama.cpp 그대로 (torch 무관) | 변화 없음 |

- **"torch 제거 = 더 빠름"은 아니다.** 이득은 설치 용량(torch+CUDA 수 GB), 콜드스타트, Pascal/CUDA 13 호환, 단일 백엔드 단순화이고,
  속도 이득은 STT 모델을 Qwen3-ASR→turbo로 바꿀 때만 생긴다. 모든 프로파일 적용 조건: 150쌍 평가에서 turbo가 Qwen3-ASR와 품질 동률(기존 결과: 통계적 동률)이고
  end-to-end 지연이 같거나 낮을 것. 단 Qwen3-ASR는 2026-08-26 사용자 요청으로 채택·라이브 확인한 모델이므로 교체는 사용자 승인 후.
- whisper.cpp 선택 이유: Vulkan 빌드 하나로 NVIDIA/Intel iGPU/Pascal 모두 커버(CUDA 툴킷 의존 제거). NVIDIA 한정 속도는 CUDA 빌드가 유리할 수 있어
  프로파일별 빌드 선택. llama.cpp도 동일(CUDA/Vulkan 빌드). 미확인 사항: whisper.cpp가 faster-whisper 수준의
  per-segment no-speech/avg_logprob를 노출하는지(`WHISPER_*_THRESHOLD` 필터층 이식 가능 여부), partial용 반복 전사(0.6s 주기) 지연.
- music_gate는 미사용이므로 제거 대상이 아니라 "재활성화 시 Demucs 의존 재검토" 메모만 남김.

## 5. 후보 모델 (벤치마크 대상)

- 번역: gemma-3-12b(기준선), TranslateGemma 12B/4B, gemma-3-4b-it, 12B Q4_K_S, (조사 중 언급된 Gemma 4 계열은 존재·품질 모두 미확인)
- STT: Qwen3-ASR 1.7B(기준선), whisper large-v3-turbo(int8/fp16), Qwen3-ASR 0.6B, whisper small/distil 계열
- 평가: 기존 150쌍 평가셋(CER/chrF++/BLEU/ROUGE-L + LLM 판정). 일·한 번역은 COMET 또는 기존 LLM 판정 병행.
  허용 편차(품질 하한)는 기준선 대비 신뢰구간 겹침으로 정의 — 기존 Qwen3-ASR vs turbo 판정 방식과 동일.

## 6. 결정 필요 사항

1. ~~지연 허용치~~ → 번역은 끝까지 수행, 지연은 실측 후 결정(보류)
2. ~~클라우드 폴백~~ → 도입하지 않음으로 확정
3. P2.5(8GB) 추가 여부 (VRAM 기준 분류로 확정되면 자동 포함)
4. ~~모델 교체 승인~~ → 허용됨. torch 제거는 §4-1 조건(품질 동률 + 지연 비악화) 충족 시 전 프로파일 적용
5. 실측 후 결정할 것: 프로파일별 허용 지연 기준, 지연이 큰 프로파일의 지원/표기 방식
6. §8 평가 계획 확정 사항: 비열등성 마진 δ(기준선 재측정 후 확정). 평가는 개발기 단일 환경에서 프로파일 번들별로 수행(확정, §8.4) —
   타깃급 실기 검증은 후속(시기·방법 미정)

## 7. 작업 순서

1. 계측 스크립트: 단계별 VRAM 피크, STT RTF, 번역 tok/s(prefill/generation 분리), 대기열 길이. `llama-bench -ngl 0,10,20,99`로 분할 지점 탐색
2. 품질 하한선 확정 (기준선 점수 + 허용 편차)
3. 번역·STT 후보 벤치마크 → `research/topic/<날짜>_hw_profile_candidates/` (5-섹션 형식)
4. TranslateGemma 호환성 점검 (GBNF, 프롬프트 템플릿, 용어집)
5. Pascal/torch 의존성 정리 결정 → 프로파일 메커니즘(하드웨어 감지, config 프로파일, 열화 규칙) 설계
6. 실기 검증: P4(현 개발기) 외에 가능한 사양 확보 방안 점검 (1050 Ti, 8GB, 사무용 노트북 실기 부재 시 VRAM 제한/`CUDA_VISIBLE_DEVICES`·CPU 강제로 시뮬레이션 가능하나 속도 대표성은 낮음)

## 8. 평가 계획 (모든 세션이 이 절차를 그대로 따른다)

기존 자산을 재사용해 세션 간 결과가 비교 가능하도록 한다. 방법론의 상위 규칙은 `docs/eval/EVAL.md`
(정규화·CER·chrF++·§6 공정성 검토)이며, 이 절은 "하드웨어 프로파일"에 특화된 층위만 정의한다.

### 8.1 재사용 자산 (변경 금지 — 바꾸면 새 버전으로 분리)

| 용도 | 자산 |
|---|---|
| STT 품질 | `data/wav/` 150쌍(5카테고리×30), `research/topic/20260826_stt_model_survey_gpu_full/src/common.py` 로더·정규화, `score_quantitative.py`, `analyze_ci_and_plot.py`(페어드 부트스트랩 500회, 95% CI) |
| STT 정성 | 같은 토픽의 50세그먼트 페어드 수동 채점(자연스러움/의미 충실도 1~5) |
| 번역 품질 | `data/eval_set_2026-08-18.jsonl` 120클립. `ja_ref`→번역 vs `ko_ref`(STT 오류 배제), chrF++(`word_order=2`), 정성 3축(의미 충실도/유창성/뉘앙스) 수동 채점. `research/topic/20260826_translation_model_survey/` 스크립트 |
| RTF 정의 | 처리 소요시간 합 / 원본 오디오 길이 합, 순차 측정(동시 실행 금지) |
| 파이프라인 재생 | `research/topic/20260827_vad_stt_survey/` 하네스(0.3s 청크 → VAD → partial/finalize 큐, `virtual_clock.py`) |
| 환각 회귀 | `data/flagged_segments.jsonl`(사용자가 라벨링한 환각 사례) |
| 라이브 세션 | `data/sessions/` jsonl 로그 |

### 8.2 기준선 고정 (Baseline lock)

- 기준선 = **현행 구성**(Qwen3-ASR-1.7B + gemma-3-12b-it Q4_K_M, 프로덕션 `config.py` 그대로)을 P4 개발기에서
  위 하네스로 1회 재측정해 `baseline-2026-10`으로 고정한다. 과거 리포트 수치를 직접 가져다 쓰지 않는다
  (하네스/버전이 달라졌을 수 있음).
- 모든 후보는 이 기준선과 **페어드 비교**한다(같은 seg_id 순서, 같은 데이터 파일).
- 기록 필수: git commit hash, 데이터셋 파일 해시, 모델 파일명+양자화+sha, 추론 엔진/빌드(CUDA/Vulkan/CPU)와 버전,
  디코딩 파라미터(beam, max_tokens, temperature, repetition_penalty), 드라이버 버전.

### 8.3 평가 층위

| 층 | 무엇을 | 하드웨어 의존 | 실행 위치 |
|---|---|---|---|
| **L1 컴포넌트 품질** | 후보 모델(+양자화)의 STT·번역 품질 | 거의 없음(모델·양자화에 의존) | 어디서든 1회 (결과를 모든 프로파일이 공유) |
| **L1b 백엔드 동등성** | 같은 모델이 CUDA / Vulkan / CPU 빌드에서 같은 출력인가 | 백엔드 | 프로파일이 쓰는 백엔드마다 30세그먼트 스모크 |
| **L1c torch 제거 동등성** | ONNX VAD·임베딩이 원본과 같은가 | 없음 | 1회 |
| **L2 자원/처리량** | VRAM·RAM 피크, STT RTF, 번역 tok/s | 용량은 낮음, 속도는 **높음** | 개발기(프로파일 번들별), 속도는 §8.4 규칙대로 상한 해석 |
| **L3 종단 지연 재생** | 말 끝→한국어 final 도착 지연 분포와 누적(드리프트) | **높음** | 개발기(번들별), 값은 DEV 기준 |
| **L4 라이브 스모크** | 실제 일본어 라이브 15분 사람 확인 | 높음 | 개발기(번들별). 타깃급 실기 검증은 별도·후속 |

**L1 합격 기준 (비열등성)**
- 지표: STT = CER(낮을수록 좋음)·chrF++·BLEU·ROUGE-L, 번역 = chrF++. 후보−기준선 **페어드 차이의 95% CI**로 판정.
- 합격 = CER 차이 CI 상한 ≤ +δ_cer, chrF++/BLEU/ROUGE-L 차이 CI 하한 ≥ −δ_chrf. 정성(의미 충실도)은 기준선 대비 CI가 0을 유의하게 아래로 제외하지 않을 것.
- δ는 **기준선 재측정 시 CI 반폭을 보고 확정**한다(잠정 제안: δ_cer=0.01 절대값, δ_chrf=0.5). 사전에 정한 값은 근거 없는 추정이므로 §6 결정 항목.
- "이상"(개선) 판정은 CI가 0을 제외하고 개선 방향일 때만.
- EVAL.md §6 공정성 검토 필수: 문서화된 추론 경로, 디코딩 파라미터 패리티(프로덕션 값 그대로), 모델별 강건성 처리, 동일 전처리.
- 양자화 변경(Q4_K_M→Q4_K_S, int8_float16→int8 등)은 **실제 프로파일이 쓰는 그 양자화로** 측정한다.

**L1b**: 같은 150쌍 중 카테고리별 6개씩 30세그먼트로 CER 차이가 CI 내인지, 문자열 불일치율을 기록.

**L1c**: ① VAD: 150클립 프레임별 speech prob 최대 절대 오차 < 1e-3, 발화 경계(시작/끝) 일치율 ≥ 99%.
② 환각 게이트: 임베딩 코사인 ≥ 0.999, `data/flagged_segments.jsonl` + 정상 전사 샘플에서 차단/통과 판정 **100% 동일**.

**L2 측정 규칙**
- 항목: 프로세스별 VRAM 피크(상주+추론 중), 시스템 RAM 피크, STT RTF(partial greedy 경로와 final beam=5 경로 분리),
  번역 prefill tok/s·generation tok/s(`llama-bench`, `-ngl` 스윕), 모델 로드 시간(콜드스타트).
- 3회 반복의 중앙값, 워밍업 1회 제외. 노트북은 **전원 연결/배터리 두 조건**, 10분 지속 부하에서 클럭 저하(서멀) 기록.
- STT·번역이 GPU를 직렬로 쓰므로(커밋 806585b) **동시 가동 상태**에서도 측정한다(개별 측정만으로는 부족).

**L3 종단 지연 재생 규칙**
- 입력: 150클립을 실제 시간 속도(1.0×)로 이어 붙인 10분 이상 세션(긴 세션 유지 시 열/메모리 누적 포착). 기준 오디오는 모든 프로파일에서 동일 파일.
- 타임아웃 폴백(`LLAMA_SERVER_TIMEOUT_S`, `STT_*_TIMEOUT_S`)은 **해제**하고 끝까지 번역(§1-2). 대기열 상한 없음.
- 기록: 문장별 ① 말 끝(VAD 종단)→일본어 final, ② 말 끝→한국어 final, ③ 첫 partial 지연, ④ finalize 큐 깊이.
  요약: p50/p95/max, **드리프트 기울기(지연 vs 경과시간 선형 회귀, 초/분)**, 처리 못한 문장 수(0이어야 함).
- 가상 시계 하네스를 쓰되 실기 추론 시간이 실제로 소요되는 모드로 돌린다(시뮬 시간만으로는 지연이 안 나옴).
- 이 단계 결과로 **지연 기준을 사후에 정한다**(지금은 게이트 없음, 기록 전용).

**L4**: 실제 일본어 라이브 방송 15분, 사람 체크리스트 — 체감 지연, 환각·누락, 한국어 오역 의심 문장 수(`flagged_segments` 방식으로 기록),
`backend_run.log` 확인(오디오/STT 파이프라인 변경 후 필수 — 과거 공유 MediaStream 문제 사례).

### 8.4 평가 방식: 개발기에서 "프로파일 파이프라인"을 실행 (2026-10-11 사용자 결정)

모든 평가는 **현재 개발기(RTX 4080 SUPER)** 에서 수행한다. 타깃 하드웨어를 동일하게 재현하지 않고,
**각 프로파일이 쓰게 될 파이프라인 번들을 개발기에서 그대로 실행해 그 퍼포먼스를 평가**한다.

- **프로파일 파이프라인 번들** = 모델 + 양자화 + 추론 엔진/빌드 + 디코딩 파라미터 + `config` 값(`PARTIAL_UPDATE_INTERVAL_S`,
  `n_ctx`, KV 양자화, `-ngl` 등) + VAD/게이트 구현(torch/ONNX). 번들은 `profiles/<P>.yaml`처럼 **한 파일로 정의**하고
  manifest에 그 해시를 기록한다(편집 가능한 설정은 YAML — 프로젝트 관례).
- 같은 번들이 타깃 기기에서도 그대로 돌아가야 하므로, 번들 정의에는 "그 프로파일의 VRAM 예산"(P2=4GB, P2.5=8GB, P3=10GB, P4=16GB+)을
  함께 적는다.

**측정값의 효력 (개발기 한계를 명시)**

| 측정 | 개발기에서의 효력 | 사용법 |
|---|---|---|
| L1 품질 (CER/chrF++/정성) | **유효** (하드웨어 독립) | 합격/불합격 판정에 직접 사용 |
| L1b/L1c 동등성 | **유효** | 직접 사용. 단 Vulkan/CPU 빌드는 개발기에서도 실행 가능하므로 빌드별로 측정 |
| VRAM·RAM 피크, 모델 크기 | **유효** (용량은 하드웨어 독립) | 번들의 VRAM 예산 대비 **적재 가능 여부 판정**에 직접 사용(예산 초과 = 해당 프로파일 탈락). 예산 격리: llama.cpp `-ngl`/`--ctx-size`, 프로세스별 VRAM 합산으로 확인 |
| RTF, tok/s, 지연, 드리프트 | **개발기 기준 값** — 타깃보다 빠름 | **상한(best case)** 으로만 해석: 개발기에서도 느리면 타깃은 더 느리다(탈락 근거 가능). 개발기에서 빠르다고 타깃에서 충분하다는 보증은 아님 |

- **외삽은 추정으로만 표기한다.** 타깃 속도 추정은 대역폭·연산 비율(예: 4080 736GB/s → 1050 Ti 112GB/s, CPU DDR 40~90GB/s)로
  "예상 최악 하한"을 산출하되 리포트에 `추정(미실측)` 라벨을 붙이고 판정 열과 분리한다.
- 모든 L2/L3 표에 `측정환경: DEV(4080 SUPER) | 추정(방법)` 열을 둔다. **추정값으로 프로파일 통과를 선언하지 않는다.**
  통과 선언은 타깃급 실기 검증(L4, 가능해지는 시점)을 거친 뒤에만 한다 — 그 전까지 판정은 "개발기 기준 통과 / 실기 미검증"으로 표기.
- 서로 다른 번들은 같은 기기·같은 세션 조건(전원 모드 고성능, 백그라운드 부하 없음, GPU 온도 안정화 후)에서 **순차 실행**해
  번들 간 상대 비교가 성립하게 한다. 개발기가 게임 등 다른 GPU 사용 중이면 측정을 보류(과거 GPU 경합 이슈).
- 같은 이유로 **프로파일 간 속도 비교는 "번들 A가 번들 B보다 몇 배 가볍다"의 상대값**으로 보고하고, 절대 지연은 DEV 값으로만 기록한다.

### 8.5 산출물과 세션 간 일관성

- 위치: `research/topic/<날짜>_hw_profile_eval_<profile>/` (5-섹션 리포트 형식, `reference_research-folder-convention` 따름).
  각 폴더에 `manifest.json`(§8.2 기록 필수 항목 + `측정환경`) 포함. 스크립트는 기준선 토픽의 것을 복제하지 않고 **import/참조**해 로직을 단일화.
- 요약 허브: `docs/eval/HW_PROFILES_RESULTS.md` (프로파일 × L1~L4 한 장 표, 마지막 갱신일·기준선 버전). 각 평가 세션은 끝날 때 이 표를 갱신.
- 비교 규칙: **다른 기준선 버전의 수치를 섞어 비교하지 않는다.** 하네스·데이터·기준선이 바뀌면 `baseline-YYYY-MM`을 새로 따고 전 프로파일 재측정.
- 변경 후 재평가 트리거: 모델/양자화/엔진 빌드/디코딩 파라미터/VAD·게이트 변경 시 → L1 스모크(변경 성격에 따라 풀) + L3 재실행.

### 8.6 결과 판정표 (지연 기준은 실측 후 채움)

| 프로파일 | 번들(yaml 해시) | L1 품질(비열등) | VRAM 피크 / 예산 | RAM 피크 | RTF·tok/s (DEV) | L3 p50/p95 (DEV) | L3 드리프트 (DEV) | 타깃 속도 추정(미실측) | 판정 |
|---|---|---|---|---|---|---|---|---|---|
| P4 | 기준선 | (기준) | / 16GB+ | | | | | | |
| P3 | | | / 10GB | | | | | | |
| P2.5 | | | / 8GB | | | | | | |
| P2 | | | / 4GB | | | | | | |
| P1 | | | iGPU 공유 | | | | | | |

판정 규칙(잠정): ① L1 불합격 또는 **VRAM 예산 초과**면 그 번들은 탈락(지연과 무관). ② 개발기에서도 드리프트 > 0(대기열이 계속 증가)이면
"실시간 지속 불가"로 표기 — 타깃은 더 느리므로 확정 탈락 근거가 된다. ③ 개발기 통과는 "개발기 기준 통과 / 실기 미검증"으로만 표기하고,
지연 기준은 이 표를 보고 사용자가 사후에 정한다.

## 9. 구현 현황 (2026-10-11) — 코드 구성 완료, 구동·평가 대기

### 9.1 브랜치 구성 (origin 푸시 완료, main 에는 미병합)

| 브랜치 | 내용 |
|---|---|
| `profile/base` | 공통 구성요소 — 프로파일 로더(`backend/profile.py`, `LT_PROFILE`/`LT_PROFILE_OVERLAYS`, `profiles/_measure.yaml`), ONNX VAD(`VAD_BACKEND`), ONNX 환각 게이트(`HALLUCINATION_GATE_BACKEND`), whisper.cpp STT 엔진(`STT_ENGINE=whisper-cpp`), 런처의 whisper-server 기동, `backend/requirements-lite.txt` (torch-free 의존성, ASCII 전용 + NVIDIA 런타임 휠), `backend/cuda_dll_path.py` (Windows 에서 pip 휠의 cuBLAS/cuDNN DLL 경로 추가) |
| `profile/p4-16gb` | 기준선 고정값(Qwen3-ASR 1.7B + gemma-3-12b Q4_K_M) |
| `profile/p3-10gb` | faster-whisper turbo int8_float16 + 12B 전부 GPU(`-c 4096`, KV q8_0), torch-free |
| `profile/p2.5-8gb` | P3 와 같은 STT + 12B 부분 오프로드(`-ngl 34` 가설), torch-free |
| `profile/p2-4gb` | whisper.cpp(turbo q5_0) + gemma-3-4b-it Q4_K_M(`-ngl 26` 가설), torch-free |
| `profile/p1-igpu` | whisper.cpp·llama.cpp Vulkan 빌드 + gemma-3-4b-it Q4_K_M, torch-free |

- 프로파일 브랜치는 `profile/base` 위에서 `profiles/<name>.yaml` + `profiles/<name>.md` 두 파일만 추가한다(이름: p4, p3, p25, p2, p1).
- 사용: `$env:LT_PROFILE='p3'` 후 `start.cmd`(런처가 yaml 의 llama/whisper 인자로 기동) 또는 `uvicorn`. 지연 실측 시 `$env:LT_PROFILE_OVERLAYS='_measure'` 로 타임아웃 폴백 해제.
- **프로파일 yaml 의 모든 수치(`-ngl`, `-c`, 모델 선택, `PARTIAL_UPDATE_INTERVAL_S` 등)는 초기 가설**이다. 평가 세션에서 확정한다.

### 9.2 구현 단계에서 확인된 것

- 기본 동작 불변: `LT_PROFILE` 미설정 시 `backend/config.py` 대문자 상수 전체가 변경 전과 동일. P4 적용 시에도 동일(전체 상수 덤프 비교).
- ONNX VAD ↔ torch silero: 20클립·3,755프레임 최대 절대 오차 7.3e-06, 판정 일치 100% (L1c 기준 충족).
- ONNX 환각 게이트 ↔ sentence-transformers: 77개 텍스트 코사인 최솟값 1.000000, 판정 불일치 0건 (L1c 기준 충족).
- torch-free 구성(VAD/게이트 onnx + faster-whisper 또는 whisper-cpp)에서 `backend.main` import 시 torch/transformers/sentence_transformers 미로드.
- whisper.cpp 엔진은 가짜 서버로 18건 검증(WAV 형식, fast/final 라우팅, 응답 파싱, 연결 실패 메시지). **실제 whisper-server 로는 미검증.**

### 9.3 알려진 미검증·미해결 (평가 세션에서 해소)

1. whisper-server 가 요청별 `beam_size`/`language` 를 지원하는가 — 미지원이면 시작 시 `-bs` 가 fast/final 에 모두 적용됨(필요하면 `WHISPER_CPP_FINAL_URL` 에 beam=5 서버 추가).
2. whisper-server `verbose_json` 이 `no_speech_prob`/`avg_logprob` 를 주는가 — 없으면 임계값 필터층 inert(Qwen3-ASR 경로와 동일 상태), 임베딩 게이트만 동작.
3. Pascal(sm_61)용 llama.cpp/whisper.cpp 빌드(CUDA 12.x 또는 Vulkan) 가용성. 저장소의 `llama-server/` 빌드가 sm_61 을 포함하는지 미확인.
4. Vulkan 빌드 디렉터리(`llama-server-vulkan/`, `whisper-server-vulkan/`)는 저장소에 없음. 모델 파일(`gemma-3-4b-it` GGUF, `ggml-large-v3-turbo-q5_0.bin`)도 미준비.
5. ~~`requirements-lite.txt` 설치 검증 없음~~ → **2026-10-11 격리 venv 에서 검증, 결함 2건 수정 후 `profile/base` 에 반영.** (a) 파일의 한글 주석 때문에 한국어 Windows(cp949)에서 pip 가 읽지 못함 → ASCII 주석으로 교체. (b) torch 없는 환경에서 faster-whisper GPU 추론이 `cublas64_12.dll is not found` 로 실패 → `nvidia-cublas-cu12`/`nvidia-cudnn-cu12` 추가 + `backend/cuda_dll_path.py`(pip 휠의 DLL 디렉터리를 Windows DLL 검색 경로에 추가, 해당 디렉터리가 없으면 no-op)를 `faster_whisper_engine.py` 가 호출. 수정 후 torch 없는 venv 에서 tiny 모델 `int8_float16` GPU 추론 통과, torch 환경(`live-translator` conda env)에서는 import 정상(no-op). 프로파일 5개 브랜치에 `profile/base` 병합 완료.
6. 런처(`tray_launcher.ps1`) 변경은 PowerShell 문법 검사만 했고 실행 검증 전. 트레이/종료/PID 정리는 미실행.
7. 4B 번역 품질(P2·P1), 12B `-c 4096`/KV q8_0 의 품질 영향, turbo 대 Qwen3-ASR 의 파이프라인 통과 시 환각 방어 구성 차이(faster-whisper 는 확률 필터 + 임베딩 게이트, Qwen3-ASR 은 임베딩 게이트만).
8. `README.md` 수동 실행 예시는 `-c 4096` 이지만 런처 기본값·P4 는 `-c 8192` (기준선은 런처 값). README 정정은 미반영.

### 9.4 남은 작업 (구동 및 평가) — 순서

구동·평가 외에 평가를 돌리기 위한 **선행 준비**가 있다.

1. **준비**: 프로파일별 바이너리(llama-server CUDA/Vulkan, whisper-server)·모델 파일 확보(§9.3-3,4). 모델 파일명은 각 `profiles/<name>.md` 참고.
2. **측정 도구 작성**: §8 의 L2(VRAM 피크 샘플러, llama-bench 래퍼)·L3(문장별 지연·드리프트 기록기, 가상 시계 하네스 실시간 모드)는 **계획만 있고 구현되지 않았다**. 평가 전에 만들어야 한다.
3. **기준선 고정**: P4 를 `baseline-2026-10` 으로 재측정(§8.2). 이때 비열등 마진 δ 확정(§6).
4. **L1 품질**: 프로파일별 번들(turbo, 4B 등)을 150쌍/120클립에 통과 → 비열등성 판정. 탈락 번들은 대안 후보(TranslateGemma 등)로 교체.
5. **L2/L3**: 각 프로파일 번들을 개발기에서 순차 실행(§8.4 규칙), 지연·드리프트 기록. `-ngl` 스윕으로 P2.5·P2 의 오프로드 값 확정.
6. **L4 + 판정표(§8.6) 작성**, 지연 기준은 이 결과를 보고 사용자가 결정. 타깃급 실기 검증은 후속.

## 10. 환경 준비 (다른 세션이 이 절만 보고 따라 할 수 있도록)

> **이 계획서의 최신본은 `main` 브랜치의 `docs/planning/HARDWARE_PROFILES.md` 이다.** `profile/*` 브랜치는 이 문서가 갱신되기 전의 `main` 에서 갈라졌으므로 그 안의 사본은 낡았을 수 있다. 세션은 `$MAIN`(main 체크아웃)에서 이 문서를 읽고, 작업은 `$EVAL` worktree 에서 한다.

**실행 기기는 현재 로컬 개발기 하나다**: Windows 11 Home, RTX 4080 SUPER 16GB(드라이버 591.86), PowerShell, conda 설치 경로 `C:\Users\User\miniconda3`. 컨테이너·WSL·원격 기기는 쓰지 않는다(§8.4: 다른 프로파일은 이 기기에서 그 번들을 실행해 평가). 아래 변수는 이후 모든 명령에서 공통으로 쓴다.

```powershell
$R    = "C:\Users\User\Documents\pyworks"
$MAIN = "$R\live-translator"          # 기존 체크아웃(main). 건드리지 않는다
$EVAL = "$R\lt-wt\eval"                # 평가 전용 worktree (§10.1)
$ENVS = "$R\lt-envs"                   # 평가용 venv 보관
```

### 10.0 지켜야 할 규칙 (현재 환경에 간섭하지 않기)

- 기존 conda env(`live-translator`, `reazonspeech`) 에 **패키지를 설치/업그레이드하지 않는다.** (과거 `nemo_toolkit` 설치로 torch 가 CPU 전용으로 바뀌어 CUDA 가 깨진 사례 — `research/topic/20260826_stt_model_survey_gpu_full/README.md`.) 새 패키지는 §10.2 의 새 venv 에만 설치.
- `$MAIN` 에서 브랜치를 전환하지 않는다. `$MAIN` 에는 커밋되지 않은 `data/flagged_segments.jsonl` 수정이 있다. 모든 작업은 `$EVAL` worktree 에서.
- 모델·바이너리 다운로드는 용량이 크다(§10.4 표). **사용자 승인 후** 받는다.
- 서버(llama 8080, backend 8000, whisper 8081)는 **한 번에 한 프로파일만** 띄운다. 프로파일을 바꿀 때마다 전부 종료하고 `nvidia-smi` 로 VRAM 이 비었는지(기준 약 0.9GB, 데스크톱 점유분) 확인한다. 다른 GPU 사용(게임 등)이 있으면 측정하지 않는다.
- Windows 콘솔 인코딩: 모든 파이썬 실행 전에 `$env:PYTHONUTF8 = "1"`.
- `tray_launcher.ps1`/`start.cmd` 는 평가에 쓰지 않는다 — 백엔드 실행 파일 경로가 conda env 의 `uvicorn.exe` 로 하드코딩돼 있어 venv 를 못 쓴다. §11 의 수동 기동 명령을 사용한다.

### 10.1 코드: 평가 전용 worktree (`profile/eval`)

프로파일 5개 브랜치는 `profile/base` 와 비교해 **`profiles/<이름>.yaml`·`.md` 만 다르다**(2026-10-11 확인, 서로 충돌 없이 병합됨). 그래서 브랜치마다 worktree/환경을 따로 만들 필요 없이, 5개를 모두 병합한 통합 브랜치 하나에서 `LT_PROFILE` 만 바꿔 모든 프로파일을 돌린다. 평가용 도구(§12.3)와 결과물도 이 브랜치에 쌓는다.

```powershell
git -C $MAIN fetch origin
# 로컬에 브랜치가 없는 세션이면 먼저: git -C $MAIN branch profile/p3-10gb origin/profile/p3-10gb  (5개 + base 모두)
New-Item -ItemType Directory -Force "$R\lt-wt", $ENVS | Out-Null
git -C $MAIN worktree add -b profile/eval "$R\lt-wt\eval" profile/base
git -C $EVAL merge --no-ff -m "eval line: all profile bundles" profile/p4-16gb profile/p3-10gb profile/p2.5-8gb profile/p2-4gb profile/p1-igpu
& "C:\Users\User\miniconda3\envs\live-translator\python.exe" -m backend.profile validate   # cwd=$EVAL; OK 6줄(_measure,p1,p2,p25,p3,p4)
```

이미 `profile/eval` 이 있으면 `git -C $MAIN worktree add "$R\lt-wt\eval" profile/eval` 로 붙인다. 프로파일 브랜치가 갱신되면 `git -C $EVAL merge <branch>` 로 가져온다.

> 브랜치별 충실도를 직접 확인하고 싶으면(코드가 yaml 외에 갈라졌는지) `git diff --name-only profile/base profile/<branch>` 가 `profiles/<이름>.{yaml,md}` 두 파일만 나와야 정상이다.

### 10.2 파이썬 환경 (2종)

| 환경 | 대상 | 만드는 법 | 비고 |
|---|---|---|---|
| **E-full** | **P4** | 새로 만들지 않음. 기존 conda env `live-translator`(Python 3.11.15, torch 2.6.0+cu124) 그대로 | 기준선 환경. 수정 금지 |
| **E-lite** | P3, P2.5, P2, P1 | 아래 | torch/transformers/sentence-transformers 없음 |

```powershell
# E-lite (conda python 3.11 을 베이스로 venv — 새 패키지는 이 venv 에만 들어간다)
& "C:\Users\User\miniconda3\envs\live-translator\python.exe" -m venv "$ENVS\lite"
& "$ENVS\lite\Scripts\python.exe" -m pip install --disable-pip-version-check -r "$EVAL\backend\requirements-lite.txt"
# 평가 스크립트가 쓰는 추가 패키지 (E-lite 와 E-full 에 이미 있는 것은 설치 불필요)
& "$ENVS\lite\Scripts\python.exe" -m pip install soundfile scipy jiwer sacrebleu matplotlib websockets psutil
```

E-lite 검증 결과(2026-10-11, 격리 venv): `ctranslate2 4.8.2`, `faster-whisper 1.2.1`, `onnxruntime 1.31.0`, `nvidia-cublas-cu12 12.9`, `nvidia-cudnn-cu12 9.27` 설치 후 tiny 모델 `int8_float16` GPU 추론 통과(`add_nvidia_wheel_dll_dirs()` 경유), `torch` 미로드. HF 캐시 symlink 경고(`Developer Mode` 안내)는 무시해도 된다.

설치 후 확인:

```powershell
$env:PYTHONUTF8 = "1"
& "$ENVS\lite\Scripts\python.exe" -c "import sys, backend.config as c; import backend.main; print('torch' in sys.modules)"   # cwd=$EVAL, 환경변수 LT_PROFILE=p3 로도 한 번 → False
```

### 10.3 데이터·모델·바이너리 연결 (복사 없이 NTFS 정션)

`backend/models/`, `data/wav/`, `data/json/`, `data/sessions/`, `llama-server/` 는 `.gitignore` 대상이라 worktree 에 없다. `$MAIN` 의 것을 정션으로 연결한다(원본은 읽기만 한다; 새 모델은 `$MAIN\backend\models` 에 받는다).

```powershell
foreach ($p in "backend\models","data\wav","data\json","data\sessions","llama-server") {
  New-Item -ItemType Junction -Path "$EVAL\$p" -Target "$MAIN\$p" | Out-Null
}
```

새 바이너리 폴더(`whisper-server`, `llama-server-vulkan`, `whisper-server-vulkan`)는 `$R\lt-shared\bin\<이름>` 에 풀고 같은 방식으로 `$EVAL\<이름>` 에 정션을 건다. 존재 확인:

```powershell
Test-Path "$EVAL\backend\models\google_gemma-3-12b-it-Q4_K_M.gguf"      # True
(Get-ChildItem "$EVAL\data\wav" -Recurse -Filter *.wav).Count            # 150
Test-Path "$EVAL\llama-server\llama-server.exe"                          # True (CUDA 12 빌드 b10453, llama-bench.exe 포함)
```

### 10.4 프로파일별로 필요한 것 (브랜치 → 환경·바이너리·모델)

크기는 대략치이며 다운로드 전 실제 크기를 확인한다. "현재"는 개발기에 이미 있다는 뜻.

| 브랜치 / LT_PROFILE | 파이썬 | STT | 번역 | 필요 바이너리 | 필요 모델 파일 |
|---|---|---|---|---|---|
| `profile/p4-16gb` / `p4` | E-full | Qwen3-ASR 1.7B (HF 자동, 첫 실행 시 다운로드; 캐시에 이미 있음) | llama-server(CUDA) | 현재 | 현재: `google_gemma-3-12b-it-Q4_K_M.gguf` (7.3GB) |
| `profile/p3-10gb` / `p3` | E-lite | faster-whisper `large-v3-turbo` (HF 자동, 약 1.6GB) | llama-server(CUDA) | 현재 | 현재 12B GGUF |
| `profile/p2.5-8gb` / `p25` | E-lite | 같음 | llama-server(CUDA), `-ngl 34` | 현재 | 현재 12B GGUF |
| `profile/p2-4gb` / `p2` | E-lite | **whisper.cpp** `whisper-server` | llama-server(CUDA) + gemma-3-4b | **신규**: `whisper-server`(CUDA 12 또는 CPU 빌드) | **신규**: `ggml-large-v3-turbo-q5_0.bin`(≈0.6GB, HF `ggerganov/whisper.cpp`), `google_gemma-3-4b-it-Q4_K_M.gguf`(≈2.5GB, HF `bartowski/google_gemma-3-4b-it-GGUF`) |
| `profile/p1-igpu` / `p1` | E-lite | **whisper.cpp** (Vulkan) | llama-server **Vulkan** + gemma-3-4b | **신규**: `whisper-server-vulkan`, `llama-server-vulkan` | P2 와 같은 2개 파일 |

- whisper.cpp·llama.cpp 바이너리는 각 프로젝트의 GitHub Releases 의 Windows 빌드를 우선 확인하고(llama.cpp 는 `win-vulkan-x64` 등 제공), Vulkan 사전 빌드가 없으면 소스 빌드(`cmake -DGGML_VULKAN=1`; Vulkan SDK + VS Build Tools 필요). **미확인**: 어떤 사전 빌드가 있는지.
- 이 기기에서 P2/P1 의 Vulkan·CUDA 빌드는 **RTX 4080 위에서** 돈다(§8.4). 어느 장치에서 실행됐는지 `manifest.json` 의 `device` 에 기록한다(llama.cpp: `llama-server --list-devices`). 속도는 DEV 값으로만 해석한다.
- P2 의 Pascal(sm_61) 호환은 이 기기로 검증할 수 없다 — 평가 결과의 `미검증` 항목으로 남긴다.

## 11. 구동 방법 (프로파일 공통 절차, 수동 기동)

`$P` = 프로파일 이름(`p4|p3|p25|p2|p1`), `$PY` = P4 면 `C:\Users\User\miniconda3\envs\live-translator\python.exe`, 그 외면 `$ENVS\lite\Scripts\python.exe`. 모두 `$EVAL` 에서 실행. **지연 실측(L3)에는 `_measure` 오버레이를 켠다**(타임아웃 폴백 해제, 아니면 켜지 않고 품질만 본다).

```powershell
Set-Location $EVAL
$env:PYTHONUTF8 = "1"
$env:LT_PROFILE = $P                     # 예: "p3"
$env:LT_PROFILE_OVERLAYS = "_measure"    # L3 때만. L1/L2 에서는 비움
nvidia-smi --query-gpu=memory.used --format=csv,noheader   # 기동 전 기준 VRAM 기록 (~900MiB)

# 1) whisper-server (프로파일에 whisper_server 섹션이 있을 때만: p2, p1)
$w = & $PY -m backend.profile launch-args whisper $P 2>$null | ConvertFrom-Json
if ($LASTEXITCODE -eq 0) {
  Start-Process $w.exe -ArgumentList (@("-m", $w.model, "--port", $w.port) + $w.args) -WorkingDirectory $EVAL -WindowStyle Hidden `
    -RedirectStandardOutput "$EVAL\whisper_server.log" -RedirectStandardError "$EVAL\whisper_server.err.log"
}

# 2) llama-server (번역)
$l = & $PY -m backend.profile launch-args llama $P | ConvertFrom-Json
Start-Process $l.exe -ArgumentList (@("-m", $l.model, "--port", $l.port) + $l.args) -WorkingDirectory $EVAL -WindowStyle Hidden `
  -RedirectStandardOutput "$EVAL\llama_server.log" -RedirectStandardError "$EVAL\llama_server.err.log"
# 준비 대기: 200 이 올 때까지  (Invoke-WebRequest http://127.0.0.1:$($l.port)/health)

# 3) 백엔드
Start-Process $PY -ArgumentList "-m","uvicorn","backend.main:app","--port","8000" -WorkingDirectory $EVAL -WindowStyle Hidden `
  -RedirectStandardOutput "$EVAL\backend_run.log" -RedirectStandardError "$EVAL\backend_run.err.log"
```

**기동 성공 판정 — `backend_run.err.log`(uvicorn/로거는 stderr) 에서 다음을 모두 확인한다**(과거 파이프라인 변경 후 이 로그를 보지 않아 놓친 사례가 있다):
1. `Profile '<P>' applied (N overrides)` (+ 오버레이 사용 시 overlays 표시)
2. `STT model ready (engine=<STT_ENGINE>)`
3. `Translation engine ready` 와 `verify_contract` 경고 없음(GBNF `grammar` 지원 확인)
4. 에러·`Traceback` 없음. VAD/게이트 백엔드가 onnx 인 프로파일은 `Hallucination gate ready (…backend=onnx…)`.

**종료**: 세 프로세스를 이름으로 정지하고(`Stop-Process -Name llama-server,whisper-server,uvicorn,python -Force` 는 다른 python 을 죽일 수 있으므로 **이 worktree 에서 띄운 PID 만** — `Start-Process -PassThru` 로 PID 를 변수에 담아 `Stop-Process -Id`), `nvidia-smi` 로 VRAM 반환을 확인한다.

**P4 동등성 스모크**: `LT_PROFILE=p4` 와 미설정에서 기동 로그·응답이 같아야 한다(config 상수 덤프 동일은 이미 확인됨, §9.2).

## 12. 평가 (프로파일 공통 — 정책은 §8, 이 절은 실행 절차)

정책(기준선 고정, 비열등성, 개발기 효력 규칙, 판정표)은 §8 이 우선이다. 모든 프로파일(P4 포함)을 **같은 절차·같은 입력·같은 도구 버전**으로 돈다. 한 번의 평가 세션 = 한 프로파일 1회 전체 사이클.

### 12.1 순서 (프로파일마다 반복)

| 단계 | 내용 | 도구 | 산출 |
|---|---|---|---|
| E0 | 기동 + 판정(§11), `manifest.json` 초안 작성 | — | `manifest.json` |
| E1 | **L1c 동등성(1회, base 기준)** — 프로파일과 무관, 이미 통과(§9.2). 새 환경에서 회귀 확인용으로만 재실행 | `scripts/verify_vad_parity.py`, `scripts/verify_gate_parity.py`, `scripts/verify_whisper_cpp_engine.py` | 콘솔 PASS |
| E2 | **L1 STT 품질** — 프로파일 파이프라인 통과 150클립 | `run_pipeline.py --engine profile` (T1, **신규**) → `score_quantitative.py` | `out/<P>/pipeline_transcripts.jsonl`, `quant_summary.json` |
| E3 | **L1 번역 품질** — llama-server 가 프로파일 모델로 떠 있는 상태에서 120클립 | `research/topic/20260826_translation_model_survey/src/translate_llm.py --label <P>` → `score_chrf.py --method <P>` | `out/<P>/…`, chrF++ |
| E4 | **L2 자원** — 기동 직후/E2·E3 수행 중 VRAM·RAM 피크, `llama-bench -ngl` 스윕 | T2 (**신규**), `llama-server\llama-bench.exe` | `l2.json` |
| E5 | **L3 종단 지연** — `_measure` 오버레이로 재기동 후 11분+ 실시간 재생 | T3 (**신규**) | `l3_events.jsonl`, `l3_summary.json` |
| E6 | **L4** — 사용자 동참 필요(Chrome 확장으로 일본어 라이브 15분). 사용자가 없으면 `pending` 으로 표기하고 넘어간다 | `extension/` (unpacked) | 체크리스트 메모 |
| E7 | 비교·판정 — 기준선(P4) 대비 페어드 부트스트랩 | T4 (**신규**) | `compare.json`, 판정표(§8.6) 갱신 |
| E8 | 5-섹션 리포트 작성, 커밋·푸시 | — | `report/` |

실행 순서: **P4 → P3 → P2.5 → P2 → P1** (기준선이 있어야 δ 와 비교가 가능). P4 의 E2·E3 결과가 `baseline-2026-10`. 이 기준선은 **재측정** 값이며 과거 서베이 수치를 쓰지 않는다(§8.2). 번역 L1 에서 grammar 사용 여부(기본 GBNF, 서베이는 일부 `--no-grammar`)는 **전 프로파일이 같은 설정**이어야 하며 manifest 에 기록한다.

### 12.2 입력·고정 조건

- STT: `data/wav` 150쌍(5카테고리×30, 총 약 11.3분), 로더·정규화는 `research/topic/20260826_stt_model_survey_gpu_full/src/common.py` 의 `load_dataset`/`normalize_ja`. 번역: `data/eval_set_2026-08-18.jsonl` 120클립(`ja_ref`→번역 vs `ko_ref`, STT 오류 배제).
- 파이프라인 설정: 프로파일 yaml 만 다르고 그 외 `backend/config.py` 는 그대로. 한 비교 안에서 `PARTIAL_UPDATE_INTERVAL_S` 등을 임의로 바꾸지 않는다.
- 측정 중 **다른 GPU 작업 금지**, 전원 모드 고성능, 순차 실행. 반복 측정은 3회 중앙값(워밍업 1회 제외).
- EVAL.md §6 공정성: 각 번들은 문서화된 추론 경로·프로덕션 디코딩 파라미터로 돈다(예: whisper.cpp 는 `-bs 5` 서버 + 요청 필드). 후보가 나쁘게 나오면 결론 전에 셋업 편향부터 점검.

### 12.3 아직 없는 도구 (평가 전에 `profile/eval` 에 먼저 구현·커밋)

| ID | 파일 | 사양 |
|---|---|---|
| **T1** | `research/topic/20260827_vad_stt_survey/src/run_pipeline.py` 수정 | `--engine profile` 추가: `build_engine()` 이 `backend/main.py` startup 과 **같은 분기**(`config.STT_ENGINE` 로 qwen3-asr / faster-whisper / whisper-cpp)로 엔진을 만든다. `LT_PROFILE` 로 이미 적용된 config 를 그대로 사용. 출력 경로는 `out/profile-<P>/pipeline_transcripts.jsonl`. 기존 `--engine` 값은 그대로 동작해야 한다(회귀 금지). whisper-cpp 는 whisper-server 가 떠 있어야 한다 |
| **T2** | `scripts/hwprof/vram_ram_sampler.py` | 0.5초 간격으로 `nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits`(**GPU 전체 사용량** — Windows WDDM 에서는 프로세스별 사용량이 N/A 로 나오는 경우가 많아 기준 차감 방식 사용) 와 `psutil` 로 대상 프로세스 트리 RSS 를 기록. 인자: `--baseline-s 10`(시작 전 유휴 평균), `--pids a,b,c`, `--out`. 출력 JSON: `baseline_mib, peak_mib, peak_delta_mib, ram_peak_mib, samples`. `peak_delta_mib` 가 프로파일의 VRAM 예산(`vram_budget_gb`) 판정에 쓰인다. 서버 로그의 `llama_model_load`/버퍼 크기 줄을 교차 확인용으로 함께 저장 |
| **T3** | `scripts/hwprof/replay_latency.py` | 백엔드 WebSocket(`ws://127.0.0.1:8000/ws/audio`)에 **실제 시간 속도**로 오디오를 재생하는 클라이언트(`websockets`). ① 텍스트 `{"type":"start_session", ...}` 전송 ② 150클립을 `data/wav` 순서대로 16kHz mono PCM16 으로 바꿔 **0.3초(4800샘플=9600바이트) 바이너리 프레임**으로 벽시계 속도로 전송, 클립 사이에 무음 1.0초(VAD_SILENCE_MS=600 보다 길게) ③ 모든 수신 이벤트(`partial`/`final`, `segment_id`, `text`, `translation`)에 수신 시각 기록 ④ 마지막에 `{"type":"stop_session"}` 후 백엔드가 드레인할 때까지 수신(타임아웃 길게). 클립별 스트림 시각(시작/끝)을 기록해 `final` 을 클립에 매칭(`segment_id` 순서 + `normalize_ja` 편집거리로 `ja_ref` 와 정렬, 매칭 실패는 따로 집계). 출력: `l3_events.jsonl`(이벤트 원본+수신시각), `l3_summary.json`: `d_final = t_recv(final) − t_clip_end`(final 이벤트에 일본어와 한국어가 함께 실리므로 한 값) 의 p50/p95/max, 첫 partial 지연, **드리프트 기울기**(클립 종료 시각 대비 `d_final` 선형회귀 기울기, 초/분), 미수신 final 수(0이어야 함), 총 소요 시간. 클립 반복 없이 1회(≈11.3분+간격) |
| **T4** | `scripts/hwprof/compare_profiles.py` | 기준선(P4)과 후보의 `quant_per_segment.jsonl` 을 seg_id 로 정렬해 CER·chrF++ 등의 **페어드 부트스트랩(500회, 95% CI)** 차이를 계산, §8.3 L1 합격 기준(δ) 적용. δ 는 P4 재측정 CI 반폭을 보고 사람이 정한 값을 `--delta-cer`, `--delta-chrf` 로 받는다. 출력 `compare.json` |
| **T5(선택)** | `scripts/hwprof/make_manifest.py` | `manifest.json` 자동 생성(아래 스키마) |

### 12.4 `manifest.json` 스키마 (모든 결과 폴더 필수)

```json
{
  "profile": "p3", "profile_yaml_sha256": "...", "overlays": ["_measure"],
  "baseline_version": "baseline-2026-10", "harness_commit": "<git rev-parse HEAD of $EVAL>",
  "date": "2026-10-xx", "measured_on": "DEV(RTX 4080 SUPER, Windows 11)", "device": "CUDA0 ...",
  "python_env": "E-lite", "packages": {"ctranslate2": "...", "onnxruntime": "...", "faster-whisper": "..."},
  "binaries": {"llama-server": "version/build", "whisper-server": "version/build or null"},
  "models": {"stt": "name+quant+sha256", "translation": "gguf filename+sha256"},
  "decode": {"stt_beam_fast": 1, "stt_beam_final": 5, "llama_args": ["..."], "grammar": true},
  "dataset": {"stt": "data/wav 150", "mt": "eval_set_2026-08-18.jsonl sha256"},
  "unverified": ["Pascal sm_61 build", "..."]
}
```

### 12.5 결과 저장·보고·푸시

- 위치: `research/topic/<YYYYMMDD>_hw_profile_eval_<P>/` (5-섹션 리포트 형식, `README.md`+`report/`+`out/`+`src/` 규칙은 기존 토픽 따름). 스크립트는 기존 토픽 것을 **복제하지 말고** 경로로 호출. 동일 폴더를 덮어쓰지 말고 재실행은 날짜를 바꿔 새 폴더.
- 요약 허브: `docs/eval/HW_PROFILES_RESULTS.md` — §8.6 표(프로파일×L1~L4, `측정환경` 열: `DEV(4080 SUPER)`/`추정(미실측)`)를 세션이 끝날 때마다 갱신, 갱신일·기준선 버전 기재.
- 판정 규칙(§8.6): L1 불합격 또는 VRAM 예산 초과 = 탈락. 개발기에서도 드리프트 > 0 = "실시간 지속 불가". 개발기 통과는 "개발기 기준 통과 / 실기 미검증"으로만 표기. **지연 기준은 이 표를 보고 사용자가 정한다 — 세션이 임의로 합격선을 만들지 않는다.**
- 푸시: 결과·도구는 `profile/eval` 에 커밋·푸시(`git -C $EVAL push -u origin profile/eval`). 프로파일 번들 yaml 을 평가 결과로 수정할 때는 해당 `profile/p*` 브랜치에 먼저 커밋하고 `profile/eval` 로 병합. `main` 에는 병합하지 않는다.
- 세션 인계: 끝낼 때 `docs/planning/HARDWARE_PROFILES.md` §9.4 의 체크리스트를 갱신하고, 다음 세션이 이어갈 지점(어느 프로파일의 어느 단계까지 했는지)을 `docs/log/HANDOFF.md` 에 남긴다.

### 12.6 알려진 함정

- 한국어 Windows 콘솔(cp949): 일본어 출력이 깨지거나 pip 가 UTF-8 파일을 못 읽는다 → `PYTHONUTF8=1`, 요구사항 파일은 ASCII.
- `*.log` 는 `.gitignore` 대상(서버 로그는 커밋되지 않음). 필요한 로그 발췌는 리포트에 인용.
- HF 캐시 symlink 경고는 무시 가능. HF 비인증 경고도 동일.
- Qwen3-ASR(P4)은 확률 기반 환각 필터가 inert, faster-whisper(P3·P2.5)는 활성 — 환각 건수를 L1 에서 별도로 비교(§9.3-7).
- whisper-server 요청별 `beam_size` 지원·`verbose_json` 신뢰도 필드 지원은 미확인(§9.3-1,2). P2·P1 첫 기동 시 가짜 서버가 아닌 실제 서버로 `scripts/verify_whisper_cpp_engine.py` 와 같은 항목을 수동 확인하고 결과를 리포트에 적는다.

## 출처

1. https://computingforgeeks.com/how-much-vram-to-run-llm/
2. https://huggingface.co/42ailab/TranslateGemma-12B-GGUF
3. https://gigazine.net/gsc_news/en/20260116-google-translategemma
4. https://arxiv.org/pdf/2602.11961
5. https://models.handy.computer/languages/ja
6. https://models.handy.computer/models/whisper-large-v3-turbo
7. https://dev.classmethod.jp/articles/openai-whisper-large-v3-turbo-faster-whisper/
8. https://www.phoronix.com/news/Whisper-cpp-1.8.3-12x-Perf
9. https://sysrqmts.com/en/gpus/nvidia/geforce-10/gtx-1050-ti
10. https://dev-discuss.pytorch.org/t/notice-cuda-12-6-wheels-will-no-longer-be-published-from-pytorch-2-15-drops-maxwell-pascal-volta/3432
11. https://www.tweaktown.com/news/87212/nvidia-geforce-rtx-4080-rumored-specs-same-16gb-gddr6x-now-at-21gbps/
12. https://www.amd.com/en/blogs/2024/accelerating-llama-cpp-performance-in-consumer-llm.html
13. https://www.thefpsreview.com/2026/08/10/the-july-steam-survey-is-in-16gb-gpus-have-officially-overtaken-8gb-for-the-first-time/
