# 02. CPU 파일럿 결과 — large-v3-turbo vs granite-speech-4.1-2b vs Qwen3-ASR-0.6B/1.7B-hf

## 배경

`report/01-model-scoping.md`에서 9개 후보 중 일본어를 지원하고 GPU 전용 커널 의존이
없는 3개(`ibm-granite/granite-speech-4.1-2b`, `Qwen/Qwen3-ASR-0.6B-hf`,
`Qwen/Qwen3-ASR-1.7B-hf`)만 CPU 파일럿 대상으로 남았다. 사용자 지시(2026-08-26)에
따라 전체 150쌍이 아닌 **5개 카테고리 x 5개 = 25쌍 소규모 서브셋**(정렬 순서 기준
카테고리당 앞 5개, `common.load_dataset(pilot_n_per_category=5)`)으로 먼저 동작
여부와 대략적인 RTF/품질을 확인했다. 기준선(large-v3-turbo)도 `20260822` 토픽의
GPU/150쌍 결과를 그대로 쓰지 않고, **같은 CPU 환경·같은 25쌍 서브셋으로 재측정**했다
— GPU/CPU, 25쌍/150쌍 조건이 다르면 RTF와 CER 모두 직접 비교가 무의미해지기 때문.

## 방법

- 4개 방법 모두 `src/common.py`의 동일 데이터셋 로더 + `normalize_ja` 정규화, 동일
  `score_quantitative.py`(CER/chrF++/BLEU-char/ROUGE-L, `--method` 인자로 out/
  서브디렉터리 선택)로 채점.
- `transcribe_turbo.py` — faster-whisper large-v3-turbo, CPU, `compute_type="int8"`
  (CPU엔 GPU 전용 `int8_float16` 대신 `int8` 사용), beam=5.
- `transcribe_granite.py` — `AutoModelForSpeechSeq2Seq` + 오디오 chat template
  (모델카드 권장: "non-English ASR는 영어 프롬프트 사용"), `dtype=float32`.
- `transcribe_qwen3_asr.py --size 0.6b|1.7b` — `AutoModelForMultimodalLM` +
  `processor.apply_transcription_request(audio=..., language="Japanese")`.
- **RTF는 세그먼트를 순차로(동시 실행 없이) 측정** — 파일럿 초반 granite와
  qwen-0.6b를 병렬로 돌리려다 CPU 경합으로 RTF가 오염될 것을 발견해 중단하고
  재측정했다(같은 실수를 반복하지 않도록 기록).
- 정성(LLM 채점 보조) 패스는 이번 라운드에서 생략 — llama-server(:8080)가 현재
  떠 있지 않음. 필요하면 `judge_qualitative.py --method <name>`으로 이어서 실행
  가능(사용자 확인 후).
- **오차범위(95% CI)**: `20260822_stt_transcription_eval`과 동일한 방법론 —
  페어드 부트스트랩(25개 세그먼트를 복원추출로 재표본, 500회 반복), 매 재표본마다
  세그먼트를 다시 합쳐 코퍼스 단위 지표(CER/chrF++/BLEU/ROUGE-L/RTF)를 처음부터
  재계산(세그먼트별 값의 단순 평균이 아님). `src/analyze_ci_and_plot.py`가 4개
  방법 전체·카테고리별(n=5) CI를 계산해 `out/ci_summary.json`에 저장하고,
  `fig/quant_metrics.png`·`fig/rtf.png`·`fig/category_cer.png`(오차막대 포함
  막대그래프)를 생성한다. 4개 방법 모두 같은 25개 세그먼트(같은 seg_id)를 쓰므로
  페어드 비교가 성립한다.

### 겪은 이슈 (재현성을 위해 기록)

1. `torchaudio.load()`가 이 환경엔 `torchcodec`이 없어 실패 — `soundfile.read()` +
   수동 모노 변환/리샘플로 교체(`transcribe_granite.py`).
2. Windows 콘솔 기본 인코딩(cp949)이 일본어 print에서 크래시 — 모든 스크립트
   상단에 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` 추가.
3. `Qwen3-ASR-*-hf`는 `transformers==5.12.1`에서 `qwen3_asr` 아키텍처를 인식 못함
   → `pip install -U "transformers>=5.13.0"`(5.15.1로 업그레이드) 후 해결.
4. `AutoModelForSpeechSeq2Seq.from_pretrained(..., device_map=...)`에 `accelerate`
   필요 → 설치.
5. `Qwen3-ASR-1.7B-hf`를 백그라운드 bash로 실행하면 가중치 로딩 85% 지점에서
   매번 원인불명으로 죽음(재현 2회) — **포그라운드 실행에서는 문제없이 완료**.
   메모리는 충분(가용 10.9GB, 필요분 ~7GB)해서 OOM보다는 백그라운드 프로세스
   실행 환경의 리소스/타임아웃 제약으로 추정. 이후 무거운 모델은 포그라운드로
   돌림.

## 결과 (25세그먼트, CPU, 95% CI = 페어드 부트스트랩 500회)

| 방법 | CER↓ (95% CI) | chrF++↑ (95% CI) | BLEU(char)↑ (95% CI) | ROUGE-L F1↑ (95% CI) | 총 소요 | RTF↓ (95% CI) |
|---|---|---|---|---|---|---|
| large-v3-turbo (baseline) | 0.423 ± 0.212 | 51.68 ± 12.69 | 62.06 ± 14.73 | 0.648 ± 0.132 | 348.3s | 2.468 ± 0.534 |
| granite-speech-4.1-2b | 0.368 ± 0.145 | 55.82 ± 14.12 | 63.72 ± 15.78 | 0.634 ± 0.147 | 372.4s | 2.640 ± 0.590 |
| **Qwen3-ASR-0.6B-hf** | 0.393 ± 0.149 | 51.23 ± 12.91 | 59.88 ± 16.49 | 0.605 ± 0.132 | **98.7s** | **0.699 ± 0.137** |
| Qwen3-ASR-1.7B-hf | 0.359 ± 0.147 | 55.52 ± 13.53 | 64.15 ± 16.45 | 0.644 ± 0.136 | 519.1s | 3.679 ± 1.841 |

CI가 전부 넓어(예: CER ± 0.15~0.21) **4개 방법의 CER/chrF++/BLEU/ROUGE-L 점추정치는
서로 통계적으로 구분되지 않는다** — 25 표본으로는 품질 순위를 확정할 수 없다는
뜻. 반면 **RTF는 Qwen3-ASR-0.6B-hf(0.699 ± 0.137)만 CI가 좁고 다른 세 방법(turbo
2.47±0.53, granite 2.64±0.59, qwen-1.7b 3.68±1.84)과 CI가 전혀 겹치지 않는다** —
CPU 속도 우위만큼은 이 표본에서도 통계적으로 뚜렷하다.

### 카테고리별 CER (95% CI, n=5)

| 카테고리 | turbo | granite | qwen-0.6b | qwen-1.7b |
|---|---|---|---|---|
| 게임 | 0.973 ± 0.032 | 0.991 ± 0.013 | 0.982 ± 0.022 | 0.973 ± 0.023 |
| 여행 | 0.315 ± 0.193 | 0.435 ± 0.218 | 0.444 ± 0.260 | 0.417 ± 0.274 |
| 음식,요리 | 0.898 ± 0.844 | 0.459 ± 0.341 | 0.561 ± 0.255 | 0.459 ± 0.295 |
| 일상,소통 | 0.125 ± 0.112 | 0.060 ± 0.086 | 0.060 ± 0.060 | 0.028 ± 0.038 |
| 패션,뷰티 | 0.201 ± 0.079 | 0.237 ± 0.091 | 0.288 ± 0.077 | 0.273 ± 0.074 |

음식,요리의 turbo CI(± 0.844)가 특히 넓다 — n=5 중 일부만 아웃트로 환각으로
튀어(`out/turbo/quant_results.csv` 참고) 재표본마다 값이 크게 흔들린다는 뜻으로,
바로 아래 문단의 서브셋 편향 지적과 같은 맥락이다.

**게임 카테고리는 4개 모델 전부 CER 0.97+로 사실상 전멸** — `20260822` 토픽의
GPU/150쌍 결과(turbo 게임 CER 0.588)와 크게 다르다. 이 파일럿은 카테고리당
정렬 순서 기준 앞 5개만 뽑았는데, 게임 카테고리의 앞 5개(`7112_25921_*` 클립 5개
전부)가 유독 어려운(배경음 심한) 구간으로 우연히 몰린 것으로 보인다 — **파일럿
서브셋 선택 편향**이지 모델 성능 차이가 아니다. 흥미로운 점은 granite와
qwen-0.6b가 이 구간에서 전부 짧은 **환각**(각각 "thank you" 5회 연속, "うん。"
5회 연속)을 뱉어 사실상 무음/무의미로 처리했다는 것 — turbo와 qwen-1.7b는
그나마 뭔가 텍스트를 시도한다(CER은 비슷하게 나쁘지만 완전한 정지 패턴은 아님).
음식,요리 카테고리에서도 turbo만 유독 나쁜데(0.898 vs 나머지 0.46 안팎) 이 역시
같은 서브셋 편향(turbo가 이 특정 클립들에서 아웃트로 상투구 환각을 냈을 가능성)
일 수 있어 원인은 세그먼트별 로그(`out/*/quant_results.csv`)로 스팟체크가 필요.

## 결론 (파일럿 단계 — 표본 25개, 통계적으로 작음)

- **품질**: 4개 모델 다 비슷한 구간(CER 0.36~0.42)에 몰려 있고, 게임/음식,요리
  카테고리의 서브셋 편향이 순위를 흔들 수 있고, **95% CI가 넓어 4개 방법의
  점추정치가 통계적으로 구분되지 않는다**(CI 전부 겹침) — 이 표본만으로 품질
  순위를 확정할 수 없다. 다만 qwen-1.7b와 granite가 turbo보다 근소하게 나은
  경향은 일관된다.
- **속도(RTF, CPU)**: **Qwen3-ASR-0.6B-hf가 독보적**이다 — turbo 대비 3.5배,
  granite/qwen-1.7b 대비 4~5배 빠르면서 품질은 turbo와 동급(CER 0.393 vs
  0.423, CI 겹침). 품질과 달리 **RTF는 CI가 다른 세 방법과 겹치지 않아
  통계적으로도 뚜렷한 차이**다. CPU 전용 환경에서 가장 실용적인 후보로 보인다.
- granite-speech-4.1-2b, Qwen3-ASR-1.7B-hf는 CPU에서 turbo보다 느리거나
  비슷해(RTF 2.6~3.7) 이 환경에서는 속도 이점이 없다 — GPU가 생기면 재평가
  가치가 있다.

## 그림

`fig/`(막대그래프, 오차막대=95% CI):
- `quant_metrics.png` — 정량 지표 4종(CER/chrF++/BLEU/ROUGE-L) 4개 방법 비교.
- `rtf.png` — RTF(CPU 처리 속도) — Qwen3-ASR-0.6B-hf만 CI가 겹치지 않게 낮음.
- `category_cer.png` — 카테고리별 CER, n=5라 CI가 넓다(특히 음식,요리의 turbo).

## 다음 단계 (사용자 결정 필요)

1. **게임/음식,요리 카테고리 표본을 무작위로 다시 뽑아** 이번 파일럿의 편향이
   진짜인지 확인 — 지금은 "정렬 순서 앞 5개"라 특정 파일에 쏠려 있다.
2. 유의미해 보이면(특히 Qwen3-ASR-0.6B-hf) **150쌍 전체로 확장**해 통계적으로
   신뢰할 수 있는 CER/chrF++/BLEU/ROUGE-L + 부트스트랩 95% CI 산출.
3. `judge_qualitative.py`로 정성 채점(llama-server 필요)까지 돌려 `20260822`
   토픽과 같은 형식의 3-way(일치/부분일치/불일치) 비교 추가.
4. 게임 카테고리의 짧은-환각 패턴("thank you"/"うん。" 반복)이 특정 모델의
   구조적 약점인지, 이 데이터의 특정 클립 문제인지 별도로 원인 분석.

## 레퍼런스

- 원본 산출물: `out/turbo/`, `out/granite-speech-4.1-2b/`, `out/qwen3-asr-0.6b/`,
  `out/qwen3-asr-1.7b/` — 각각 `transcripts.jsonl`, `quant_results.csv`,
  `quant_summary.json`. `out/ci_summary.json` — 전체/카테고리별 95% CI.
- 실행 스크립트: `src/common.py`, `src/transcribe_turbo.py`,
  `src/transcribe_granite.py`, `src/transcribe_qwen3_asr.py`,
  `src/score_quantitative.py`, `src/judge_qualitative.py`,
  `src/analyze_ci_and_plot.py`(CI 계산 + `fig/` 그림 생성).
- 스코핑 근거: `report/01-model-scoping.md`.
