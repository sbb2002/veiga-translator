# HF ASR 후보 모델 서베이 — GPU 전체 150쌍 재측정

## 배경

`20260826_stt_model_survey`(CPU 파일럿, 25쌍)에서 현재 채택 모델(large-v3-turbo)의
대안 후보 3개(granite-speech-4.1-2b, Qwen3-ASR-0.6B/1.7B-hf)가 CPU 환경에서
turbo와 동급 품질에 Qwen3-ASR-0.6B-hf가 RTF 3.5배 빠르다는 결과를 얻었지만, 표본이
25개뿐이라 품질 순위는 통계적으로 구분되지 않았다. 개발 환경에 **GPU(RTX 4080
SUPER, CUDA)가 사용 가능**해짐에 따라(2026-08-26), 사용자 요청으로 같은 4개 방법을
**GPU + 전체 150쌍**(`data/` 5카테고리 x 30개)으로 재측정한다.

CPU 파일럿과 별개 토픽으로 분리한 이유: 디바이스(CPU→GPU)와 표본 크기(25→150)가
모두 달라져 RTF/CER 수치가 직접 비교 불가능하기 때문 — `20260826_stt_model_survey`의
방법론(같은 서브셋 편향 우려)은 여기서 전체 150쌍을 씀으로써 해소된다.

## 방법

- `20260826_stt_model_survey/src/`를 베이스로 복제 후 GPU용으로 수정:
  - `common.py`: `pilot_n_per_category` 옵션 제거, 항상 전체 150쌍 로드.
  - `transcribe_turbo.py`: `device="cuda"`, `compute_type="int8_float16"`
    (`backend/stt/`의 실제 프로덕션 설정과 동일).
  - `transcribe_granite.py`, `transcribe_qwen3_asr.py`: `device="cuda"`,
    `torch_dtype=float16`. Qwen3-ASR은 `processor.apply_transcription_request()`
    결과가 float32로 나와 모델(`float16`)과 dtype이 안 맞아 `RuntimeError`가
    났던 것을 입력 텐서를 모델 dtype으로 캐스팅해 수정.
  - `score_quantitative.py`, `analyze_ci_and_plot.py`: 카테고리별 n을 5→30으로
    반영한 것 외 로직 동일(페어드 부트스트랩 95% CI, 500회 재표본).
- 4개 방법 모두 같은 150개 세그먼트(같은 seg_id 순서)로 측정 — 페어드 비교 성립.
- RTF는 순차 측정(동시 실행 없음, CPU 파일럿과 동일 원칙).
- 정성(LLM judge) 패스는 이번에도 생략 — llama-server(:8080) 미기동. 필요하면
  `judge_qualitative.py --method <name>`으로 추가 가능(사용자 확인 후).

### 겪은 이슈

- `Qwen3-ASR-*-hf`의 `apply_transcription_request()` 출력을 `.to(DEVICE)`만
  호출하면 float32로 남아 `model.generate()`가
  `RuntimeError: Input type (float) and bias type (struct c10::Half) should be the same`
  로 실패 — 부동소수 텐서만 골라 `.to(DEVICE, dtype=DTYPE)`로 명시 캐스팅해 해결.
- 실행 환경은 `conda env live-translator`(CUDA 지원 torch/faster-whisper 설치됨,
  base miniconda 환경에는 없음) — `soundfile`, `accelerate`, `matplotlib`,
  `librosa`가 빠져 있어 추가 설치.

## 결과 (150세그먼트, GPU, 95% CI = 페어드 부트스트랩 500회)

자세한 내용과 해석은 [`report/01-full-results.md`](report/01-full-results.md) 참고.

핵심 요약: **RTF는 GPU에서 4개 방법 전부 1.0을 훨씬 밑돌아(0.03~0.15) 실시간
처리에 문제없고, large-v3-turbo가 그중에서도 가장 빠르다** — CPU 파일럿에서
Qwen3-ASR-0.6B-hf가 보였던 속도 우위가 GPU에서는 사라지고 순위가 뒤집힌다.
품질(CER/chrF++/BLEU/ROUGE-L)은 4개 방법 CI가 대부분 겹쳐 150쌍으로도 통계적
우열은 뚜렷하지 않지만, granite-speech-4.1-2b는 반복(루프) 환각 세그먼트 하나 때문에
카테고리별 CER 분산이 유독 크다.

## 산출물

- `report/01-full-results.md` — 정량 결과, 카테고리별 CER, 해석, 결론.
- `src/common.py` — 데이터셋 로더(전체 150쌍 고정) + 정규화.
- `src/transcribe_turbo.py`, `src/transcribe_granite.py`,
  `src/transcribe_qwen3_asr.py` — GPU 전사 스크립트.
- `src/score_quantitative.py`, `src/judge_qualitative.py` — 채점 스크립트.
- `src/analyze_ci_and_plot.py` — 페어드 부트스트랩 95% CI + `fig/*.png` 생성.
- `out/turbo/`, `out/granite-speech-4.1-2b/`, `out/qwen3-asr-0.6b/`,
  `out/qwen3-asr-1.7b/` — 방법별 전사·채점 결과. `out/ci_summary.json` — CI.
- `fig/quant_metrics.png`, `fig/rtf.png`, `fig/category_cer.png`.

## 레퍼런스

- CPU 파일럿(25쌍): `research/topic/20260826_stt_model_survey/`.
- 원래 STT 벤치마크(large-v3 vs turbo vs kotoba-whisper, GPU/150쌍):
  `research/topic/20260822_stt_transcription_eval/`.
