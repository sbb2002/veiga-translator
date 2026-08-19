# 작업 핸드오프 — batch1-instrumentation (2026-08-19)

세션 간 인수인계 문서. 구현은 GPU 없는 환경에서 코드 수준 검수까지만 진행했고, **실사용
검증은 GPU 머신에서 이 문서의 체크리스트대로 수행**한다. 명세는 `docs/IMPROVEMENT_SPECS.md`,
항목 배경은 `docs/IMPROVEMENT_BACKLOG.md`.

## 현재 상태

- 브랜치: `batch1-instrumentation` (main에서 분기)
- 구현 범위: **배치 1 = Q1(단계별 latency 계측) + R4(번역 런타임 계약 자가진단)**,
  **배치 2 = R1(엔진 호출 예외 격리) + Q3(fast 전용 3s 타임아웃)**,
  **배치 3 = R2(WS 자동 재연결) + R3(stop 시 finalize 드레인)**
- 검수 수준: 코드 리뷰 + `py_compile` 문법 확인만. **런타임 검증 미실시** (이 환경에 GPU 없음
  — faster-whisper CUDA 로드 불가, gemma 모델/서버도 이 머신에 없음)

## 변경 내용 (검수 후 확정)

| 파일 | 변경 |
|---|---|
| `backend/audio_session.py` | Q1: `enqueued_at`, partial/final 타이밍 INFO 로그, finalize 트리거 사유 로그. R1: STT/번역/이벤트 전송 예외 격리(`_emit_safe`), `last_partial_translation` 폴백, `_finalize_worker` 생존 보장 |
| `backend/translation/llama_server_engine.py` | R4: `verify_contract()` 프로브. Q1: 응답 `timings` DEBUG 로그. Q3: fast/final per-request 타임아웃 분리 |
| `backend/main.py` | startup에서 `verify_contract()` 호출 |
| `backend/config.py` | `LLAMA_FAST_TIMEOUT_S = 3.0`, `CLOSE_DRAIN_TIMEOUT_S = 10.0` 추가 |
| `docs/PIPELINE.md` | partial "논블로킹" 서술 정정, R1 폴백 예외 경로 1줄 추가 |
| `extension/offscreen.js` | R2: `captureActive` + 백오프 재연결(1s→10s cap). R3: stop 시 소켓을 열어둔 채 서버 드레인 종료 대기(12s 강제종료 타이머, 낡은 소켓이 새 세션을 건드리지 않게 인스턴스 기준 처리) |
| `extension/background.js` | R3: transcriptLog 클리어를 stop→start 시점으로 이동 |

## GPU 머신에서의 검증 체크리스트 (저녁)

준비: llama.cpp server(gemma-3-12b-it) 기동 → `uvicorn backend.main:app --port 8000` →
확장으로 실제 일본어 라이브 캡처 10분 이상.

1. **R4 — 기동 로그 확인**:
   - 번역 서버가 켜진 상태로 백엔드 기동 → `translation server contract verified` INFO가 떠야 함.
   - 번역 서버를 끈 채 기동 → `translation server unreachable at startup` WARNING 1줄, 기동은 계속.
   - (가능하면) grammar를 무시하는 서버(Ollama 등)를 8080에 붙여 `does NOT honor GBNF grammar`
     경고가 뜨는지 교차 확인 — 안 되면 생략 가능.
2. **Q1 — 로그 수집**: 캡처 세션 로그를 파일로 남길 것 (예:
   `uvicorn ... 2>&1 | Tee-Object backend_run.log`).
3. **Q1 — 실측 분석** (이 결과가 배치 5의 착수 여부를 정한다 — 명세 Q1 §검증):
   - `partial seg=` 라인: `buf`가 커질수록 `stt`가 얼마나 증가하는가?
     `stt+llm`이 0.6s(PARTIAL_UPDATE_INTERVAL_S)를 넘는 구간이 있는가? → **Q4/Q2 착수 판단**
   - `final seg=` 라인: `queue_wait`/`depth` 분포. `depth >= 2`가 반복되는가? → **Q6 착수 판단**
   - final 처리 구간과 겹치는 시점의 partial `stt` 스파이크가 있는가? → **Q5 착수 판단**
   - `finalize trigger=` 라인: `hard_cap` 빈도가 유의미한가? → **S3 착수 판단**
   - LLM `timings` DEBUG 로그를 보려면 로깅 레벨 DEBUG 필요:
     `logging.basicConfig(level=...)`는 `backend/main.py`에 있음 — 임시로 DEBUG로 바꾸거나
     해당 로거만 레벨 조정. (T1 착수 전 `prompt_ms` 기준선 확보 목적)
4. **R1 — 예외 격리 확인**: 캡처 진행 중 번역 서버(llama.cpp server)를 강제 종료 →
   (a) 세션이 안 죽고 일본어 partial이 계속 갱신되는지, (b) final이 마지막 partial 내용으로
   폴백 확정되는지(`finalize failed`/`translation failed` 스택이 로그에 남는지), (c) 서버
   재기동 후 번역이 자동 복귀하는지. 폴백 final은 `_final_history`에 안 들어가야 함(다음
   문장 번역의 [PREVIOUS TRANSLATION]에 폴백 문장이 안 보이는 것으로 간접 확인).
5. **Q3 — fast 타임아웃 확인**: 번역 서버가 느려진 상황(대형 요청을 병행 투입 등)에서
   partial의 일본어 갱신 공백이 3s(LLAMA_FAST_TIMEOUT_S)를 크게 넘지 않는지.
6. **R2 — 재연결 확인**: 캡처 중 uvicorn 재시작(파일 저장으로 --reload 트리거도 가능) →
   수 초 내 자막 재개. 백엔드를 끈 채 Start Capture → 백엔드를 나중에 켜도 붙는지.
   Stop 후 재연결 시도가 없는지(offscreen 콘솔 — chrome://extensions에서 offscreen 문서
   inspect).
7. **R3 — 드레인 확인**: 말이 이어지는 도중 Stop Capture → 마지막 문장(들)의 final이
   팝업에 도착해 확정되는지. stop 후 팝업을 닫았다 열어도 히스토리가 남아 있고, 다음
   Start에서 비워지는지. **stop 직후 곧바로 Start를 다시 눌러도**(드레인 진행 중 재시작)
   새 세션 자막이 정상 동작하는지 — 낡은 소켓 정리가 새 세션을 건드리는 엣지를 코드
   수준에서 막아뒀는데 실동작 확인 필요.
8. 이상 없으면 main에 머지. 실측 요약은 이 문서에 "실측 결과" 절로 추가해둘 것.

## 다음 배치 (명세의 구현 순서)

- 배치 2: R1(예외 격리) + Q3(fast 타임아웃) — 같은 파일 묶음, GPU 없이도 구현/코드 검수 가능
- 배치 3: R2(WS 재연결) + R3(stop 드레인)
- 배치 4: S1(16kHz AudioContext), S4(NFKC), Q7(VAD 공유), T1(hint 위치), D1(문서)
- 배치 5 이후는 위 3번의 실측 결과가 선행 조건.
