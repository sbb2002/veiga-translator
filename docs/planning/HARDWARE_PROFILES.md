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
