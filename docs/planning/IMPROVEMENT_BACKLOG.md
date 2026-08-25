# 개선 백로그 — 코드 리뷰 기반 (작성: 2026-08-19)

전사/번역 품질 향상 작업 중 코드 전체를 리뷰하며 찾은 개선 후보 목록.
**각 항목의 구체적 구현 방법은 `docs/planning/IMPROVEMENT_SPECS.md` 참고** (항목 ID 동일).
`docs/eval/EVAL_REPORT_2026-08-18.md` §5의 기존 개선 계획(A~E)과 겹치는 항목은 여기 다시 쓰지 않고
맨 아래 "기존 계획 추적" 표에서 상태만 관리한다. 항목이 처리되면 이 문서에서 상태를 갱신할 것.

우선순위 기준:
- **P0** — 품질 이전의 문제. 실사용 중 세션이 통째로 죽거나, 이후 모든 최적화 판단의 전제가 되는 것.
- **P1** — 지연/품질에 직접적인 영향이 있고 구현 비용이 낮은 것.
- **P2** — 효과가 있을 것으로 보이나 계측/실험으로 확인이 먼저 필요한 것.

**구현 현황 (2026-08-19, `batch1-instrumentation` 브랜치, GPU 검증 대기)**:
R1·R2·R3·R4·Q1·Q3·Q7·S1·S4·T1·D1 구현 완료. 미착수(실측/실험 선행 조건):
Q2·Q4·Q5·Q6·S2·S3·T2·T3·T4. 검증 절차는 `docs/log/HANDOFF.md`. **각 섹션의 증상/코드 서술은
구현 전(`be319ef`) 코드 기준**이므로, 구현된 항목의 현재 동작은 코드와 `PIPELINE.md`가 기준.

## 요약

| ID | 분류 | 항목 | 우선순위 | 상태 (2026-08-19) |
|---|---|---|---|---|
| R1 | 안정성 | 엔진 호출 예외 격리 없음 — LLM 오류 1번에 세션/워커 사망 | P0 | 구현됨 |
| R2 | 안정성 | WebSocket 재연결 없음 (offscreen.js) | P0 | 구현됨 |
| R3 | 안정성 | stop 시 finalize 큐 미처리 — 마지막 문장 final 소실 | P1 | 구현됨 |
| Q1 | 큐/지연 | 단계별 latency 계측 부재 — 최적화 판단 근거 없음 | P0 | 구현됨 |
| Q2 | 큐/지연 | partial 트랙이 오디오 수신 경로를 블로킹 | P1 | 대기 (Q1 실측 후) |
| Q3 | 큐/지연 | LLM 타임아웃 15s가 partial 인라인 경로에 그대로 적용 | P1 | 구현됨 |
| Q4 | 큐/지연 | 발화가 길어질수록 partial 재전사 비용 누적 (전체 버퍼 재전사) | P2 | 대기 (Q1 실측 후) |
| Q5 | 큐/지연 | GPU 자원 경쟁 — partial STT / final STT / LLM 동시 실행 | P2 | 대기 (Q1 실측 후) |
| Q6 | 큐/지연 | finalize 큐 backlog 열화 모드 없음 (깊이 로그는 Q1로 확보) | P2 | 대기 (Q1 실측 후) |
| Q7 | 큐/지연 | 연결마다 silero-VAD 모델 재로드 | P2 | 구현됨 |
| S1 | 전사 품질 | 선형보간 리샘플러의 앨리어싱 — 네이티브 리샘플로 교체 | P1 | 구현됨 |
| S2 | 전사 품질 | STT `previous_context` 배선이 준비만 되고 미사용 | P2 | 대기 (GPU A/B) |
| S3 | 전사 품질 | hard cap 강제 절단이 단어 중간을 자름 | P2 | 대기 (Q1 실측 후) |
| S4 | 전사 품질 | glossary 매칭이 표면형 exact substring | P2 | 구현됨 |
| S5 | 전사 품질/지연 | VAD_SILENCE_MS·FINALIZE_GRACE_MS 고정값 — 화자별 말 속도/간격 차이 미반영 | P2 | 구현됨 (2026-08-26, 라이브 검증) |
| S6 | 전사 품질 | hallucination gate가 무음 세그먼트를 조용히 드롭 — partial 폴백까지 같이 차단 | P1 | 발견됨 (2026-08-26, 미착수) |
| S7 | 전사 품질 | final(beam=5)이 partial(beam=1)보다 외래어/고유명사 표기에서 후퇴하는 사례 | P2 | 발견됨 (2026-08-26, 원인 불명) |
| T5 | 번역 품질 | 번역 모델이 화자 이름/닉네임을 임의로 다른 걸로 환각 | P1 | 발견됨 (2026-08-26, 미착수) |
| T1 | 번역 품질/속도 | glossary_hint가 system prompt에 붙어 KV prefix cache 무효화 | P1 | 구현됨 |
| T2 | 번역 품질 | 단어별 예외 노트 누적 구조의 확장성 한계 | P2 | 대기 (노트 증가 시) |
| T3 | 번역 품질 | repeat_penalty 1.3 상시 적용의 부작용 가능성 | P2 | 대기 (GPU A/B) |
| T4 | 번역 품질 | fast(부분) 번역에 문맥 없음 | P2 | 대기 (Q2 이후) |
| D1 | 문서 | 코드-문서 불일치 (PIPELINE.md 등) | P2 | 구현됨 |

(R4 — 시작 시 런타임 계약 자가진단 — 는 명세 단계에서 추가된 항목으로 `IMPROVEMENT_SPECS.md`에만
있음. 구현됨.)

---

## 안정성 (품질 이전에 세션이 죽는 문제)

### R1. 엔진 호출 예외 격리 없음 — 오류 1번에 트랙 전체 사망 (P0)

현재 STT/LLM 호출 어디에도 예외 처리가 없어, llama-server의 일시적 오류(타임아웃, 5xx,
재시작 순간의 connection refused) **한 번**이 복구 불가능한 상태를 만든다:

- **partial 경로**: `_emit_partial()`(`backend/audio_session.py:153`)에서 예외 발생 →
  `_process_frame` → `feed_audio` → `main.py`의 수신 루프까지 전파 → `finally`에서
  `session.close()` → **웹소켓 연결 자체가 끊긴다.** 확장 쪽에 재연결이 없으므로(R2)
  사용자가 수동으로 캡처를 재시작할 때까지 전체가 멈춘다.
- **final 경로**: `_finalize_worker()`(`backend/audio_session.py:203`)는 `try/finally`만 있고
  `except`가 없다. `_do_finalize` 안에서 예외가 나면 `while True` 루프를 뚫고 나가 **워커
  태스크가 조용히 죽는다.** 이후 세션이 끝날 때까지 모든 finalize가 큐에 쌓이기만 하고
  처리되지 않는다 — UI는 partial 상태로 영원히 멈추고, 에러 로그도 없다(태스크 예외 미회수).

**개선안**: 두 지점 모두 엔진 호출을 try/except로 감싸고 로그 후 계속 진행. partial은 해당
주기 스킵, final은 번역 실패 시 전사만 담은 final 이벤트라도 방출(빈 final 폴백과 같은
철학 — UI가 partial 상태에 멈추지 않게). 워커 루프는 어떤 예외에도 살아남아야 한다.

### R2. WebSocket 재연결 없음 (P0)

`extension/offscreen.js`의 `connectWebSocket()`은 `onerror`에서 로그만 남기고 `onclose`
핸들러가 없다. 백엔드가 재시작되면(개발 중 `--reload`로는 파일 저장만 해도 발생) 캡처와
오디오 그래프는 계속 도는데 전송만 조용히 끊긴다 — 사용자는 자막이 안 뜨는 것으로만 인지.

**개선안**: `onclose`에서 백오프를 두고 재연결(캡처 활성 상태인 동안만). 재연결 성공 시
`start_session`을 다시 보내면 백엔드는 어차피 연결 단위로 `AudioSession`을 새로 만들므로
서버 쪽 변경은 불필요하다.

### R3. stop 시 finalize 큐를 버림 — 세션 마지막 문장의 final이 안 나옴 (P1)

`AudioSession.close()`(`backend/audio_session.py:211`)는 워커를 즉시 `cancel()`한다.
사용자가 Stop을 누른 시점에 큐에 대기 중이거나 처리 중이던 finalize는 소실되고, 해당
세그먼트는 partial 상태로 로그에 남는다.

**개선안**: close 시 `queue.join()`(타임아웃 부여)으로 잔여 큐를 비운 뒤 cancel. 진행 중이던
미완 utterance까지 finalize할지는 선택(마지막 발화 도중 stop이면 버리는 게 자연스러울 수
있음 — 큐에 이미 들어간 것만 지키는 것부터).

**운영 노트 (2026-08-26)**: `llama-server`가 포트 8080에 **중복 프로세스로 2개** 뜬 채 한동안
공존한 적이 있음(원인 미확인 — `tray_launcher.ps1`을 직접 재실행한 적 없는데 언젠가 두 번째가
떴음). Windows에서 같은 포트에 리스너가 2개 있는 게 정상은 아니라, 번역 요청이 간헐적으로
느려지거나 실패하는 원인 중 하나였을 가능성이 있음(둘 중 하나로 요청이 비결정적으로 라우팅됐을
수 있음 — 확실히 검증은 못 함, 정리 후 증상이 없어진 것만 확인). 재발하면
`netstat -ano | findstr :8080`으로 중복 여부 먼저 확인할 것.

---

## 큐 처리 / 지연 최적화

### Q1. 단계별 latency 계측 부재 (P0 — 아래 모든 Q 항목의 선행 조건)

PRD 목표는 발화 후 1~2초인데, 현재 어느 단계에도 소요 시간 로그가 없다. partial STT / fast
LLM / final STT / final LLM / finalize 큐 대기 각각이 예산을 얼마나 쓰는지 모르는 상태로는
Q2~Q6 어느 것도 "실제로 문제인지" 판단할 수 없다.

**개선안**: 각 엔진 호출과 큐 대기를 `time.monotonic()`으로 감싸 INFO 로그 한 줄씩
(`partial stt=0.21s llm=0.34s buf=3.2s`, `final queue_wait=0.8s stt=1.1s llm=1.9s` 식).
코드 몇 줄, 별도 인프라 불필요. 이 로그가 쌓인 뒤에 Q2~Q6의 착수 여부를 정한다.

### Q2. partial 트랙이 오디오 수신 경로를 블로킹 (P1)

`docs/planning/PIPELINE.md`는 "partial 트랙은 메인 오디오 처리 루프 안에서 논블로킹으로 실행된다"고
서술하지만, 실제로는 `_process_frame`이 `await self._emit_partial()`로 **fast STT + fast LLM이
끝날 때까지 다음 프레임 처리(= 웹소켓 오디오 드레인)를 멈춘다.** 이벤트 루프를 안 막는다는
의미의 논블로킹일 뿐, finalize를 큐로 뺀 것과 정확히 같은 이유("feed_audio는 오디오를
드레인하는 유일한 경로")가 partial에도 적용된다. fast 패스 소요가 `PARTIAL_UPDATE_INTERVAL_S`
(0.6s)에 근접/초과하면 — 발화 후반부로 갈수록 버퍼가 길어져 STT가 느려지므로 실제로 일어날
수 있는 조건 — 오디오가 서버 버퍼에 적체되며 체감 지연이 누적된다.

**개선안**: Q1 계측으로 실제 소요를 확인한 뒤, 필요하면 partial도 백그라운드 태스크로 분리
("이전 partial이 아직 실행 중이면 이번 주기는 스킵" 규칙이면 큐도 필요 없음 — 최신 버퍼로
다음 주기에 다시 돌면 되므로). 분리 시 같은 세그먼트의 partial이 final보다 늦게 도착해 UI를
되돌리는 역전이 생기지 않도록, finalize가 enqueue된 세그먼트의 미완 partial 결과는 버릴 것.

### Q3. LLM 타임아웃 15s가 partial 인라인 경로에 그대로 적용 (P1)

`LLAMA_SERVER_TIMEOUT_S = 15.0`(`backend/config.py:79`) 하나가 fast/final 공용이다. Q2의
구조에서 fast LLM이 스톨하면 **최대 15초 동안 오디오 수신이 통째로 멈춘다** (그리고 타임아웃
발생 시 R1에 의해 세션이 죽는다).

**개선안**: fast 경로에 짧은 전용 타임아웃(예: 2~3s)을 분리. fast 번역은 어차피 다음 주기에
갱신되므로 빨리 포기하는 게 맞다. final은 15s 유지해도 무방(백그라운드 큐라서).

### Q4. 발화가 길어질수록 partial 재전사 비용 누적 (P2)

partial마다 utterance 버퍼 **전체**를 처음부터 재전사한다(`_emit_partial` →
`utterance.audio()`). 10초 발화면 0.6s 주기로 1s, 1.6s, …, 10s 길이를 반복 전사 — 발화당
총합이 길이의 제곱에 비례한다. `MAX_UTTERANCE_SECONDS = 10` 캡 덕에 상한은 있지만, 후반부
partial일수록 느려져 Q2의 블로킹 시간도 같이 커진다.

**개선안(계측 후)**: 마지막 N초 윈도우만 재전사하고 앞부분은 직전 partial 텍스트를 prefix로
이어붙이는 방식 검토. 단순하고 효과 있지만 경계 단어가 깨질 수 있으므로 품질 A/B 필요.
Q1 계측에서 후반부 partial STT가 0.6s를 실제로 넘는지 확인한 뒤에만 착수.

### Q5. GPU 자원 경쟁 (P2)

한 GPU(RTX 4080 SUPER 16GB) 위에서 partial STT(fast), final STT(beam=5), fast LLM, final
LLM이 서로 동기화 없이 동시 실행될 수 있다 (partial은 인라인, final은 별도 워커, LLM은
llama-server 4슬롯). 예: 긴 final 처리 중에 다음 발화의 partial이 돌면 둘 다 느려진다 —
partial 지연 스파이크가 "finalize 직후"에 몰릴 가능성.

**개선안(계측 후)**: Q1 로그에서 finalize 처리 중 partial 소요가 눈에 띄게 튀는지 확인.
문제라면 STT 호출을 단일 워커 스레드로 직렬화(partial 우선)하는 것부터. 추측으로 미리
직렬화하지 말 것 — 경쟁이 실측으로 문제일 때만.

### Q6. finalize 큐 무한 + 깊이 관측 없음 (P2)

`_finalize_queue`는 무한 큐이고 깊이를 어디서도 로그하지 않는다. final 1건 처리(beam=5 STT +
히스토리 포함 LLM)가 발화 생산 속도보다 느리면 — 쉬지 않고 말하는 화자 + 선제 분할이 겹치면
가능 — final이 라이브에서 점점 뒤처지는데 이를 감지할 방법이 없다.

**개선안**: Q1 계측에 `queue_depth` 포함(공짜). 실측으로 backlog가 확인되면 열화 모드 검토
(예: 깊이 ≥ 3이면 해당 건은 beam=1로 처리하거나 마지막 partial 전사를 재사용해 LLM만 호출).

### Q7. 연결마다 silero-VAD 모델 재로드 (P2)

`AudioSession.__init__` → `SileroVAD()` → `torch.hub.load(...)`가 **웹소켓 연결마다** 실행된다
(`backend/vad.py:29`). 디스크 캐시에서 읽지만 그래도 연결 시작이 수 초 단위로 느려질 수 있다.
또한 `SileroVAD.reset()`은 정의만 있고 호출처가 없다.

**개선안**: STT 엔진처럼 `main.py` startup에서 1회 로드해 세션에 주입하고, 세션 시작 시
`reset()` 호출(RNN 상태 초기화). 코드 몇 줄.

---

## 전사(STT) 품질

### S1. 선형보간 리샘플러의 앨리어싱 (P1 — 코드가 줄면서 품질이 오르는 항목)

`extension/offscreen.js`의 `resampleLinear`는 48kHz→16kHz 다운샘플 시 저역통과 필터가 없어
8kHz 이상 성분이 가청 대역으로 접혀 들어온다(앨리어싱). BGM/효과음이 섞인 라이브 스트림에서
STT 입력의 노이즈로 작용할 수 있다.

**개선안**: `new AudioContext({ sampleRate: 16000 })`으로 생성하면 Chrome이 미디어 스트림을
안티앨리어싱 필터 포함 고품질 리샘플러로 알아서 16kHz로 변환해준다 — `resampleLinear` 함수
자체를 삭제할 수 있다. 주의: 캡처 오디오를 같은 컨텍스트의 `destination`으로 재생하는 구조가
아니라 `<audio>` 요소로 재생 중이므로 재생 품질에는 영향 없음. 교체 후 실스트림에서 CER
악화가 없는지만 확인.

### S2. STT `previous_context` 배선이 준비만 되고 미사용 (P2)

`FasterWhisperEngine.transcribe`는 final 패스에서 `initial_prompt=previous_context`를 받도록
구현돼 있지만(`backend/stt/faster_whisper_engine.py:84`), `AudioSession._do_finalize`는 이
인자를 넘기지 않는다 — 사실상 죽은 배선. 직전 final 전사를 넘기면 분절 경계에서의 표기
일관성(같은 고유명사가 세그먼트마다 다르게 전사되는 문제, EVAL 패턴 B의 일부)에 도움이 될
수 있다.

**주의**: initial_prompt는 강한 prior라 환각 위험이 있다(glossary hotwords를 끈 것과 같은
이유 — `backend/main.py:41` 주석). 켜기 전에 EVAL 데이터셋으로 A/B 필수. 효과가 없거나
위험하면 인터페이스에서 인자를 제거해 죽은 배선을 정리하는 쪽으로 결론 내도 됨.

### S3. hard cap 강제 절단이 단어 중간을 자름 (P2)

`MAX_UTTERANCE_SECONDS` 도달 시 침묵 여부와 무관하게 그 프레임에서 즉시 자른다 — 단어/음절
중간이 잘리면 양쪽 세그먼트 모두 전사가 깨진다 (EVAL 패턴 D-1과 같은 증상이 hard cap에서도
발생). 선제 분할(`has_strong_sentence_boundary`)이 대부분의 run-on을 먼저 잘라주므로 빈도는
낮을 것으로 추정.

**개선안**: cap 도달 시 (a) 버퍼 내 마지막 무음 프레임 경계에서 자르고 나머지를 다음
utterance의 선두로 이월, 또는 (b) 다음 utterance에 직전 꼬리 0.3~0.5s를 오디오로 겹쳐 넣기
(overlap). (a)가 더 단순. 실로그에서 hard cap 발동 빈도를 먼저 확인(Q1 계측에 포함)한 뒤
빈도가 유의미할 때만.

### S4. glossary 매칭이 표면형 exact substring (P2)

`Glossary.match`(`backend/glossary.py:47`)는 `src in text` — STT가 등록 표기와 한 글자라도
다르게 전사하면(전각/반각, 장음 유무, 가나 변형) 매칭이 안 돼 hint가 안 나간다.

**개선안**: 양쪽 NFKC 정규화 후 매칭부터(한 줄). 가나 퍼지 매칭은 glossary가 실제로 커진
뒤에(현재 1항목) — 지금은 과설계.

### S5. VAD_SILENCE_MS·FINALIZE_GRACE_MS 고정값 — 화자별 말 속도/간격 차이 미반영 (P2, 2026-08-25 제안)

**계기**: 실캡처 세션(`data/sessions/`)을 리뷰하며 나온 아이디어. 지금 `VAD_SILENCE_MS = 600`,
`FINALIZE_GRACE_MS = 200`(`config.py`)은 모든 화자·모든 순간에 동일하게 적용되는 고정값이다.
그런데 사람마다 문장 사이 자연스러운 침묵 길이와 말 속도가 다르고, 같은 화자도 상황(노래/게임
집중/잡담)에 따라 리듬이 달라진다 — 고정 임계값은 말이 빠른 화자에겐 필요 이상으로 오래
기다리고(체감 지연 증가), 침묵이 긴 화자에겐 문장을 성급하게 잘라버릴 수 있다.

**주의 — 범위**: 이건 로드맵 2차 목표(화자 분리/다중 화자별로 다른 값 적용)가 아니다.
화자 구분 없이 **"지금 이 세션에서 관찰되는 리듬"에 세션 단위로 적응**시키는 것으로,
1차 목표(단일 화자) 범위 안에서 착수 가능. "화자 A는 600ms, 화자 B는 900ms"처럼 화자별로
다른 값을 유지하려면 화자 분리가 먼저 필요하므로 그건 로드맵 2차 목표와 함께 재검토.

**제안된 설계 방향 (사용자 승인, 2026-08-25 — 구현은 보류)**:
1. `AudioSession`에 최근 N개 finalize된 발화의 (a) 문장 종결 직전 실제 침묵 길이,
   (b) 말 속도(전사 글자수 ÷ 발화 지속시간) 롤링 통계를 추가.
2. 이 통계를 바탕으로 `VAD_SILENCE_MS`/`FINALIZE_GRACE_MS`를 세션 시작 시 고정값이 아니라
   EMA(지수이동평균)로 서서히 조정 — 매 발화마다 급격히 바뀌지 않게 변화 속도(EMA 계수)를
   작게 유지.
3. **안전장치(필수, 사용자 지적)**:
   - 임계값에 상한/하한을 둬서 무한정 늘어나거나(반응 지연 폭주) 너무 짧아지지(문장 조기
     절단 폭증) 않게 clamp.
   - 노래/BGM 등 비정상적으로 긴 침묵 한두 번에 통계가 흔들리지 않도록, 이상치(outlier)
     — 예: `MAX_UTTERANCE_SECONDS`에 근접한 극단값 — 는 롤링 통계 계산에서 제외하거나
     가중치를 낮춤.
   - 세션 초반(통계 샘플 부족 구간)은 현재 고정값을 그대로 사용하고, 표본이 충분히 쌓인
     뒤부터 적응 시작.
4. 효과 측정: 적응형 on/off 각각 실캡처로 (a) 체감 지연(특히 말 빠른 화자에서 finalize까지
   걸리는 시간), (b) 문장 조기 절단 빈도(S3의 hard_cap 트리거 로그, 또는 `grace_expired` 사유
   빈도)를 비교.

**착수 조건**: 아직 미확정 — 사용자 지시로 착수 시점 결정. 착수한다면 Q1 계측(대기/트리거
사유 로그)이 이미 있어 효과 측정 인프라는 추가 비용 없이 재사용 가능.
**의존**: 없음(Q1 계측 인프라는 이미 구현됨).

**구현 현황 (2026-08-26)**: 설계대로 구현·라이브 검증됨. `AudioSession._last_speech_at`으로
발화 간 실제 침묵 gap을(우리 임계값과 무관하게) 직접 측정, `_record_pause_sample`/
`_record_rate_sample`이 EMA 갱신(`config.ADAPTIVE_VAD_EMA_ALPHA = 0.2`), 표본 5개
(`ADAPTIVE_VAD_MIN_SAMPLES`) 미만이면 고정값 사용. `_effective_silence_ms`/`_effective_grace_ms`가
매 finalize 판정에 실제 사용됨 — 로그(`finalize trigger=... eff_silence_ms=... eff_grace_ms=...`)로
600ms 고정값이 세션 중 720→1200ms(상한 clamp)까지 실측 기반으로 올라가는 것 확인. 안전장치(상하한
clamp, 이상치 배제, 표본 부족 시 고정값)도 설계대로 동작. **미해결**: 상한(1200ms)/이상치 임계값
(8000ms) 자체는 감이지 실측 보정은 아직 안 됨 — 세션이 더 쌓이면 재검토.

---

### S6. hallucination gate가 무음 세그먼트를 조용히 드롭 — partial 폴백까지 같이 막힘 (P1, 2026-08-26 발견)

**증상**: 전체 final의 약 4~5%에서, partial(beam=1)은 정상적으로 문장을 잡았는데
(`おやすみなさい`, `またね` 같은 흔한 짧은 인사말 다수) final이 완전히 빈 텍스트로 나옴.
`backend_run.err.log`에 대응하는 "STT dropped segment" 로그도 없고, 예외(`final STT failed`)도
없음 — 조용히 사라짐.

**원인 (코드 추적으로 확인)**: `backend/stt/faster_whisper_engine.py`의 beam=5 재전사가 아주
짧고 조용한 오디오를 세그먼트 하나로 묶어 "사실상 무음"(`no_speech_prob` HARD 임계값 초과)이라고
판단하면 `should_drop=True`, `dropped_low_confidence=True`로 설정되는데, 이때 `stripped`(세그먼트
텍스트)가 빈 문자열이라 `if stripped: logger.info(...)` 조건에 걸려 **로그 자체가 안 남는다**.
동시에 `kept_no_speech_probs`/`kept_avg_logprobs`가 비어서 `TranscriptionResult`의
`no_speech_prob`/`avg_logprob`도 `None`이 됨. `audio_session.py`의 폴백 로직
(`if not final_text and not dropped_low_confidence: final_text = utterance.last_partial_text`,
2026-08-19 `9db825f`에서 추가)은 `dropped_low_confidence=True`일 때 **의도적으로** 폴백을
건너뛴다 — 2026-08-19에 "환각으로 명시적으로 거부된 걸 partial로 되살리면 안 된다"(`ご視聴あり
がとうございました` 재발 사례)는 이유로 만든 안전장치인데, 그물이 너무 넓어서 진짜 정상적인
짧은 발화까지 같이 걸러내고 있다.

**개선 방향(미착수, 논의만)**: `dropped_low_confidence`를 "명시적으로 알려진 환각과 매칭됨"과
"단순히 no_speech_prob이 높아서 던짐"을 구분하는 두 상태로 나누고, 후자는 폴백을 허용하는 쪽으로
좁혀볼 수 있음 — 다만 정확한 재현/영향 범위는 `data/sessions/`의 partial-있음·final-없음 세그먼트를
더 모아서 확인 필요.

**착수 조건**: 미정 — 사용자 지시 대기.
**의존**: 없음.

### S7. final(beam=5)이 partial(beam=1)보다 외래어/고유명사 표기에서 후퇴하는 사례 (P2, 2026-08-26 발견)

**증상**: 한 발화에서 partial 재전사가 진행되며 `サーティンスライブ`(가타카나)를 어느 시점에
`13th live`(알파벳)로 정확히 옮겼는데, 그 다음 final(beam=5) 재전사는 다시 `サーティンスライブ`
가타카나로 후퇴함 (`data/sessions/20260826_001535_..._cb09f5.jsonl`, segment
`77be92190d8946d2950442f4a529874b`). beam이 클수록 항상 더 정확한 게 아니라는 실증 사례 —
빔 크기에 따라 다른 지역 최적해로 수렴하는 Whisper의 알려진 특성으로 추정되며, 파라미터
튜닝만으로 일반적으로 고치기 어려움.

**개선 방향(미착수, 아이디어만)**: partial 트랙에서 특정 구간(숫자+영단어 조합처럼 스크립트가
전환되는 지점)이 이미 신뢰도 높게 나왔다면, final이 그 표기를 우선하도록 힌트를 주는 방법이
있을 수 있으나 구체적 설계는 안 함.

**착수 조건**: 미정 — 사용자 지시 대기.
**의존**: 없음.

---

## 번역 품질 / 속도

### T1. glossary_hint가 system prompt에 붙어 KV prefix cache 무효화 (P1)

`LlamaServerEngine.translate`(`backend/translation/llama_server_engine.py:253`)는 hint가 있으면
system prompt 끝에 이어붙인다. llama-server는 슬롯별로 프롬프트 prefix KV cache를 재사용하는데,
system prompt가 요청마다 달라지면 그 긴 프롬프트(final은 노트 8개 연결로 상당히 길다)를 매번
처음부터 다시 처리한다 — hint가 자주 붙는 세션일수록 TTFT가 불필요하게 늘어난다.

**개선안**: hint를 user message 쪽(`[TEXT TO TRANSLATE]` 위에 별도 섹션)으로 옮겨 system
prompt를 상수로 고정. 품질 영향이 있는지만 짧게 A/B(지시 추종 위치 민감성).

### T2. 단어별 예외 노트 누적 구조의 확장성 한계 (P2)

`_SLANG_NOTE`/`_CONNOTATION_NOTE`/`_FALSE_FRIEND_NOTE` 등에 개별 단어 사례(ずるい, やばい,
嘘, キンキン, ガーッチ…)가 프롬프트 본문으로 계속 쌓이는 구조다. 사례가 늘수록: (1) 프롬프트가
길어져 처리 비용 증가, (2) 12B 모델의 지시 추종이 흐려져 기존 사례의 준수율이 떨어질 위험,
(3) 어떤 노트가 실제로 효과 있는지 회귀 검증 불가.

**개선안(방향 제시만)**: 단어별 사례를 프롬프트 상수에서 데이터 파일(예:
`translation_notes.json`)로 분리해 glossary처럼 "입력에 해당 단어가 있을 때만" 주입하는 구조
검토 — 프롬프트 길이를 입력에 비례하게 유지. 사례가 계속 늘어나는 게 확인되면 착수하고,
그 전까지는 현행 유지(지금 당장은 동작하는 구조를 흔들 이유 없음). 노트 하나를 추가할 때마다
EVAL 최소 재현 세트로 기존 사례 회귀 확인하는 습관은 지금부터라도.

### T3. repeat_penalty 1.3 상시 적용의 부작용 가능성 (P2)

grammar 궁지 몰림 대응으로 넣은 `repeat_penalty: 1.3`(꽤 강한 값)이 모든 요청에 적용된다.
greedy(temperature 0)와 결합하면 정당한 반복 — ㅋㅋㅋ 스케일링(w 개수 반영 지침과 충돌),
"정말 정말" 같은 강조 반복, 같은 조사/어미의 자연스러운 반복 — 을 억제해 어색한 출력을 만들
수 있다.

**개선안**: EVAL 세트에서 repeat_penalty on/off로 chrF++ 및 반복 표현 케이스 육안 비교.
붕괴(단일 문자 반복 벽)는 드문 케이스이므로 "기본 off + 출력에서 반복 붕괴를 감지하면
penalty 켜고 1회 재시도"가 더 정확한 구조일 수 있다 — 단 재시도는 지연 비용이 있으므로
final 경로에만.

### T4. fast(부분) 번역에 문맥 없음 (P2)

fast 패스는 문맥 없이 조각만 번역한다. 직전 final 한국어 1문장만 fast 프롬프트에 얹어도
지시어/생략 주어/존댓말 톤이 partial 단계부터 이어질 수 있다 (final은 이미 3쌍 히스토리를
받는 것과 대비). 토큰 비용은 작지만 fast 지연에 민감(Q2·Q3)하므로, partial 트랙 분리(Q2)
이후에 시도하는 게 안전하다.

### T5. 번역 모델이 화자 이름/닉네임을 임의로 다른 걸로 환각 (P1, 2026-08-26 발견)

**증상**: 원문 `僕が仲町あられとして`(제가 나카마치 아라레로서)가 `제가 릿짱으로서`로 번역됨
— "릿짱"은 원문 어디에도 없는, 모델이 지어낸 완전히 다른 닉네임
(`data/sessions/20260826_001535_..._cb09f5.jsonl`, segment `77be92190d8946d2950442f4a529874b`).
`[BROADCASTER]` 힌트(`backend/audio_session.py::_broadcaster_hint`, 채널명 스크래핑 기반, 2026-08-25
추가)가 있는데도 발생 — 힌트가 실제로 프롬프트에 반영이 안 됐는지, 아니면 힌트와 무관하게
모델이 학습 데이터의 다른 닉네임을 끌어온 것인지 원인 미확인. STT 오류(전사 자체는
`仲町あられ`로 정확)와 무관한, **번역 단계에서만 발생한 환각**이라는 점에서 S6/S7보다 더
직접적으로 부정확한 결과를 만든다.

**개선 방향(미착수, 아이디어만)**: `[BROADCASTER]` 힌트가 실제 llama-server 요청에 어떻게
꽂히는지(`llama_server_engine.py::translate`) 재확인, 화자 이름이 원문에 명시적으로 등장할 때
그 표기를 그대로 쓰도록(글로서리의 라틴 타깃 강제와 비슷한 방식) 강제하는 방안 검토.

**착수 조건**: 미정 — 사용자 지시 대기.
**의존**: 없음.

---

## 문서/코드 불일치 (발견 시 정리)

- `docs/planning/PIPELINE.md`: "partial 트랙은 …논블로킹으로 실행된다" — 실제로는 오디오 드레인 경로를
  블로킹한다(Q2). Q2 처리 여부와 무관하게 서술은 정정 필요.
- `backend/main.py:7` docstring의 llama-server 실행 예시가 Qwen2.5 모델 파일 — `config.py`는
  gemma-3-12b-it 기준. 헤더 커맨드 갱신.
- `backend/glossary.py` 모듈 docstring: "every glossary source term is fed to faster-whisper as
  an initial_prompt vocabulary hint" — `main.py:41`에서 의도적으로 배선을 끊었으므로(환각 이슈)
  현행과 다름. docstring 갱신.
- `backend/stt/base.py` `TranscriptionResult.words` — `word_timestamps=False`라 항상 빈 값.
  쓸 계획이 없으면 제거, 있으면 주석으로 명시.

## 노래/음악 구간 처리 (로드맵 3순위 — 2026-08-25 조사, 착수 보류)

**주의**: 이 섹션은 로드맵 3번째 항목(노래/음악 환경 품질 개선)에 속한다. Goal priority(1차
목표: 1인 화자 일반 발화)가 아직 완료 전이므로 **오늘은 착수하지 않고 조사/설계만 문서화**한다
— 착수 시점은 1차 목표 완료 후로 사용자가 별도 판단.

### M1. 조건부 Demucs 보컬 분리로 노래 구간 전사/번역 품질 개선

**계기**: `data/sessions/20260824_232824_tab1206683466_02acbc.jsonl` 실캡처 세션(노노카 어쿠스틱
기타 탄영 방송)을 리뷰한 결과, 노래+기타 구간에서 final 512건 중 23.4%가 빈 텍스트로
드롭되고, 저신뢰(avg_logprob < -0.9) final이 31건 발생. 대표 사례:
`"あうんうんうんうんうんうんを"` → `"아, 음~ 나나나가~!"`(logprob -1.54) 처럼 STT가 의미
없는 음절을 뽑아내고 번역 모델이 그걸 자연스러운 감탄사로 그럴듯하게 포장하는 패턴 — 원문이
이미 깨졌으므로 번역 개선만으론 못 잡는다. 순수 반주(보컬 없음) 구간은 VAD가 애초에
"speech 아님"으로 판단해 이벤트 자체가 없음(로그에 40~90초 공백)도 확인.

**조사 결과 (2026-08-25, WebSearch)**:
- Demucs(htdemucs) 2-stem(`--two-stems=vocals`) 보컬 분리 후 Whisper에 넣으면 가사 전사
  정확도가 **개선**된다는 최신 보고 있음 — 사용자가 기억하는 "Demucs 물리면 전사가 더
  나빠진다"와 반대 결과. 단, 해당 보고는 오프라인/배치 가사전사 태스크 기준이라 실시간
  파이프라인 적용 가능성을 그대로 보장하진 않음. 사용자의 과거 경험이 어떤 상황이었는지는
  미확인 — 착수 시 먼저 재확인.
  ([Exploiting Music Source Separation for Automatic Lyrics Transcription with Whisper](https://arxiv.org/pdf/2506.15514))
- ASR 신뢰도 신호를 번역 프롬프트에 넘겨 "낮으면 직역/보수적으로" 유도하는 접근은 실무에서
  이미 쓰이는 패턴(confidence-aware prompting). 단 Whisper 환각은 신뢰도 자체가 높게 나오는
  경우도 흔해 신뢰도만으론 완전히 못 거른다는 한계가 여러 논문에서 공통 지적됨.
  ([Towards interfacing LLMs with ASR systems using confidence measures and prompting](https://arxiv.org/html/2407.21414))
- Whisper-UT(2025): 번역 모델이 불완전한 전사를 "조건부로 불신"하도록 학습(ASR 노이즈를 섞어
  파인튜닝)하는 프레임워크 — 우리 아이디어(신뢰도 신호 → 보수적 번역 유도)의 더 정교한
  버전이지만 파인튜닝이 필요해 현재 스택(프롬프트 only) 범위 밖.
  ([Whisper-UT: A Unified Translation Framework for Speech and Text](https://arxiv.org/pdf/2509.16375))
- Demucs GPU 처리 속도: RTX 3060 Ti 기준 오디오 1초당 약 0.04초(RTF~25배), 최적화 없는 일반
  GPU 파이프라인은 오디오 1초당 약 0.14초(RTF~7배), RTX 3090+TensorRT 최적화는 오디오 1초당
  약 0.015초(RTF~60배 이상). htdemucs는 내부적으로 ~7.8초 단위로 청크 처리하므로 어테런스
  길이(1~10초)와 무관하게 청크 1개 처리 비용이 거의 고정으로 깔린다.
  ([Demucs Apple Silicon 포팅기](https://medium.com/@andradeolivier/i-ported-demucs-to-apple-silicon-it-separates-a-7-minute-song-in-12-seconds-6c4e5cffb5c3),
  [Stemuc Audio Forge 벤치마크](https://www.researchgate.net/publication/396205016_Stemuc_Audio_Forge_AI-based_Music_Source_Separation_Using_Demucs_and_CUDA_Acceleration),
  [htdemucs vs BS-RoFormer vs Spleeter 2026 벤치마크](https://aistemsplitter.org/blog/htdemucs-vs-bs-roformer-vs-spleeter-2026-benchmark))

**합의된 설계 방향 (사용자 승인, 2026-08-25 — 구현은 보류)**:
1. 모든 final이 아니라 **음악/저신뢰도 의심 구간에서만 조건부 트리거**. 트리거 조건 후보:
   `music_gate`(현재 `MUSIC_GATE_ENABLED = False`로 꺼져 있음 — 재보정 필요, `config.py` 참고)
   신호, 또는 STT 1차 결과의 `avg_logprob`/`no_speech_prob`가 임계값을 넘을 때.
2. 트리거되면 `_do_finalize`의 STT **이전**에 Demucs 2-stem으로 보컬만 분리한 오디오를
   생성해 그걸로 beam=5 재전사 — Demucs가 STT보다 뒤에 오면 의미가 없다(분리 후 재전사해야
   효과가 있음).
3. 예상 지연: 청크당 대략 0.3~1.5초 추가(모델 상주 로드 가정, TensorRT 미적용 기준) —
   `_finalize_worker` 백그라운드 큐에서 도는 경로라 오디오 수신 자체는 안 막히지만, 트리거된
   세그먼트의 final 표시 지연은 그만큼 늘어난다. 조건부 트리거이므로 일반 발화의 지연에는
   영향 없음.
4. **1차 검증 후에만** TensorRT 최적화 착수(품질 개선 효과가 실측으로 확인된 뒤 — 엔지니어링
   비용을 먼저 쓰지 않는다).
5. UI: 이 트리거가 걸린 구간(=노래/저신뢰 구간 판정)에는 "🎵 노래 중" 같은 플레이스홀더
   표시를 얹으면 사용자 경험이 낫다는 아이디어도 나왔음 — 로드맵 4(UI 설계)와 맞물리는
   부분이라 M1과 별도 항목으로 UI 단계에서 재검토.

**착수 조건**: 로드맵 1차 목표(단일 화자 일반 발화 품질) 완료 후, 사용자 지시로 시작.
**의존**: `music_gate.py` 재보정(또는 `avg_logprob`/`no_speech_prob` 임계값 기반 트리거로
대체), GPU에 Demucs 모델 로드 여유 확인(현재 large-v3-turbo + gemma-3-12b-it와 VRAM 공존).

**진행 상황 업데이트 (2026-08-26, 사용자 지시로 로드맵 순서 앞당겨 조기 실험 — 현재 다시 보류)**:

`music_gate.py`의 목적 자체를 "배경음악 검출"에서 **"화자 본인이 노래 중인지 검출"**로 바꿔서
실제로 두 번 다시 설계·구현·라이브 검증까지 했다. 순서대로:

1. **v1 (3~5.5Hz 리듬 변조 FFT, 기존 방식 그대로)**: 라이브에서 가사 있는 노래를 거의 못 잡음
   (노래도 말소리와 비슷한 음절 리듬을 가짐) + 세션 전체 롤링 윈도우를 비동기로 늦게 읽어서
   판정이 엉뚱한 시점(노래 끝난 뒤 정상 대화)에 붙는 타이밍 버그 발견. 폐기.
2. **v2 (발화별 피치(F0) 자기상관 추적 + 세션 적응형 기준선)**: 발화가 끝나는 순간 그 발화
   자신의 오디오에서 직접 피치를 뽑아 타이밍 버그를 구조적으로 없앰. 그런데 실측 결과 평범한
   대화도 자기상관 특유의 옥타브 점프 노이즈 때문에 "피치 범위" 13~18반음까지 튀는 걸 확인 —
   중앙값 필터(5프레임)로 완화했지만, 그래도 임계값을 몇 차례 재보정해야 했음
   (`ADAPTIVE_SINGING_BOOTSTRAP_RANGE_MAX_SEMITONES`/`FIXED_SINGING_RANGE_SEMITONES` 히스토리는
   `backend/config.py` 주석 참고).
3. **Demucs(htdemucs) 보컬 분리를 피치 추적 앞단에 추가**: 배경음악이 목소리와 섞여 피치 추적을
   오염시키는 문제(원래 M1이 다루려던 문제 그 자체)를 실측으로 확인하고 해결 — 라이브에서
   배경음악 아래 정상 대화를 더 이상 노래로 오판하지 않고, 실제 노래도 여러 건 정확히 잡음
   (`range_st=14.7~15.6` 등, 기준선 대비 명확히 튀는 구간). 웜업 후 처리 비용도 발화 길이 무관
   ~100-200ms로 확인 (콜드 스타트 2.4초는 CUDA 커널 컴파일 1회 비용이었을 뿐).
4. **그런데도 지금은 비활성화(`config.SINGING_DETECTION_ENABLED = False`, `music_gate.py`의
   Demucs 로딩부는 주석 처리)**: `demucs.pretrained.get_model()`의 HuggingFace Hub 최신성 확인
   네트워크 호출이 세션 중 한 번 멈췄고, 이게 FastAPI `startup()` 이벤트 안에서 실행되다 보니
   **앱 시작 자체가 막혀** 전사/번역이 통째로 죽었음(웹소켓 연결 자체를 안 받음) — 파일은
   이미 로컬에 완전히 캐시돼 있었는데도(`HF_HUB_OFFLINE=1`로 우회 성공) 발생. 원인이 분리
   로직 자체가 아니라 네트워크 호출 위치라 고칠 수는 있지만(다음 항목 참고), 그 시점엔 이미
   피치 감지 자체의 recall도 라이브에서 계속 들쭉날쭉(잡았다 놓쳤다)해서 안정성 리스크 대비
   가치가 애매하다고 판단, 기능 전체를 보류.

**재개 시 참고할 것**:
- Demucs 복원: `music_gate.py::MusicGate.warmup()`의 주석 처리된 블록을 되살리고, `get_model()`이
  네트워크를 절대 안 타게 만들어야 함 — `HF_HUB_OFFLINE=1`을 launcher 스크립트에 상시 박아두거나,
  가중치를 리포에 벤더링.
- 피치 판정 자체(옥타브 점프, 임계값)는 여전히 라이브 튜닝이 더 필요한 상태 — 사용자는 이번
  세션 끝에 "새 브랜치에서 파이프라인을 바닐라로 되돌리고 다시 보강하겠다"고 결정함. 이 섹션과
  S5/S6/S7/T5가 그 재작업의 출발점 자료.

---

## 기존 계획 추적 (`../eval/EVAL_REPORT_2026-08-18.md` §5 / gemma 리포트 §5)

| 항목 | 상태 (2026-08-19) |
|---|---|
| A. 오디오 게인 정규화 | **미구현** — `audio_session.py`에 정규화 없음. 여전히 최우선(S1의 82.7%가 STT 전파) |
| A-2. VAD/no_speech 불일치 재시도 | 미구현 (no_speech+avg_logprob 이중 조건으로 완화는 반영됨) |
| B. glossary 채우기 | **사실상 미착수** — 현재 1항목(`ティーワイ`)뿐. マッターホルン/白馬岳/千恵子 등 시드 미등록 |
| C. 번역 모델 교체 | **완료** — Gemma-3-12b-it 채택. Qwen3-14B(벤치 1위)는 grammar 비호환 미해결로 보류 중 |
| D-1. finalize grace/디바운스 | **완료** — `FINALIZE_GRACE_MS` + `looks_complete` 구현됨 |
| D-2. 직전 번역문 context 전달 | **완료** — `_final_history` 3쌍 + `_CONTINUITY_NOTE` 구현됨 |
| D-2-3. 정정(correction) 이벤트 | 미착수 — UI 단계(로드맵 4)에서 프로토콜 설계 시 반영 예정 |
| E. 장기 맥락 요약 메모리 | 미착수 — 단기 3쌍의 효과 확인 후 판단 |
