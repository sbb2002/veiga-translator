# 작업 핸드오프 (2026-08-22 기준 갱신)

세션 간 인수인계 문서. 다른 로컬 세션(또는 다른 머신)에서 이어 작업할 때 여기서부터 시작할 것.
**이번 갱신에서 가장 중요한 사실**: STT가 `large-v3`에서 `large-v3-turbo`로 바뀌었고(정식
정량/정성 벤치마크 근거 있음), 번역 모델 벤치마크 기록도 `research/`로 이관됐고, 로컬 서버
실행 방식이 콘솔 창 2개 → 트레이 아이콘 1개로 바뀌었다. `CLAUDE.md` 로드맵 4번(UI 설계 및
구현)은 2026-08-20에 이미 완료됨. 아래 "2026-08-22 세션에서 바뀐 것", "브랜치 지도", "남은
작업"이 지금 상태를 가장 정확히 반영한다.

## 2026-08-22 세션에서 바뀐 것 — 요약

**1) STT 모델을 `large-v3-turbo`로 교체 (정식 벤치마크 근거)**
- `research/topic/20260822_stt_transcription_eval/`에 `large-v3` vs
  `kotoba-tech/kotoba-whisper-v2.0-faster` vs `large-v3-turbo` 3-way 정량(CER/chrF++/
  BLEU/ROUGE-L)+정성(LLM 채점) 비교를 처음으로 정식 실행(`data/wav`+`data/json` 150쌍,
  앱 파이프라인 미경유, STT 모델 단독 호출). large-v3-turbo가 품질은 large-v3와 사실상
  동급이면서 처리 시간은 1/11.4 — `backend/config.py`의 `WHISPER_MODEL_SIZE`에 반영,
  `CLAUDE.md`/`README.md`도 갱신.
- 같은 김에 `docs/eval/`에만 있던 기존 번역 모델 벤치마크(Qwen2.5-7B 베이스라인 vs
  Qwen3-14B vs Gemma-3-12b-it vs EXAONE-3.5-7.8B, 2026-08-18 작업분)도 재실험 없이
  `research/topic/20260818_translation_model_benchmark/`로 소급 이관해 같은 형식으로
  정리. 결론: Qwen3-14B가 순수 품질 1위지만 GBNF grammar와 비호환이라 미채택 상태 유지,
  32B급 후보(Qwen3-32B 등)는 여전히 미벤치마크. **사용자가 두 대안 모두 명시적으로
  보류** — 다음은 모델 교체가 아니라 파라미터/프롬프트 튜닝(`docs/planning/
  IMPROVEMENT_BACKLOG.md` T2/T3/T4) 차례.
- `research/README.md`에 이 저장소의 research 폴더 컨벤션(주제별 폴더 + 5절 보고서 양식,
  `bandori-playlist-maker`의 research 브랜치 기록법 벤치마크)이 새로 정의됨.

**2) `start.cmd` — 콘솔 창 2개 → 트레이 아이콘 1개**
- `tray_launcher.ps1`이 llama-server + uvicorn backend를 창 없이 띄우고 하나의
  NotifyIcon(우클릭: 로그 열기/종료)으로 묶어서 관리.
- `start.cmd`는 이제 `wscript.exe`로 `tray_launcher.vbs`를 거쳐 위 스크립트를 실행 —
  cmd의 `start` + `powershell -WindowStyle Hidden` 조합이 이 머신에서 자식 프로세스를
  몇 초~수십 초 안에 조용히 죽이는 문제가 있어서(원인 미확정, VBScript의
  `WScript.Shell.Run`으로 우회) 이렇게 됨. cmd 배치파일에 non-ASCII 문자(예: em-dash)를
  쓰면 명령어 파싱이 깨지는 것도 이번에 확인(`'M'은 인식할 수 없는 명령입니다` 류 에러) —
  `.cmd` 파일은 항상 순수 ASCII로 유지할 것.
- `tray_launcher.ps1`은 한글 텍스트(트레이 메뉴)를 포함하므로 **UTF-8 BOM 필수**
  (Windows PowerShell 5.1이 BOM 없으면 시스템 코드페이지로 오독해 한글이 깨짐).

**3) 오버레이 컨텍스트 요약에 marquee 효과**
- `extension/popup.js`/`popup.html` — 요약 텍스트가 헤더 너비를 넘치면(기존엔 ellipsis로
  잘림) 텍스트를 복제한 트랙을 만들어 왼쪽으로 무한 스크롤(seamless loop, `-50%`
  transform). 안 넘칠 땐 기존과 동일.

## 브랜치 지도

```
main                     — 오래됨, 최근 작업 미반영 (병합 안 함, 그대로 둘 것)
  └─ batch1-instrumentation  — 백엔드 트렁크. 1차 목표(단일 화자) 품질 마일스톤 달성.
       ├─ multi-speaker      — 2차 목표(다중 화자) 1차 구현. 겹치는 발화 문제로 보류.
       │                       ⚠ multi-tab-capture에 병합 안 됨 — 아래 참고.
       └─ ui-chat-reply
            └─ multi-tab-capture — ⭐ 지금 작업의 기준 브랜치. UI 완성 + 멀티탭 지원 +
                                    오늘(2026-08-20) 오버레이 패널로 전면 재작업, 다수의
                                    실사용 버그 수정, 프롬프트 리팩터링까지 반영됨.
```

**브랜치 계승 확인 결과 (2026-08-20, `git merge-base --is-ancestor`로 검증)**:
- `ui-chat-reply`, `batch1-instrumentation` → **코드상 전부 `multi-tab-capture`에 포함됨**
  (batch1-instrumentation은 문서 전용 커밋 하나만 차이남, 실제 코드 차이 없음).
- `multi-speaker` → **포함 안 됨.** 화자 라벨링 코드(`backend/speaker_id/` 등, 커밋
  `1d74c74`)가 `multi-tab-capture`에는 없다. 2차 목표를 재개할 때는 `multi-speaker`
  브랜치에서 별도로 이어가거나 리베이스/병합이 필요함.
- 브랜치 삭제는 아직 하지 않음(사용자 명시적 보류 지시) — 정리는 나중에.

## multi-tab-capture — 지금 상태: UI 단계 완료

`CLAUDE.md` 로드맵 4번(UI)이 완료됐다. 오버레이 패널이 최종 형태이며, 아래 "확정된
설계"에 있는 예전 결정들(별도 창, 오버레이 자막 없음 등)은 **오늘 자로 뒤집혔거나
구현이 끝났다** — 최신 상태만 신뢰할 것.

### 오늘(2026-08-20) 세션에서 바뀐 것 — 요약

**1) UI: 별도 OS 창 → 유튜브 페이지 내 주입형 오버레이 패널로 전환**
- 사유: `chrome.windows.create` 분리 창은 유튜브 화면 클릭 시 뒤로 밀려서 실사용에
  불편하다는 지적 → `content_script.js`가 유튜브 탭 DOM에 직접 패널(iframe으로
  `popup.html` 그대로 재사용)을 주입, z-index로 항상 최상단 유지.
- 드래그(헤더)/리사이즈(좌우/상하/모서리 5방향) 지원. **Pointer Events +
  setPointerCapture**로 구현 — 초기에 mousemove+document 리스너와 수동
  `iframe.pointerEvents` 토글로 만들었다가 라이브에서 씹힘/역방향 리사이즈 버그가 나서
  다시 씀. 새 패널은 항상 `left`/`top` 좌표계로 생성됨(`right` 앵커였다가 리사이즈가
  마우스와 반대로 움직이는 버그의 원인이었음).
- 헤더 버튼 세 개: `─` 최소화(캡처는 유지, 헤더만 남기고 접힘) / ✕ 완전 종료(캡처
  스트림·웹소켓까지 정리) / 캡처 토글(일시정지·재개).
- 헤더 드래그 핸들러가 버튼 클릭까지 가로채던 버그 수정(`target.closest("button")`
  가드 추가) — 닫기 버튼이 반응 없던 원인.

**2) 진짜 일시정지/재개 (기존엔 불가능했던 것)**
- 문제: 오버레이 안의 버튼 클릭은 Chrome이 `tabCapture`용 신뢰 제스처로 인정하지
  않음(2026-08-19에 sidePanel로 이미 확인된 제약) → "정지 후 재시작"을 버튼 하나로
  못 함.
- 해결: "정지"를 "일시정지"로 재정의 — `offscreen.js`가 MediaStream/AudioContext/
  WebSocket을 전부 유지한 채 오디오 전송만 멈춤(`session.paused`). 재개는 특권 API가
  전혀 필요 없는 내부 상태 전환이라 오버레이 버튼만으로 완전히 제어 가능.
- 세션을 완전히 새로 시작(탭 재오픈 등)하는 것은 여전히 툴바 아이콘 클릭이 필요함
  (Chrome 제약, 우회 불가로 확인됨).

**3) 새 UI 요소**
- 볼륨 미터: 헤더 아래 얇은 막대, 오디오 워클릿에서 RMS를 5Hz로 브로드캐스트하고
  `popup.js`가 `requestAnimationFrame`으로 매 프레임 보간해서 부드럽게 움직임(샘플
  주기를 낮춰도 체감은 매끄럽게).
- 맥락 요약: 최종(final) 문장 3개(`config.CONTEXT_SUMMARY_EVERY_N_FINALS`)마다 LLM이
  "지금 무슨 얘기 중인지" 한국어 한 줄 요약을 생성, 헤더와 로그 사이에 표시.
- 로그 영역 "맨 아래로" 버튼: 스크롤이 맨 아래가 아닐 때만 노출, 사용자가 과거 로그를
  보는 중엔 새 문장이 와도 강제로 안 끌어내림(그 전엔 항상 강제 스크롤이었음).
- 테마 수동 토글(☀️/🌙), 클립보드 복사 버그 수정(iframe에 `allow="clipboard-write"`
  델리게이션 필요했음 + `execCommand` 폴백).

**4) 번역 품질 버그 다수 수정 (`backend/translation/`)**
- **가장 심각했던 버그**: KO→JA 문법(GBNF)의 허용 문자 클래스에서 `-`가 `ー` 앞에
  와서 `)`~`ー` 전체가 범위 연산자로 잘못 해석 → 사실상 라틴 문자 전체가 새고 있었음.
  영어 단어("SUPERCELL")가 로마자 그대로 번역되어 나온 게 증상. `-`를 클래스 맨
  끝으로 옮겨서 수정 + 영어 고유명사는 grammar literal 예외로 명시적으로 보존하도록
  추가(사용자 요청: 영어는 그대로 두고 나머지만 번역).
- 카나 강제 표기(`'단어'`→히라가나, `"단어"`→카타카나): 프롬프트 지시만으로는
  신뢰도가 낮아서, 구간별로 grammar-constrained 소형 요청을 먼저 돌려 확정한 뒤
  본문에 리터럴로 삽입하는 방식으로 교체. 메시지 전체가 마킹 구간 하나뿐인 경우의
  단축 경로, 다중 구간 검증+재시도 로직도 추가.
- 시스템 프롬프트에 넣은 예시 단어가 실제 출력에 그대로 새어 나오는 문제 발견(예시로
  쓴 "ともちゃん"을 모델이 실제 스트리머 이름인 것처럼 씀) — 예시를 프롬프트에서
  완전히 제거해서 해결.
- 노래 필러(なななな 등)를 "그리고" 같은 엉뚱한 단어로 오역하던 버그 — 프롬프트에
  노트를 추가했었는데 실제 프롬프트 조립부에 안 들어가고 있던 걸 오늘 프롬프트
  리팩터링 중 재발견해서 제대로 연결함.
- 원문에 없는 `!`/`?`를 번역이 임의로 붙이던 문제 — 프롬프트 지시가 안 먹혀서 코드로
  강제(원문에 없으면 결과에서도 제거).
- 모호한 단어(예: 눈=eye/snow)를 조용히 생략해버리던 문제 — 문맥으로 최선의 추측을
  해서라도 포함시키라는 지시 추가.

**5) 프롬프트를 코드에서 분리**
- `backend/translation/llama_server_engine.py`에 하드코딩돼 있던 시스템 프롬프트/
  노트들을 `backend/translation/prompts.yaml`(순수 텍스트) +
  `backend/translation/prompts.py`(YAML 로드·조합만 담당)로 분리. 프롬프트 문구만
  고치고 싶으면 이제 `.yaml`만 열면 됨, Python 문법 안 건드려도 됨.

### 확정된 UI 설계 (더 이상 미해결 아님)

- 영상 위 오버레이 **자막**은 여전히 안 함(시청 방해 판단, 유지) — 하지만 오버레이
  **패널**(로그+채팅) 자체는 이제 페이지 위에 뜬다. 헷갈리지 말 것: "자막 오버레이"와
  "패널 오버레이"는 다른 결정임.
- `chrome.sidePanel`은 계속 안 씀(활동 제약 확인됨).
- 여러 탭 동시 캡처 지원, 탭마다 독립 오버레이 패널 — 완료.
- 발신(한국어→일본어) 채팅 답장 번역, 카나 강제 표기 — 완료 및 다수 버그 수정 완료.

## 남은 작업 (2026-08-20, 사용자 확정)

1. **README.md 보강** — 오늘 1차로 갱신했지만(파이프라인 다이어그램, 사양표, 사용법),
   사용자가 내용을 직접 더 보강할 예정 + 실제 동작하는 모습을 GIF로 추가할 계획.
2. **정식 품질 평가** — STT는 2026-08-22에 처음으로 정량/정성 벤치마크 완료
   (`research/topic/20260822_stt_transcription_eval/`, large-v3-turbo 채택). 번역은
   2026-08-18 벤치마크(`research/topic/20260818_translation_model_benchmark/`)로 이미
   gemma-3-12b-it 채택까지 완료된 상태 — 모델 교체 축은 사용자가 당분간 보류로 확정.
   **다음은 파라미터/프롬프트 튜닝**(`docs/planning/IMPROVEMENT_BACKLOG.md` T2/T3/T4 —
   예외 노트 데이터 파일화, `repeat_penalty` on/off A/B, partial 문맥 등), 기존
   `data/eval_set_2026-08-18.jsonl`(120클립, normal/hard 태그)로 A/B 재사용.
3. **안정성 검증** — 계속 실사용하면서 모니터링 필요. 정상 구동 상태로 1시간 넘게
   틀어봤을 때 안정성 이슈는 아직 발견 안 됨(2026-08-20 확인). 버그가 아니라 "장시간
   구동 안정성"이 관찰 대상.
4. **노래 구간 품질** — 로드맵 3번, 챌린저블해서 보류 가능성 있음. 사용자가 명확히
   정의한 목표: **화자가 직접 부르는 노래는 전사/번역해야 함** / **배경에 깔리는
   노래(BGM)는 전사/번역하지 말고 화자의 목소리만 걸러서 번역해야 함**. 지금은 이
   구분이 전혀 안 되고 있음 — 오늘 손댄 건 "な" 반복 필러가 엉뚱하게 오역되던 것만
   고친 것뿐, 배경음악 vs 직접 가창 구분 로직은 미착수.
5. **다중 화자 지원** — 로드맵 2번, 챌린저블 과제로 보류(기존 결정 유지, `multi-speaker`
   브랜치 참고, 겹치는 발화는 업계도 미해결).
6. **멀티탭 GPU 병렬화** — 위 1~5번이 모두 개선된 후에 착수하기로 확정. 설계 메모는
   있으나(`docs/eval/multitab-pipeline-simulation.html` 시뮬레이션 포함) 실측/구현은
   아직 미착수.
7. **브랜치 정리** — 위 "브랜치 지도" 참고. `multi-speaker`는 아직 안 합쳐진 고유
   코드가 있음. 삭제는 보류 중, 다음에 다시 판단.
8. **STT 신뢰도 신호를 번역 프롬프트에 전달(2026-08-21, 신규 아이디어)** — `stt/base.py`의
   `TranscriptionResult.no_speech_prob`/`avg_logprob`가 지금은 `hallucination_gate`의
   통과/차단 이진 판단에만 쓰이고, 일단 통과된 텍스트는 번역 단계에 100% 확정된 사실처럼
   넘어감. 그래서 STT가 애매하게 흐린 구간(예: `data/flagged_segments.jsonl`의 `保った`,
   `お蔵に` 등 — 실사용 QA 리뷰, `myprojects/value-reports/veiga-translator.md` 참고)도
   번역 모델이 의심 없이 그럴듯한 한국어로 매끄럽게 포장해버리는 "확신에 찬 오역"이 발생함.
   개선 방향: 신뢰도가 낮은 세그먼트는 `translate()` 호출 시 힌트로 같이 넘겨서, 해당 구간은
   직역/보류/불확실 표시 쪽으로 유도. 새 모델 벤치마크 없이 기존 신호만 재사용하는 저비용
   개선이라 우선순위 높게 볼 만함.

## 실행 방법 (공통)

가장 간단한 방법: 저장소 루트의 `start.cmd`를 더블클릭 — llama-server + backend가 콘솔 창
없이 트레이 아이콘 하나로 뜬다(우클릭: 로그 열기/종료). 수동으로 띄우려면:

```
# 저장소 루트에서
uvicorn backend.main:app --port 8000
```
`SSL_CERT_FILE`이 빈 문자열로 설정돼 있으면 httpx/huggingface_hub SSL 컨텍스트 생성이 깨짐 —
매 백엔드 기동 전 `unset SSL_CERT_FILE`. VAD/임베딩 모델 첫 로드는 네트워크 필요(이후 캐시).
`llama-server`(gemma-3-12b-it, 포트 8080)는 별도 프로세스로 먼저/나중에 띄워도 됨 — 백엔드
startup이 `verify_contract()`로 probe만 하고 경고만 남김.

확장은 `chrome://extensions` → 개발자 모드 → "압축해제된 확장 프로그램 로드" → `extension/`.
코드 수정 후 반드시 리로드. 캡처 시작은 **툴바 아이콘 클릭 한 번**으로 바로 시작 +
유튜브 페이지 위에 오버레이 패널이 뜬다(`extension/background.js`의 `action.onClicked`
→ `content_script.js`의 `SHOW_OVERLAY`).
