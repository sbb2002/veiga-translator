# 번역 모델 벤치마크 계획 — GPU(RTX 5090) 머신에서 이어서 실행

이 CPU-only 환경에서는 GPU가 없어 여기서 만든 3가지 모델 후보를 실제로 못 돌려봤다. GPU
머신(RTX 5090, 32GB VRAM)에서 아래 순서대로 이어서 진행하면 된다. 배경/근거는
`docs/EVAL_REPORT_2026-08-18.md` §5 "C. 번역 엔진 자체 오역" 참고 — 현재 모델
(Qwen2.5-7B-Instruct Q4_K_M)이 STT 오류를 완전히 제거해도(정답 전사 직접 입력) chrF++
24.25에 그쳐, 모델 자체 교체가 다른 개선(A/B/D/E)보다 선행돼야 한다는 결론.

## 비교 대상

| 모델 | Q4_K_M 크기 | 선정 이유 |
|---|---|---|
| Qwen2.5-7B-Instruct (현재, 베이스라인) | 4.5GB | 지금 쓰는 모델 — 대조군 |
| **Qwen3-32B** | 19.8GB | 70B 이하 로컬 모델 중 최강 번역 성능으로 평가됨(중국어↔영어 GPT-4o 능가, 유럽어 Claude 3.7 Sonnet급), 한/일 포함 100개+ 언어 강함 |
| **EXAONE-4.0-32B** (LG AI Research) | 19.3GB | 한국어 네이티브 특화 — 이번 평가에서 가장 약했던 자연스러움(2.85)·존댓말 일치(2.74) 개선 기대. 일본어 입력 처리력은 미검증이라 벤치마크 필요 |
| Gemma-3-27B-it | 16.5GB | 140개+ 언어 지원 범용 대안 |

셋 다 RTX 5090(32GB)에 `-ngl 999`(전체 GPU 오프로드)로 여유 있게 들어가고 4096~8192
컨텍스트도 확보 가능.

## 사전 준비 (GPU 머신)

1. 이 저장소를 GPU 머신에 pull. `data/`(eval_set jsonl + wav 120클립), `scripts/`,
   `docs/EVAL_REPORT_2026-08-18.md`, `data/eval_set_2026-08-18_results.jsonl`(현재 모델
   STT/번역 결과, 대조군으로 재사용 가능)이 그대로 있어야 함.
2. `pip install -r backend/requirements.txt jiwer sacrebleu` (+ CUDA용 torch/torchaudio —
   README 참고).
3. **llama-server를 CUDA 빌드로 받을 것** — 이 환경에서 받은 `llama-server/`는 CPU 빌드라
   GPU 머신에서는 못 씀. `https://github.com/ggml-org/llama.cpp/releases` 최신 릴리스의
   `llama-*-bin-win-cuda-12.x-x64.zip`(또는 실제 CUDA 버전에 맞는 것) 받아서 교체.
4. 모델 GGUF 4개(위 표)를 `backend/models/`에 받기:
   - `https://huggingface.co/bartowski/Qwen2.5-7B-Instruct-GGUF` (이미 있음, 대조군)
   - `https://huggingface.co/Qwen/Qwen3-32B-GGUF/resolve/main/Qwen3-32B-Q4_K_M.gguf`
   - `https://huggingface.co/LGAI-EXAONE/EXAONE-4.0-32B-GGUF/resolve/main/EXAONE-4.0-32B-Q4_K_M.gguf`
   - `https://huggingface.co/bartowski/google_gemma-3-27b-it-GGUF/resolve/main/google_gemma-3-27b-it-Q4_K_M.gguf`

## 실행 순서 (모델 하나당 반복)

```bash
# 1) 해당 모델로 llama-server 기동 (GPU 오프로드, config.py의 CPU 타임아웃 오버라이드 불필요 — GPU라 빠름)
llama-server/llama-server.exe -m backend/models/<모델파일>.gguf --port 8080 -ngl 999 -c 4096

# 2) STT는 이제 GPU 있으니 config.py 기본값(cuda, int8_float16) 그대로 사용 —
#    scripts/run_eval.py의 CPU 오버라이드(device="cpu", compute_type="int8")를 지우거나
#    device="cuda", compute_type="int8_float16"으로 바꿔서 실행
python scripts/run_eval.py data/eval_set_2026-08-18.jsonl
#    -> data/eval_set_2026-08-18_results.jsonl (모델별로 파일명 구분해서 백업해둘 것,
#       예: results_qwen3-32b.jsonl 로 rename)

# 3) STT 영향 배제한 순수 번역 품질 비교 (핵심 지표 — 이게 §5-C의 24.25 대비 비교 기준)
python scripts/eval_stt_propagation.py data/eval_set_2026-08-18_results.jsonl
#    -> chrF++(정답 전사 직접 번역) 수치가 핵심 비교 대상
```

STT는 세 모델 다 동일 조건이라 한 번만 돌리고(현재 `data/eval_set_2026-08-18_results.jsonl`의
`hyp_ja` 재사용 가능), 모델 교체마다 재번역만 반복해도 됨 — `scripts/run_eval.py`를 매번
STT까지 다시 돌릴 필요는 없다. 각 모델의 `hyp_ko`만 새로 생성하는 작은 스크립트를 짜서
`eval_stt_propagation.py`처럼 `results.jsonl`의 `hyp_ja`를 그대로 번역기에 태우면 더 빠름.

## 비교표 (채워 넣을 것)

정답 전사(`ja_ref`) 직접 번역 기준 chrF++ — STT 영향 없는 순수 번역 품질:

| 모델 | 전체 chrF++ | normal | hard | 의미충실도 | 자연스러움 | 존댓말일치 | S1율 |
|---|---|---|---|---|---|---|---|
| Qwen2.5-7B (베이스라인) | 24.25 | 25.00 | 22.70 | (재채점 필요) | | | |
| Qwen3-32B | | | | | | | |
| EXAONE-4.0-32B | | | | | | | |
| Gemma-3-27B-it | | | | | | | |

의미충실도/자연스러움/존댓말일치/S1율은 `docs/EVAL.md` §3.2 기준 — 이번에도 Claude가 직접
채점(이번 세션에서 한 것과 동일한 방식, `data/eval_set_2026-08-18_graded.jsonl` 참고).

## 선정 기준

1. 순수 번역 chrF++(정답 전사 기준)가 가장 높은 모델을 우선 고려.
2. 동률이면 자연스러움·존댓말일치 점수(한국어 출력 품질에 더 직결)로 타이브레이크.
3. 최종 후보로 좁힌 뒤 실제 STT 출력(`hyp_ja`, 오류 포함)으로도 재확인 — 정답 전사에서는
   잘하는데 STT 오류에 유난히 취약한 모델이 있을 수 있음.
4. 선정 후 `config.py`의 `LLAMA_SERVER_MODEL`/`LLAMA_SERVER_URL` 관련 주석과
   README 실행 커맨드를 새 모델 기준으로 갱신.
