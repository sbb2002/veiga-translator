# 개선 구현 명세 (작성: 2026-08-19)

`docs/IMPROVEMENT_BACKLOG.md`의 각 항목을 실제로 어떻게 구현할지 정의한다. 항목 ID는 백로그와
동일. 기준 코드는 커밋 `be319ef` 시점의 트리.

## 전제 — 번역 런타임 계약 (모든 T/Q 항목의 기반)

코드가 요구하는 번역 서버 계약 (`backend/translation/llama_server_engine.py`, `backend/config.py`):

- `http://127.0.0.1:8080/v1/chat/completions` OpenAI 호환 엔드포인트.
- llama.cpp server 계열 확장 필드를 **실제로 해석**할 것: `grammar`(GBNF — 한글 강제
  스크립트 순수성의 유일한 강제 장치), `repeat_penalty`/`repeat_last_n`.
- 서빙 모델: gemma-3-12b-it (`LLAMA_SERVER_MODEL`은 표기용, 서버가 무엇을 띄웠는지가 실체).

**주의 — 환경 확인 필요 (2026-08-19 관찰)**: 이 머신에서 gemma GGUF 파일이 발견되지 않았고
(`backend/models/`에는 Qwen2.5 GGUF만 존재), 저장소 동봉 `llama-server/` 빌드는 CPU 전용
dll 구성이다. 즉 `config.py:70`의 실행 예시 커맨드는 이 폴더 상태 그대로는 재현되지 않는다.
실제 사용하는 서버 바이너리/모델 경로/실행 커맨드를 확인해 `README`와 `main.py` 헤더에
기록할 것(→ D1). **만약 실제 런타임이 llama.cpp server 계열이 아니라면**(Ollama, LM Studio 등
— 이들의 OpenAI 호환 API는 `grammar`/`repeat_penalty`를 조용히 무시한다) 스크립트 순수성
강제가 사라진 상태이므로, R4의 자가진단이 이를 즉시 드러내며 T1/T3 및 grammar 관련 설계는
그 시점에 재검토한다.

## 구현 순서 제안

| 배치 | 항목 | 성격 |
|---|---|---|
| 1 | Q1 (계측) + R4 (런타임 자가진단) | 이후 모든 판단의 근거. 코드 소규모 |
| 2 | R1 (예외 격리) + Q3 (fast 타임아웃) | 같은 파일들, 한 묶음으로 |
| 3 | R2 (WS 재연결) + R3 (stop 드레인) | extension + backend 종료 흐름 한 묶음 |
| 4 | S1 (16kHz 컨텍스트), S4 (NFKC), Q7 (VAD 공유), T1 (hint 위치), D1 (문서) | 각각 독립·소규모, 순서 무관 |
| 5 | Q2 (partial 분리) → 계측 결과에 따라 Q4/Q5/Q6, S3 | Q1 데이터 확보 후 |
| 6 | S2, T3, T4, T2 | 실험/A-B 트랙. T4는 Q2 이후 |

---

## R1. 엔진 호출 예외 격리

**변경**: `backend/audio_session.py`

**설계**

1. `_UtteranceState`에 `last_partial_translation: str = ""` 필드 추가.
2. `_emit_partial()` 내부에서 STT와 번역을 각각 격리:
   ```python
   try:
       stt_result = await asyncio.to_thread(self._stt.transcribe, utterance.audio(), fast=True)
   except Exception:
       logger.exception("partial STT failed — skipping this cycle")
       return utterance.last_partial_text
   ...
   try:
       translation = await self._translate.translate(...)
       utterance.last_partial_translation = translation.text
   except Exception:
       logger.exception("partial translation failed — reusing last translation")
       translation = TranslationResult(text=utterance.last_partial_translation)
   ```
   번역이 죽어도 일본어 전사는 계속 갱신·표시된다 (한국어 줄은 마지막 성공값 유지).
3. `_finalize_worker()` 루프를 어떤 예외에도 살아남게:
   ```python
   while True:
       utterance = await self._finalize_queue.get()
       try:
           await self._do_finalize(utterance)
       except asyncio.CancelledError:
           raise
       except Exception:
           logger.exception("finalize failed for segment %s", utterance.segment_id)
           await self._emit_safe({"type": "final", "text": utterance.last_partial_text,
                                  "translation": utterance.last_partial_translation,
                                  "segment_id": utterance.segment_id})
       finally:
           self._finalize_queue.task_done()
   ```
   폴백 final은 마지막 partial 결과로라도 UI를 partial 상태에서 확정시킨다.
4. `_do_finalize` 내부도 같은 패턴: final STT 실패 → `last_partial_text` 폴백(기존 빈-결과
   폴백과 동일 경로), final 번역 실패 → `last_partial_translation`으로 final 이벤트 방출.
   **번역이 실패한 세그먼트는 `_final_history`에 넣지 않는다** (폴백 번역문이 다음 문장의
   문맥으로 재사용되는 것 방지).
5. 이벤트 전송 헬퍼 `_emit_safe(event)` 추가: `self._on_event(event)`를 try/except로 감싸
   웹소켓이 이미 닫힌 경우(R3의 드레인 중 클라이언트 이탈 등) 로그만 남기고 계속.
   `_emit_partial`/`_do_finalize`의 기존 `_on_event` 호출을 이것으로 교체.

**검증**: 라이브 캡처 중 번역 서버를 강제 종료 → (a) 세션이 안 죽고 일본어 partial이 계속
갱신되는지, (b) final이 폴백으로 확정되는지, (c) 서버 재기동 후 번역이 자동 복귀하는지 확인.
백엔드 로그에 exception 스택이 남아야 한다.

**의존**: 없음. Q3과 같은 PR 권장.

---

## R2. WebSocket 자동 재연결

**변경**: `extension/offscreen.js`

**설계**

1. 모듈 상태에 `captureActive = false`, `reconnectDelayMs`, `reconnectTimer` 추가.
2. `startCapture()` 성공 경로에서 `captureActive = true` 후 `connectWebSocket()`.
   `stopCapture()`는 **가장 먼저** `captureActive = false` (재연결 억제).
3. `connectWebSocket()`에 핸들러 추가:
   ```javascript
   ws.onopen = () => { reconnectDelayMs = 1000; ws.send(JSON.stringify({ type: "start_session", ... })); };
   ws.onclose = () => {
     ws = null;
     if (!captureActive) return;
     reconnectTimer = setTimeout(connectWebSocket, reconnectDelayMs);
     reconnectDelayMs = Math.min(reconnectDelayMs * 2, 10000);
   };
   ```
   백오프: 1s 시작, 2배씩, 상한 10s. `onerror`는 지금처럼 로그만 (close가 뒤따라온다).
4. `stopCapture()`에서 `clearTimeout(reconnectTimer)`.
5. 끊긴 동안의 오디오는 기존 `ws.readyState !== OPEN` 가드가 자연스럽게 버린다 — 유실 허용
   (라이브 번역이므로 밀린 과거를 몰아 보내는 것이 오히려 해롭다). 버퍼링하지 않는다.
6. 재연결되면 백엔드는 새 연결로 새 `AudioSession`을 만든다 — 서버 쪽 변경 불필요.

**검증**: 캡처 중 uvicorn을 재시작 → 수 초 내 자막이 재개되는지. 백엔드를 끈 채 Start
Capture → 백엔드를 나중에 켜도 붙는지. Stop Capture 후 재연결 시도가 없는지(콘솔 확인).

**의존**: 없음.

---

## R3. stop 시 finalize 큐 드레인

**변경**: `backend/audio_session.py`, `backend/config.py`, `extension/offscreen.js`,
`extension/background.js`

**설계**

1. `config.py`에 `CLOSE_DRAIN_TIMEOUT_S = 10.0` 추가.
2. `AudioSession.close()`:
   ```python
   async def close(self) -> None:
       if self._utterance is not None:
           self._enqueue_finalize()          # 진행 중이던 마지막 발화도 확정 대상에 포함
       try:
           await asyncio.wait_for(self._finalize_queue.join(), timeout=config.CLOSE_DRAIN_TIMEOUT_S)
       except asyncio.TimeoutError:
           logger.warning("finalize drain timed out — dropping %d queued utterances",
                          self._finalize_queue.qsize())
       self._finalize_worker_task.cancel()
   ```
   드레인 중 이벤트 전송은 R1의 `_emit_safe`가 소켓 닫힘을 흡수한다.
3. `extension/offscreen.js` `stopCapture()` 수정 — 지금은 `stop_session` 전송 직후
   `ws.close()`라서 드레인된 final이 도착할 수 없다. 새 흐름:
   - `captureActive = false` (R2 재연결 억제)
   - 오디오 그래프(worklet/context/stream)는 즉시 정리 (기존 코드 그대로)
   - `ws.send({type: "stop_session"})` 후 **ws는 열어둔다** — 서버가 드레인을 마치고
     핸들러를 리턴하면 서버 쪽에서 닫는다. 안전장치로 12s 타이머 후 강제 `ws.close()`.
   - `ws.onmessage`는 살아있으므로 늦게 도착한 final들이 정상 브로드캐스트된다.
4. `extension/background.js`: `chrome.storage.session.remove("transcriptLog")`를
   `stopCapture()`에서 **`startCapture()`로 이동** — stop 직후 도착하는 드레인 final이
   로그에서 지워지지 않고, 사용자가 stop 후에도 히스토리를 다시 볼 수 있다. 새 세션
   시작 시점에 이전 로그가 비워진다.

**검증**: 말이 이어지는 도중 Stop Capture → 마지막 문장(들)의 final이 팝업에 도착해 확정
표시되는지. stop 후 팝업을 닫았다 열어도 히스토리가 남아 있는지. 드레인 10s 초과 시나리오
(번역 서버 정지 상태에서 stop)에서 백엔드가 경고 로그와 함께 정상 종료되는지.

**의존**: R1(`_emit_safe`), R2(`captureActive` 플래그 공유).

---

## R4. 시작 시 번역 런타임 계약 자가진단 (신규 항목)

**목적**: "전제" 절의 계약 위반 — 특히 서버가 `grammar`를 무시하는 경우(스크립트 순수성이
소리 없이 사라지는 최악의 조용한 회귀) — 를 첫 발화가 아니라 백엔드 기동 시점에 드러낸다.

**변경**: `backend/translation/llama_server_engine.py`, `backend/main.py`

**설계**

1. `LlamaServerEngine.verify_contract()` 추가:
   ```python
   async def verify_contract(self) -> None:
       """Startup probe: server reachable + honors GBNF grammar."""
       try:
           resp = await self._client.post("/v1/chat/completions", json={
               "model": config.LLAMA_SERVER_MODEL,
               "messages": [{"role": "user", "content": "1+1=?"}],
               "max_tokens": 4,
               "temperature": 0.0,
               "grammar": 'root ::= "가"',
           })
           resp.raise_for_status()
           out = resp.json()["choices"][0]["message"]["content"].strip()
       except Exception:
           logger.warning("translation server unreachable at startup — will retry per request")
           return
       if out != "가":
           logger.warning(
               "translation server does NOT honor GBNF grammar (probe returned %r) — "
               "Korean-only script enforcement is INACTIVE. Is this a llama.cpp server?", out)
   ```
   grammar가 강제되면 출력은 정확히 "가"일 수밖에 없다 — 그 외 응답은 grammar 무시의 증거.
2. `main.py` startup에서 엔진 생성 직후 `await _translation_engine.verify_contract()` 호출.
   실패는 경고만 (서버를 백엔드보다 늦게 켜는 워크플로 허용 — 기존 동작 유지).

**검증**: llama.cpp server 상대로는 경고 없음. (가능하면) Ollama를 8080에 띄워 경고가 뜨는지
교차 확인. 서버 미기동 상태에서 unreachable 경고가 뜨고 기동은 계속되는지.

**의존**: 없음. Q1과 같은 배치 권장.

---

## Q1. 단계별 latency 계측

**변경**: `backend/audio_session.py`, `backend/translation/llama_server_engine.py`

**설계**

1. `_UtteranceState`에 `enqueued_at: float = 0.0` 추가 (`_enqueue_finalize`에서 스탬프).
2. partial 경로 (`_emit_partial`): STT/번역 각각 `time.monotonic()`으로 감싸 한 줄 로그:
   ```
   partial seg=%s buf=%.1fs stt=%.2fs llm=%.2fs
   ```
   (`buf` = `utterance.duration_s()` — Q4 판단의 핵심 축: buf 대비 stt 증가 추세)
3. final 경로 (`_do_finalize`): 
   ```
   final seg=%s queue_wait=%.2fs depth=%d stt=%.2fs llm=%.2fs
   ```
   `queue_wait = 처리 시작 - enqueued_at`, `depth = self._finalize_queue.qsize()` (처리 시작 시점).
4. finalize 트리거 사유 로그: `_process_frame`의 `should_finalize` 분기에서 사유를 판정해
   `_enqueue_finalize` 직전에 로그 — `hard_cap` / `grace_expired` / `silence_complete`,
   그리고 선제 분할 지점에서 `strong_boundary`. S3(hard cap 빈도)·문장 분할 튜닝의 근거 데이터.
5. `llama_server_engine.translate()`: 응답 JSON에 `timings`가 있으면(llama.cpp server가 제공)
   `prompt_ms`/`predicted_ms`를 DEBUG 로그로 — T1(prefix cache) 효과 검증용.
   ```python
   timings = data.get("timings")
   if timings:
       logger.debug("llm timings fast=%s prompt_ms=%.0f predicted_ms=%.0f", fast,
                    timings.get("prompt_ms", -1), timings.get("predicted_ms", -1))
   ```
6. 집계는 로그 grep으로 충분 — 별도 메트릭 인프라 없음. 예:
   `Select-String "partial seg=" backend.log | ...` 수준의 일회성 분석.

**검증**: 라이브 10분 캡처 후 로그에서 (a) partial stt가 buf에 비례해 커지는지(→Q4),
(b) final 처리 중 partial 지연 스파이크(→Q5), (c) depth>0 발생 빈도(→Q6), (d) hard_cap
사유 빈도(→S3)를 실측. 이 결과가 배치 5의 착수 여부를 정한다.

**의존**: 없음. 모든 Q 후속 항목의 선행 조건.

---

## Q2. partial 트랙을 오디오 경로에서 분리

**변경**: `backend/audio_session.py`, `docs/PIPELINE.md`(서술 갱신 — D1과 함께)

**설계**

1. `_UtteranceState`에 `finalized: bool = False` 추가. `AudioSession`에
   `self._partial_task: asyncio.Task | None = None`.
2. `_enqueue_finalize()`에 idempotent 가드:
   ```python
   def _enqueue_finalize(self) -> None:
       utterance = self._utterance
       self._utterance = None
       if utterance is not None and not utterance.finalized:
           utterance.finalized = True
           utterance.enqueued_at = time.monotonic()
           self._finalize_queue.put_nowait(utterance)
   ```
3. `_process_frame`의 partial 구간을 태스크 발사로 교체 — **이전 partial이 아직 실행 중이면
   이번 주기는 건너뛴다** (큐 없음; 다음 주기가 더 최신 버퍼로 돈다. 케이던스가 자연히
   실제 처리 속도에 맞춰진다):
   ```python
   if enough_audio and now - utterance.last_partial_at >= config.PARTIAL_UPDATE_INTERVAL_S:
       if self._partial_task is None or self._partial_task.done():
           utterance.last_partial_at = now
           self._partial_task = asyncio.create_task(self._run_partial(utterance))
   ```
4. `_run_partial(utterance)` = 기존 `_emit_partial` 본문 + 뒤로 이동한 선제 분할 판정:
   - STT 후 `utterance.finalized`이면 즉시 리턴 (final 뒤에 partial이 도착해 UI가 되돌아가는
     역전 방지 — popup.js는 segment_id별 마지막 이벤트가 이긴다).
   - 번역 후에도 같은 체크 후 `_emit_safe`.
   - 마지막에 선제 분할: `if utterance is self._utterance and utterance.silence_ms == 0.0
     and has_strong_sentence_boundary(text): self._enqueue_finalize()`.
     (`silence_ms`는 태스크 완료 시점 값으로 판정 — 원래 의도인 "지금도 쉼 없이 말하는 중"
     조건과 동일하게 보수적으로 동작한다.)
   - 본문 전체가 R1의 예외 격리를 유지해야 한다 — create_task로 떠난 태스크의 예외는
     아무도 안 기다리므로 내부에서 전부 잡는다.
5. 이벤트 루프는 단일 스레드이므로 별도 락 불필요 — `finalized` 플래그와 `is self._utterance`
   동일성 체크만으로 충분하다.
6. `close()`(R3)에서 `self._partial_task` cancel 추가.

**검증**: Q1 로그로 (a) partial 실행 중에도 프레임 처리가 계속되는지(오디오 적체 해소),
(b) "skip" 주기가 얼마나 발생하는지 확인. UI에서 final 확정 후 partial로 되돌아가는 사례가
없는지 육안 확인. 선제 분할이 이전과 동일하게 동작하는지(쉼 없이 말하는 구간에서 문장
단위로 쪼개지는지).

**의존**: R1 (예외 격리 패턴), Q1 (착수 판단 근거). PIPELINE.md의 "논블로킹" 서술을 이
변경과 함께 사실로 만든다.

---

## Q3. fast 경로 전용 짧은 타임아웃

**변경**: `backend/config.py`, `backend/translation/llama_server_engine.py`

**설계**

1. `config.py`: `LLAMA_FAST_TIMEOUT_S = 3.0` 추가 (`LLAMA_SERVER_TIMEOUT_S`는 final용으로 유지).
2. `translate()`의 POST에 per-request 타임아웃:
   ```python
   response = await self._client.post("/v1/chat/completions", json=request_json,
       timeout=config.LLAMA_FAST_TIMEOUT_S if fast else config.LLAMA_SERVER_TIMEOUT_S)
   ```
3. 타임아웃 발생은 R1이 흡수 — partial은 해당 주기 번역만 건너뛰고 다음 주기에 재시도된다.

**검증**: 번역 서버에 인위적 지연(대형 요청 몰아주기 등) 상황에서 partial의 일본어 갱신이
3s 이상 멈추지 않는지.

**의존**: R1 (타임아웃 예외를 살아남게 하는 쪽이 먼저).

---

## Q4. partial 재전사 비용 — Q2로 자연 완화, 실측 후 재평가

**설계**

Q2의 "이전 태스크 실행 중이면 skip" 규칙이 곧 자기-조절(케이던스가 처리 시간에 수렴)이라
적체는 구조적으로 사라진다. 남는 문제는 발화 후반부 partial의 표시 지연(버퍼 10s 기준
fast STT 소요)뿐이며, `MAX_UTTERANCE_SECONDS = 10` 캡이 상한을 만든다.

**착수 조건**: Q1 실측에서 `buf≈10s`일 때 partial `stt`가 `PARTIAL_UPDATE_INTERVAL_S`(0.6s)를
유의미하게 초과할 때만. 그 경우의 설계 스케치 (tail-window):

- `config.PARTIAL_WINDOW_SECONDS = 5.0` 추가.
- `_UtteranceState`에 `frozen_text: str`, `frozen_samples: int` 추가. 버퍼가 window를 넘으면
  넘는 시점의 partial 텍스트를 `frozen_text`로 동결하고 `frozen_samples`에 해당 구간 샘플
  수를 기록. 이후 partial STT는 `audio()[frozen_samples:]`(tail)만 전사해
  `frozen_text + tail_text`로 표시.
- 동결 경계의 단어 깨짐은 허용 — final 패스가 전체 버퍼를 다시 전사하므로 확정 품질에는
  영향 없다. provisional의 정의("일단 뭔가 뜬다", PRD §7)에 부합.

**의존**: Q2, Q1.

---

## Q5. GPU 경쟁 — 실측 후 STT 직렬화

**착수 조건**: Q1 실측에서 final 처리 중(`depth>0` 또는 final stt/llm 실행 구간과 겹칠 때)
partial `stt` 소요가 평시 대비 뚜렷이 튀는 패턴이 보일 때만.

**설계** (`backend/audio_session.py`)

- 모듈 레벨 `_STT_EXECUTOR = ThreadPoolExecutor(max_workers=1)`.
- 두 STT 호출을 `asyncio.to_thread(...)` 대신
  `asyncio.get_running_loop().run_in_executor(_STT_EXECUTOR, functools.partial(...))`로 교체 —
  partial/final STT가 GPU에서 겹치지 않게 직렬화.
- FIFO라 긴 final STT가 partial을 최대 1건 지연시킨다 — Q2의 skip 규칙이 흡수. partial
  기아가 실측되면 그때 우선순위 큐로 확장 (지금은 하지 않는다).
- LLM 쪽은 서버의 멀티 슬롯에 그대로 맡긴다.

**의존**: Q1, Q2.

---

## Q6. finalize 큐 backlog 열화 모드 — 실측 후

**착수 조건**: Q1 실측에서 `depth >= 2`가 반복 관찰될 때만.

**설계** (`backend/config.py`, `backend/audio_session.py`)

- `config.FINALIZE_BACKLOG_FAST_STT_DEPTH = 3` 추가.
- `_do_finalize` 시작부:
  ```python
  degraded = self._finalize_queue.qsize() >= config.FINALIZE_BACKLOG_FAST_STT_DEPTH
  stt_result = await ...transcribe(audio, fast=degraded)
  ```
  backlog가 깊으면 해당 건의 재전사를 beam=1로 낮춰 처리량 회복. 번역은 그대로 final 품질
  유지. 열화 발생 시 INFO 로그 필수.

**의존**: Q1.

---

## Q7. VAD 모델 1회 로드 + 세션별 reset

**변경**: `backend/main.py`, `backend/audio_session.py`

**설계**

1. `main.py` startup에서 `_vad = SileroVAD()` 1회 생성 (STT 엔진과 동일 패턴), `ws_audio`에서
   `AudioSession(..., vad=_vad)`로 주입.
2. `AudioSession.__init__` 시그니처에 `vad: SileroVAD` 추가하고 내부 생성 제거. 생성자에서
   `vad.reset()` 호출 (이전 연결의 RNN 상태 초기화 — 현재 정의만 있고 미호출인 메서드).
3. 동시 세션 2개가 상태를 공유하는 문제는 단일 사용자 앱 특성상 실제로 발생하지 않는다
   (offscreen.js가 단일 캡처를 보장) — `AudioSession` 주입 지점에 해당 전제를 주석으로 명시.

**검증**: 캡처 stop→start 반복 시 시작 지연이 줄었는지 (기존: 연결마다 torch.hub 로드).
두 번째 세션의 초반 VAD 오판(이전 상태 이월)이 사라지는지는 로그로 간접 확인.

**의존**: 없음.

---

## S1. AudioContext를 16kHz로 — 리샘플러 삭제

**변경**: `extension/offscreen.js`

**설계**

1. `audioContext = new AudioContext({ sampleRate: TARGET_SAMPLE_RATE })` — Chrome이
   MediaStreamSource 입력을 안티앨리어싱 필터 포함 고품질 리샘플러로 16kHz 변환해준다.
2. 워크릿(`audio-worklet-processor.js`)은 `sampleRate` 전역을 그대로 쓰므로 **변경 불필요** —
   자동으로 4800샘플(0.3s@16kHz) 청크를 만든다.
3. `workletNode.port.onmessage`에서 `resampleLinear` 호출 제거, 함수 자체 삭제.
   (`floatTo16BitPCM`은 유지.)
4. `<audio>` 요소 재생은 AudioContext와 무관한 경로라 재생 음질에 영향 없다.

**검증**: 실스트림 캡처로 백엔드가 여전히 0.3s 단위 PCM을 받는지(로그의 청크 크기), 자막이
정상 동작하는지. 품질 개선 폭 정량화는 다음 EVAL 라운드에 맡긴다(별도 A/B 불필요 —
이론적으로 순개선이고 코드가 줄어드는 방향).

**의존**: 없음.

---

## S2. final STT에 previous_context 연결 (기본 off 실험)

**변경**: `backend/config.py`, `backend/audio_session.py`

**설계**

1. `config.STT_FINAL_USE_PREVIOUS_CONTEXT = False` 추가 (환각 리스크 때문에 기본 off —
   `main.py:41`의 hotwords 제거와 같은 계열의 리스크).
2. `_do_finalize`:
   ```python
   prev_ja = self._final_history[-1][0] if (config.STT_FINAL_USE_PREVIOUS_CONTEXT
                                            and self._final_history) else None
   stt_result = await ...transcribe(audio, fast=False, previous_context=prev_ja)
   ```
3. **평가 방법**: 기존 EVAL 데이터셋은 독립 클립이라 문맥 효과를 못 잰다. 실스트림 세션을
   플래그 on/off로 각각 15분 이상 돌리고 (a) 고유명사/한자 표기가 세그먼트 간 일관돼지는지,
   (b) `STT dropped segment` 로그와 환각성 출력(직전 문장 어휘가 무관한 오디오에 출현)이
   늘지 않는지 비교.
4. 효과가 없거나 환각이 늘면: 플래그를 지우면서 `previous_context` 파라미터 자체를
   `STTEngine` 인터페이스에서 제거 (죽은 배선 정리로 결론).

**의존**: 없음. R1 이후 권장(실험 중 서버 이슈로 세션이 죽지 않게).

---

## S3. hard cap 절단을 무음 경계로 — 빈도 확인 후

**착수 조건**: Q1의 트리거 사유 로그에서 `hard_cap` 빈도가 유의미할 때만 (선제 분할이
대부분을 먼저 자르고 있다면 불필요).

**설계** (`backend/audio_session.py`)

1. `_UtteranceState`에 `last_silence_frame: int | None = None` 추가 — `_process_frame`의
   무음 분기(`elif self._utterance is not None`)에서 `utterance.last_silence_frame =
   len(utterance.buffer) - 1`로 갱신.
2. hard cap 트리거 시에만 분할 finalize:
   ```python
   if past_hard_cap and utterance.last_silence_frame is not None:
       head, tail = utterance.buffer[:idx + 1], utterance.buffer[idx + 1:]
       # head로 기존 utterance를 finalize, tail은 새 _UtteranceState의 buffer로 이월
       # (segment_id 새로 발급, started_at은 tail 길이만큼 소급)
   ```
   `last_silence_frame`이 None(10초 내내 무음 프레임 0개)이면 기존대로 통째로 자른다.
3. 무음 경계가 너무 이르면(head가 지나치게 짧으면) 분할 이득이 없으므로 head가 최소
   `MIN_PARTIAL_AUDIO_SECONDS` 이상일 때만 분할.

**검증**: `MAX_UTTERANCE_SECONDS`를 임시로 4로 낮추고 연속 발화 스트림 캡처 → 분할 지점
전사가 단어 중간에서 깨지지 않는지, 이월된 tail이 다음 세그먼트로 자연스럽게 이어지는지.

**의존**: Q1 (빈도 근거).

---

## S4. glossary NFKC 정규화 매칭

**변경**: `backend/glossary.py`

**설계**

1. `load()`에서 key를 `unicodedata.normalize("NFKC", src)`로 정규화해 저장 (target은 원문
   유지 — 프롬프트/grammar literal에 그대로 들어가는 값이므로 건드리지 않는다).
2. `match()`에서 입력도 NFKC 정규화 후 substring 비교:
   ```python
   normalized = unicodedata.normalize("NFKC", text)
   return [(src, tgt) for src, tgt in self._entries.items() if src in normalized]
   ```
3. 가나 퍼지 매칭(장음 유무 등)은 glossary가 실제로 커진 뒤 재검토 — 현재 1항목에는 과설계.
4. 파일 하단에 자가 체크 추가 (`python -m backend.glossary`로 실행):
   ```python
   if __name__ == "__main__":
       g = Glossary({"ティーワイ": "TY"})
       assert g.match("ティーワイです")           # 반각 가나 NFKC → 전각 매칭
       assert g.latin_targets("ティーワイ") == ("TY",)
       print("glossary self-check OK")
   ```

**검증**: 위 self-check + 실스트림에서 등록 용어가 hint로 나가는지 로그 확인.

**의존**: 없음. glossary.json 채우기(백로그 추적표 B — 별도 데이터 작업)와 병행하면 효과 큼.

---

## T1. glossary_hint를 user message로 이동 (prefix cache 보존)

**변경**: `backend/translation/llama_server_engine.py`

**설계**

1. system prompt에 hint를 붙이는 분기 제거. 대신 user content 조립을 섹션 방식으로 통일:
   ```python
   sections = []
   if glossary_hint:
       sections.append(f"[GLOSSARY]\n{glossary_hint}")
   if context:
       sections.append(f"[PREVIOUS SENTENCE]\n{context}")
       if context_translation:
           sections.append(f"[PREVIOUS TRANSLATION]\n{context_translation}")
   sections.append(f"[TEXT TO TRANSLATE]\n{text}" if sections else text)
   user_content = "\n\n".join(sections)
   ```
   (섹션이 하나도 없으면 지금처럼 bare text — fast 경로의 흔한 경우가 가장 짧게 유지된다.)
2. 두 system prompt에 정적 한 문장 추가: "[GLOSSARY] 섹션이 있으면 그 대응을 그대로 따르라"
   — system prompt는 이제 요청 간 완전 불변이 되어 llama.cpp server의 프롬프트 prefix KV
   cache가 매 요청 재사용된다.
3. 효과 측정: Q1-5의 `timings.prompt_ms` — hint가 있는 요청에서 변경 전/후 비교.
4. 품질 확인: glossary 등록 용어가 포함된 문장 재현 테스트 (ティーワイ → "TY" 유지 확인,
   grammar literal 예외와 함께).

**의존**: Q1-5 (측정 수단). T2·T4가 같은 섹션 조립 코드를 재사용하므로 T1을 먼저.

---

## T2. 단어별 예외 노트를 데이터 파일로 — 노트가 더 늘어나면

**착수 조건**: 단어 특정 노트(현재 ずるい/やばい/嘘/キンキン/ガーッチ 등)가 계속 늘어나
프롬프트 비대·기존 사례 회귀가 걱정되는 시점. 그 전까지 현행 유지.

**설계** (`backend/translation/llama_server_engine.py`, 신규 `backend/translation_notes.json`)

1. `translation_notes.json`: `[{"triggers": ["ずるい"], "note": "..."}, ...]` — 입력 텍스트에
   trigger가 (NFKC 정규화 후) 포함될 때만 해당 note를 T1의 섹션 조립에 `[NOTES]`로 주입.
   일반 지침(_SLANG_NOTE의 일반부, _FILLER_NOTE, _HONORIFIC_NOTE, _NO_ENGLISH_NOTE 등)은
   system prompt에 남긴다 — 단어 사례만 이주.
2. 회귀 안전망(이 항목의 전제 조건으로 먼저 구축): `data/regression_cases.jsonl`
   (`{"ja": ..., "must_include": [...], "must_exclude": [...]}`) + `scripts/check_regressions.py`
   (~20줄: 각 케이스를 엔진에 직접 넣고 포함/배제 검사, 실패 목록 출력). 지금까지 실사용에서
   잡은 사례(やばい→야바위 금지, 嘘→농담 태그, ガーッチ→가르치 금지, w→ㅋㅋㅋ 유지 등)를
   시드로 등록. **노트를 추가/이동할 때마다 이 스크립트를 돌린다.**

**의존**: T1 (섹션 조립). 회귀 스크립트는 T2와 무관하게 먼저 만들어도 가치 있음.

---

## T3. repeat_penalty 조건부 적용 (A/B 후)

**변경**: `backend/translation/llama_server_engine.py`, `backend/config.py`

**설계**

1. 먼저 A/B: EVAL 세트를 penalty on/off로 각각 번역해 chrF++ 비교 + 반복 표현 케이스(ㅋㅋㅋ
   스케일, 강조 반복) 육안 대조. off가 동등 이상이면 진행.
2. 구현: 기본 off로 1차 호출 → 출력에서 반복 붕괴를 감지하면 penalty on으로 1회 재시도:
   ```python
   _REPETITION_COLLAPSE_RE = re.compile(r"(.{1,4})\1{5,}")  # 1~4자 단위가 6회 이상 연속
   ```
   재시도는 **final 경로만** (fast는 지연이 우선 — 나쁜 partial 1회는 다음 주기가 덮는다).
   단, "ㅋㅋㅋㅋㅋㅋ"처럼 정당한 반복이 감지식에 걸리지 않게 ㅋ/ㅎ 연속은 예외 처리
   (`(?!(?:ㅋ|ㅎ))` 또는 감지 전에 ㅋ/ㅎ run을 치환 후 검사 — 구현 시 택1).
3. 하드코딩된 1.3/64를 `config.LLAMA_REPEAT_PENALTY`/`LLAMA_REPEAT_LAST_N`으로 이동.

**검증**: A/B 수치 + 재시도 발동 로그가 실제 붕괴 사례에서만 찍히는지 실스트림 확인.

**의존**: 없음 (T1과 독립). EVAL 재실행 비용 있음.

---

## T4. fast 번역에 직전 문맥 1쌍 — Q2 이후

**착수 조건**: Q2 완료 후 (partial이 오디오 경로 밖으로 나가 토큰 추가의 지연 영향이
격리된 다음). 그 전에는 fast 지연을 직접 키우므로 착수 금지.

**설계** (`backend/audio_session.py`, `backend/translation/llama_server_engine.py`)

1. `_emit_partial`(→`_run_partial`)에서 `self._final_history`의 **마지막 1쌍만** 전달:
   ```python
   translation = await self._translate.translate(stt_result.text, fast=True,
       context=prev_ja, context_translation=prev_ko, ...)
   ```
   (translate()의 섹션 조립은 T1 이후 fast/final 공용이라 코드 변경은 인자 전달뿐.)
2. `_FAST_SYSTEM_PROMPT`에 축약 연속성 지침 2문장 추가 (final의 `_CONTINUITY_NOTE` 전문을
   붙이지 않는다 — fast는 토큰 예산이 지연에 직결): "[PREVIOUS ...] 섹션이 있으면 어투와
   생략된 주어 해석의 참고로만 쓰고, 그 내용을 번역/반복하지 마라" 수준.
3. 효과/비용 측정: Q1-5 `prompt_ms` 증가폭과 partial 체감 품질(지시어/존댓말 연속성)을
   실스트림에서 대조. 지연 증가가 체감되면 롤백 (인자 전달 제거만으로 원복).

**의존**: Q2, T1, Q1-5.

---

## D1. 문서/주석 정리

**변경**: `docs/PIPELINE.md`, `backend/main.py`, `backend/glossary.py`, `backend/stt/base.py`,
`README`(실행 커맨드)

1. `PIPELINE.md`: partial 트랙 "논블로킹" 서술을 현실에 맞게 수정 — Q2 이전이라면 "이벤트
   루프는 막지 않지만 오디오 드레인은 partial 완료를 기다린다"로 정정, Q2 구현 시 그 설계로
   갱신.
2. `main.py` 헤더 docstring의 llama-server 실행 예시를 실제 사용하는 커맨드로 교체 (Qwen
   GGUF 언급 제거). **"전제" 절의 환경 확인 결과를 여기에 반영** — 실제 서버 바이너리 위치와
   gemma3 모델 파일 경로를 명시.
3. `glossary.py` 모듈 docstring: "STT hotwords로 주입된다" 서술을 현실(의도적으로 미배선,
   `main.py:41` 참조)로 수정.
4. `stt/base.py`: `TranscriptionResult.words` 필드 제거 (`word_timestamps=False` 고정, 소비처
   없음 — S3에서도 불필요). `faster_whisper_engine.py`의 빈 `words` 리스트 조립도 함께 제거.

**검증**: 없음(문서). 커밋만 분리해서.
