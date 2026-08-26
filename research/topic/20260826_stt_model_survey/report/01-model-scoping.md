# 01. HF ASR 후보 9종 스코핑 — CPU 실행 가능성 + 일본어 지원 조사

## 배경

`docs/eval/EVAL.md`/`20260822_stt_transcription_eval`에서 large-v3-turbo가 현재
채택 모델로 확정됐지만, 이후 HuggingFace에 새로 올라온 오픈소스 ASR 모델 9종이
대안이 될 수 있는지 사용자가 조사를 요청했다. 현재 개발 환경은 **CPU만 사용
가능**(GPU 없음)이라, 실제 비교 실험에 들어가기 전에 각 모델이 CPU에서 현실적인
시간 내에 돌아가는지, 그리고 이 프로젝트의 필수 조건인 **일본어 전사를 지원하는지**
부터 걸러야 한다.

## 조사 대상

1. `ibm-granite/granite-speech-4.1-2b`
2. `ibm-granite/granite-speech-5.0-470m-turboctc-nc`
3. `nvidia/canary-qwen-2.5b`
4. `nvidia/canary-1b-flash`
5. `nvidia/canary-1b`
6. `nvidia/parakeet-tdt-0.6b-v2`
7. `nvidia/parakeet-tdt-0.6b-v3`
8. `Qwen/Qwen3-ASR-0.6B-hf`
9. `Qwen/Qwen3-ASR-1.7B-hf`

## 결과

| # | 모델 | 파라미터 | 아키텍처 | 필요 패키지 | 일본어 | CPU 판정 |
|---|---|---|---|---|---|---|
| 1 | granite-speech-4.1-2b | 2B | Conformer enc + Granite 4.0 1B LLM dec | `transformers>=4.52.1` | ✅ | CPU 가능(느릴 수 있음) |
| 2 | granite-speech-5.0-470m-turboctc-nc | 0.5B | Conformer + CTC | `transformers` | ❌ 영어만 | 일본어 미지원 → 제외 |
| 3 | canary-qwen-2.5b | 2.5B | FastConformer enc + Transformer LLM dec | `nemo_toolkit>=2.5.0` | ❌ 영어 전용 | GPU 전용 명시 + 일본어 미지원 → 제외 |
| 4 | canary-1b-flash | 0.88B | FastConformer enc + 경량 dec | `nemo_toolkit` | ❌ en/de/fr/es | GPU 전용 명시 + 일본어 미지원 → 제외 |
| 5 | canary-1b | 1B | FastConformer enc + Transformer dec | `nemo_toolkit` | ❌ en/de/fr/es | GPU 전용 명시 + 일본어 미지원 → 제외 |
| 6 | parakeet-tdt-0.6b-v2 | 0.6B | FastConformer-TDT | `nemo_toolkit["asr"]` | ❌ 영어 전용 | GPU 전용 명시 + 일본어 미지원 → 제외 |
| 7 | parakeet-tdt-0.6b-v3 | 0.6B | FastConformer-TDT | `nemo_toolkit["asr"]` | ❌ 유럽 25개 언어(일본어 없음) | GPU 전용 명시 + 일본어 미지원 → 제외 |
| 8 | Qwen3-ASR-0.6B-hf | 0.8B | 멀티모달 트랜스포머(음성 enc + LM dec) | `transformers>=5.13.0` | ✅ | CPU 가능 |
| 9 | Qwen3-ASR-1.7B-hf | 2B | Qwen3-Omni 계열, 위와 동일 구조 | `transformers>=5.13.0` | ✅ | CPU 가능(더 느림) |

## 판단 근거 요약

- **canary 3종 + parakeet 2종(#3~#7)**: 전부 NVIDIA NeMo 툴킷 기반이고, 모델카드가
  "designed for NVIDIA GPU-accelerated systems"를 명시한다. 게다가 다섯 개 전부
  일본어를 지원하지 않는다(영어 전용이거나 유럽어권 한정). GPU 유무와 무관하게
  이 프로젝트(일본어 방송 전사) 목적에는 애초 쓸 수 없다.
- **granite-speech-5.0-470m(#2)**: CTC 기반이라 CPU에 가장 적합한 구조지만 영어
  전용(라이선스도 CC-BY-NC-SA 비상업)이라 제외.
- **granite-speech-4.1-2b(#1), Qwen3-ASR-0.6B/1.7B-hf(#8, #9)**: 셋 다 GPU 전용
  커널(NeMo, flash-attention 필수 등) 의존이 없고 순수 `transformers`로 로드
  가능하며, 일본어를 지원한다. 2B급 LLM 디코더라 large-v3-turbo(CTranslate2,
  int8) 대비 CPU에서 훨씬 느릴 것으로 예상되지만 기술적으로는 실행 가능.

## 결정 (사용자 지시, 2026-08-26)

- 일본어를 지원하지 않는 6개 모델(#2~#7)은 GPU 필요 여부와 무관하게 **전부
  실험 대상에서 제외**한다 — 이 저장소의 목표(§1차 목표, 일본어 실시간 번역)와
  무관하기 때문. 이 6개에 대한 개별 GPU 실험계획서는 작성하지 않는다(애초에
  일본어 전사가 불가능하므로 GPU가 있어도 의미가 없음).
- 향후 **GPU 사용 가능 환경으로 전환된다면**, 이 6개 중 최소 요건(NeMo 설치
  가능, 언어 지원)을 만족하는 모델은 없으므로 재검토 자체가 불필요하다. 다만
  참고용으로 표는 남겨둔다.
- 나머지 3개(#1, #8, #9)는 **CPU 파일럿 실험**으로 진행한다 — `data/wav`+
  `data/json`의 5개 카테고리 x 각 5개 = 25쌍 소규모 서브셋으로 먼저 RTF/동작
  여부를 확인한 뒤, 결과가 유의미하면 전체 150쌍으로 확장할지 판단한다(사용자
  결정 필요). 비교 기준선(baseline)은 `20260822_stt_transcription_eval`의
  large-v3-turbo를 동일 서브셋으로 다시 돌려 RTF를 맞춘다(전체 150쌍 RTF와
  서브셋 RTF는 캐시/워밍업 효과로 다를 수 있어 baseline도 동일 조건 재측정).

## 다음 단계

`report/02-pilot-results.md` — granite-speech-4.1-2b / Qwen3-ASR-0.6B-hf /
Qwen3-ASR-1.7B-hf / large-v3-turbo(baseline) 25쌍 파일럿 정량+RTF 비교.
