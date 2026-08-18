# 번역 모델 벤치마크 계획 — 결과 (2026-08-18 실행 완료)

**실행 환경**: 이 저장소가 실제로 있는 머신은 RTX 5090/32GB가 아니라 **RTX 4080 SUPER,
16GB VRAM**이었음. 원래 후보(Qwen3-32B/EXAONE-4.0-32B/Gemma-3-27B-it, 각 19~20GB)는 Q4_K_M로도
16GB에 안 들어가 아래처럼 더 작은 사이즈로 대체해 실행함. `llama-server/`의 실행 파일은
CUDA 빌드(`ggml-cuda.dll`)였으므로 별도 교체 불필요했음.

배경/근거는 `docs/EVAL_REPORT_2026-08-18.md` §5 "C. 번역 엔진 자체 오역" 참고 — 현재 모델
(Qwen2.5-7B-Instruct Q4_K_M)이 STT 오류를 완전히 제거해도(정답 전사 직접 입력) chrF++
24.25에 그쳐, 모델 자체 교체가 다른 개선(A/B/D/E)보다 선행돼야 한다는 결론.

## 결과 요약

정답 전사(`ja_ref`) 직접 번역 기준 chrF++ — STT 영향 없는 순수 번역 품질(전체 120세그먼트):

| 모델 | 전체 chrF++ | normal | hard | 비고 |
|---|---|---|---|---|
| Qwen2.5-7B-Instruct (베이스라인) | 24.64 | 25.46 | 22.99 | grammar 제약 적용, GPU로 재확인(CPU 평가 24.25와 근접) |
| **Qwen3-14B** | **31.44** | 31.45 | 31.32 | **grammar 제약 끄고 `/no_think` 적용** — 아래 "중요 발견" 참고 |
| Gemma-3-12b-it | 28.69 | 30.04 | 26.05 | grammar 제약 정상 호환 |
| EXAONE-3.5-7.8B-Instruct | 22.37 | 21.69 | 23.89 | grammar 제약 정상 호환, 베이스라인보다 낮음(한국어 네이티브 특화라는 선정 이유와 반대 결과) |

**순위**: Qwen3-14B > Gemma-3-12b-it > Qwen2.5-7B(베이스라인) > EXAONE-3.5-7.8B.
Qwen3-14B는 grammar 없이도 라틴 문자 유출 0건이었으나, 아래처럼 다른 스크립트(간체자/히라가나/
번체자) 유출이 소량 확인돼 실사용 전 §"중요 발견" 항목을 반드시 해결해야 함.

## 중요 발견 — Qwen3-14B와 `_KOREAN_ONLY_GRAMMAR`의 비호환

`backend/translation/llama_server_engine.py`의 grammar 제약(한글만 허용하는 GBNF 화이트리스트)을
그대로 켠 채 Qwen3-14B에 적용하면 **chrF++가 6.5~8.7까지 붕괴**한다. 원인: Qwen3의 chat
template은 기본적으로 "thinking" 모드라 `<think>` 프리앰블을 내려고 하는데, grammar가 `<`를
포함한 모든 비한글 문자를 막아버리면 모델이 사고 과정을 낼 곳이 없어져 **시스템 프롬프트에
포함된 한글 예시 단어(`영차`, `-짱` 등)를 그대로 베껴 쓰는 형태로 새어나온다** — grammar를 끄면
동일 입력이 즉시 정상 번역됨을 수동 테스트로 확인.

`/no_think`(시스템 프롬프트에 추가, Qwen3 공식 스위치)로 thinking 자체는 껐지만 grammar를 다시
켜면 여전히 같은 붕괴가 재현됨 — thinking mode 문제와는 별개로, 이 grammar 자체가 Qwen3
계열과 근본적으로 안 맞는 것으로 보인다. 이번 벤치마크는 Qwen3-14B에 한해 **grammar를 끈
채** 실행했다(`LlamaServerEngine.translate(..., use_grammar=False)` — 이번에 추가된 옵션,
베이스라인 동작은 그대로 유지됨).

grammar를 끈 상태에서도 라틴 문자 유출은 0건이었지만, 240개 출력 중 10건(4.2%)에서 히라가나/
간체자/번체자 등 다른 비한글 문자가 소량 섞여 나온 것도 확인됨(`蠟`, `燭`, `ぶ` 등). **Qwen3-14B를
실제로 채택하려면 이 스크립트 순수성 문제를 grammar 없이 해결하는 방법(예: 더 강한 프롬프트
지침, 후처리 필터, 또는 Qwen3 호환 방식의 grammar 재설계)을 먼저 찾아야 함** — 지금 상태로
프로덕션에 넣으면 `docs/EVAL.md` §3.2의 "스크립트 순수성" 자동 S1 조건에 정기적으로 걸림.

## 원래 계획 (참고용 — 위 결과로 대체됨)

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
