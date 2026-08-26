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

**5번째 후보 추가(같은 날, 처음 4개 결과가 나온 뒤)**: 사용자가 "우리 150쌍 데이터는
극히 일부라 [4개 방법의 순위를] 맹신할 수 없다"고 지적하며, 웹에서 찾은
`reazon-research/reazonspeech-nemo-v2`(Fast Conformer + RNN-T, 일본어 TV 방송
오디오로 직접 학습 — 도메인이 가장 가까워 보이는 후보)를 추가로 요청했다. 결과는
`report/01-full-results.md` 참고 — 사전 기대와 달리 이번 데이터에서는 5개 카테고리
전부에서 turbo보다 유의미하게 나빴다.

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
- `transcribe_reazonspeech.py`: `nemo_asr.models.ASRModel.from_pretrained(...)`,
  `.cuda()`. 코퍼스 wav가 스테레오 44.1kHz인데 모델은 모노 16kHz `(batch, time)`를
  기대해 매 세그먼트를 스크래치 파일로 다운믹스+리샘플 후 전달(`transcribe_granite.py`와
  동일한 방식).
- 5개 방법 모두 같은 150개 세그먼트(같은 seg_id 순서)로 측정 — 페어드 비교 성립.
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
- **`nemo_toolkit`을 `live-translator` 환경에 설치했다가 CUDA가 깨짐** —
  `pip install "nemo_toolkit[asr]"`이 의존성 해석 과정에서 `torch`를 CPU 전용
  2.13.0으로 강제 업그레이드(`torchaudio`도 깨짐, `torch.cuda.is_available()`이
  `False`가 됨). `torch==2.5.1+cu121`/`torchaudio==2.5.1+cu121`로 재설치 +
  `nemo-toolkit` 제거로 복구했다. 이후 **`reazonspeech`라는 별도 conda
  env**(`python 3.10`, `torch==2.6.0+cu124`, `nemo_toolkit[asr]`)를 새로 만들어
  완전히 격리 — `transcribe_reazonspeech.py`만 이 env로 실행한다. 재현 시 절대
  `nemo_toolkit`을 `live-translator` env에 설치하지 말 것.
- NeMo의 `model.transcribe()`가 내부적으로 Windows `tempfile.TemporaryDirectory`를
  쓰는데, 입력 오디오 shape가 안 맞아 예외가 나면 그 cleanup 단계에서
  `NotADirectoryError`/`PermissionError`로 원래 에러가 가려짐 — 오디오를
  모노 16kHz로 먼저 변환하니 해결됐다(원래 에러: "Input shape mismatch...
  Input shape found : torch.Size([1, 2, 112000])" — 스테레오를 그대로 넣어서
  발생).

## 결과 (150세그먼트, GPU, 95% CI = 페어드 부트스트랩 500회)

자세한 내용과 해석은 [`report/01-full-results.md`](report/01-full-results.md) 참고.

핵심 요약: **RTF는 GPU에서 5개 방법 전부 1.0을 훨씬 밑돌아(0.03~0.15) 실시간
처리에 문제없고, large-v3-turbo가 그중에서도 가장 빠르다** — CPU 파일럿에서
Qwen3-ASR-0.6B-hf가 보였던 속도 우위가 GPU에서는 사라지고 순위가 뒤집힌다.
품질(CER/chrF++/BLEU/ROUGE-L)은 turbo/Qwen3-ASR/granite 4개는 CI가 대부분 겹쳐
150쌍으로도 통계적 우열이 뚜렷하지 않지만, **5번째로 추가한 ReazonSpeech-NeMo-v2는
CI가 겹치지 않는 유일한 후보 — turbo보다 통계적으로 유의미하게 나쁘다**(5개
카테고리 전부에서 밀림). "일본어 방송 학습이라 도메인이 가장 가깝다"는 사전
기대와 반대되는 결과. granite-speech-4.1-2b는 반복(루프) 환각 세그먼트 하나 때문에
카테고리별 CER 분산이 유독 크다.

## 산출물

- `report/01-full-results.md` — 정량 결과, 카테고리별 CER, 해석, 결론.
- `src/common.py` — 데이터셋 로더(전체 150쌍 고정) + 정규화.
- `src/transcribe_turbo.py`, `src/transcribe_granite.py`,
  `src/transcribe_qwen3_asr.py`, `src/transcribe_reazonspeech.py` — GPU 전사
  스크립트(`transcribe_reazonspeech.py`는 `reazonspeech` conda env 전용).
- `src/score_quantitative.py`, `src/judge_qualitative.py` — 채점 스크립트.
- `src/analyze_ci_and_plot.py` — 페어드 부트스트랩 95% CI + `fig/*.png` 생성.
- `out/turbo/`, `out/granite-speech-4.1-2b/`, `out/qwen3-asr-0.6b/`,
  `out/qwen3-asr-1.7b/`, `out/reazonspeech-nemo-v2/` — 방법별 전사·채점 결과.
  `out/ci_summary.json` — CI.
- `fig/quant_metrics.png`, `fig/rtf.png` — 전체(150쌍) 지표/RTF 비교.
  `fig/category_cer.png`, `fig/category_chrf.png`, `fig/category_bleu.png`,
  `fig/category_rouge_l.png` — 카테고리별(n=30) 지표 4종 각각.

## 레퍼런스

- CPU 파일럿(25쌍): `research/topic/20260826_stt_model_survey/`.
- 원래 STT 벤치마크(large-v3 vs turbo vs kotoba-whisper, GPU/150쌍):
  `research/topic/20260822_stt_transcription_eval/`.
