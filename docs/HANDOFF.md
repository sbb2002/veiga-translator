# 작업 핸드오프 — batch1-instrumentation (2026-08-19)

세션 간 인수인계 문서. 구현은 GPU 없는 환경에서 코드 수준 검수까지만 진행했고, **실사용
검증은 GPU 머신에서 이 문서의 체크리스트대로 수행**한다. 명세는 `docs/IMPROVEMENT_SPECS.md`,
항목 배경은 `docs/IMPROVEMENT_BACKLOG.md`.

## 현재 상태

- 브랜치: `batch1-instrumentation` (main에서 분기)
- 구현 범위: **배치 1 = Q1(단계별 latency 계측) + R4(번역 런타임 계약 자가진단)**
- 검수 수준: 코드 리뷰 + `py_compile` 문법 확인만. **런타임 검증 미실시** (이 환경에 GPU 없음
  — faster-whisper CUDA 로드 불가, gemma 모델/서버도 이 머신에 없음)

## 변경 내용 (검수 후 확정)

| 파일 | 변경 |
|---|---|
| `backend/audio_session.py` | `_UtteranceState.enqueued_at` 추가, partial/final 타이밍 INFO 로그, finalize 트리거 사유 로그 |
| `backend/translation/llama_server_engine.py` | `verify_contract()` 프로브 추가, 응답 `timings` DEBUG 로그 |
| `backend/main.py` | startup에서 `verify_contract()` 호출 |

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
4. 이상 없으면 main에 머지. 실측 요약은 이 문서에 "실측 결과" 절로 추가해둘 것.

## 다음 배치 (명세의 구현 순서)

- 배치 2: R1(예외 격리) + Q3(fast 타임아웃) — 같은 파일 묶음, GPU 없이도 구현/코드 검수 가능
- 배치 3: R2(WS 재연결) + R3(stop 드레인)
- 배치 4: S1(16kHz AudioContext), S4(NFKC), Q7(VAD 공유), T1(hint 위치), D1(문서)
- 배치 5 이후는 위 3번의 실측 결과가 선행 조건.
