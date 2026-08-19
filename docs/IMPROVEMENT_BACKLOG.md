# 개선 백로그 — 코드 리뷰 기반 (작성: 2026-08-19)

전사/번역 품질 향상 작업 중 코드 전체를 리뷰하며 찾은 개선 후보 목록.
**각 항목의 구체적 구현 방법은 `docs/IMPROVEMENT_SPECS.md` 참고** (항목 ID 동일).
`docs/EVAL_REPORT_2026-08-18.md` §5의 기존 개선 계획(A~E)과 겹치는 항목은 여기 다시 쓰지 않고
맨 아래 "기존 계획 추적" 표에서 상태만 관리한다. 항목이 처리되면 이 문서에서 상태를 갱신할 것.

우선순위 기준:
- **P0** — 품질 이전의 문제. 실사용 중 세션이 통째로 죽거나, 이후 모든 최적화 판단의 전제가 되는 것.
- **P1** — 지연/품질에 직접적인 영향이 있고 구현 비용이 낮은 것.
- **P2** — 효과가 있을 것으로 보이나 계측/실험으로 확인이 먼저 필요한 것.

## 요약

| ID | 분류 | 항목 | 우선순위 |
|---|---|---|---|
| R1 | 안정성 | 엔진 호출 예외 격리 없음 — LLM 오류 1번에 세션/워커 사망 | P0 |
| R2 | 안정성 | WebSocket 재연결 없음 (offscreen.js) | P0 |
| R3 | 안정성 | stop 시 finalize 큐 미처리 — 마지막 문장 final 소실 | P1 |
| Q1 | 큐/지연 | 단계별 latency 계측 부재 — 최적화 판단 근거 없음 | P0 |
| Q2 | 큐/지연 | partial 트랙이 오디오 수신 경로를 블로킹 (문서와 불일치) | P1 |
| Q3 | 큐/지연 | LLM 타임아웃 15s가 partial 인라인 경로에 그대로 적용 | P1 |
| Q4 | 큐/지연 | 발화가 길어질수록 partial 재전사 비용 누적 (전체 버퍼 재전사) | P2 |
| Q5 | 큐/지연 | GPU 자원 경쟁 — partial STT / final STT / LLM 동시 실행 | P2 |
| Q6 | 큐/지연 | finalize 큐 무한 + 깊이 관측 없음 | P2 |
| Q7 | 큐/지연 | 연결마다 silero-VAD 모델 재로드 | P2 |
| S1 | 전사 품질 | 선형보간 리샘플러의 앨리어싱 — 네이티브 리샘플로 교체 | P1 |
| S2 | 전사 품질 | STT `previous_context` 배선이 준비만 되고 미사용 | P2 |
| S3 | 전사 품질 | hard cap 강제 절단이 단어 중간을 자름 | P2 |
| S4 | 전사 품질 | glossary 매칭이 표면형 exact substring | P2 |
| T1 | 번역 품질/속도 | glossary_hint가 system prompt에 붙어 KV prefix cache 무효화 | P1 |
| T2 | 번역 품질 | 단어별 예외 노트 누적 구조의 확장성 한계 | P2 |
| T3 | 번역 품질 | repeat_penalty 1.3 상시 적용의 부작용 가능성 | P2 |
| T4 | 번역 품질 | fast(부분) 번역에 문맥 없음 | P2 |
| D1 | 문서 | PIPELINE.md "논블로킹" 서술 등 코드-문서 불일치 | P2 |

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

`docs/PIPELINE.md`는 "partial 트랙은 메인 오디오 처리 루프 안에서 논블로킹으로 실행된다"고
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

---

## 문서/코드 불일치 (발견 시 정리)

- `docs/PIPELINE.md`: "partial 트랙은 …논블로킹으로 실행된다" — 실제로는 오디오 드레인 경로를
  블로킹한다(Q2). Q2 처리 여부와 무관하게 서술은 정정 필요.
- `backend/main.py:7` docstring의 llama-server 실행 예시가 Qwen2.5 모델 파일 — `config.py`는
  gemma-3-12b-it 기준. 헤더 커맨드 갱신.
- `backend/glossary.py` 모듈 docstring: "every glossary source term is fed to faster-whisper as
  an initial_prompt vocabulary hint" — `main.py:41`에서 의도적으로 배선을 끊었으므로(환각 이슈)
  현행과 다름. docstring 갱신.
- `backend/stt/base.py` `TranscriptionResult.words` — `word_timestamps=False`라 항상 빈 값.
  쓸 계획이 없으면 제거, 있으면 주석으로 명시.

## 기존 계획 추적 (`EVAL_REPORT_2026-08-18.md` §5 / gemma 리포트 §5)

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
