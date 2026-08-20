# 작업 핸드오프 (2026-08-20 기준)

세션 간 인수인계 문서. 다른 로컬 세션(또는 다른 머신)에서 이어 작업할 때 여기서부터 시작할 것.
배치 1(Q1/R4 등) 계측 작업 자체는 완료·검증됐고, 이후 그 위에서 실사용 튜닝과 새 기능이
쌓였다 — 아래 "브랜치 지도"가 지금 상태를 가장 정확히 반영한다.

## 브랜치 지도

```
main                     — 오래됨, 최근 작업 미반영 (병합 안 함, 그대로 둘 것)
  └─ batch1-instrumentation  — 백엔드 트렁크. 1차 목표(단일 화자) 품질 마일스톤 달성, 여기서 백엔드 작업 일단 마무리.
       ├─ multi-speaker      — 2차 목표(다중 화자) 1차 구현. 겹치는 발화 문제로 보류, 로드맵 최후순위.
       └─ ui-chat-reply      — UI 단계 착수. 발신(KO→JA) 채팅 번역 초안 구현·푸시됨.
            └─ multi-tab-capture — UI 재디자인 확정 + 탭별 동시 캡처 지원. 코드는 다 됐지만
                                    **실브라우저 검증 전** — 아래 절 참고. 아직 커밋/푸시 안 됨.
```

`multi-speaker`와 `ui-chat-reply`는 `batch1-instrumentation`에서 각각 분기했고 서로 독립적 —
아직 서로 합쳐지지 않았다. `multi-tab-capture`는 `ui-chat-reply`에서 분기했다. `main`/
`multi-speaker`/`ui-chat-reply`는 원격에 푸시되어 있지만 **`multi-tab-capture`는 아직 로컬
전용 — 다른 머신에서 이어서 검증하려면 먼저 이 브랜치를 커밋하고 푸시해야 함**(다음 세션에서
사용자가 명시적으로 요청하기 전엔 자동 커밋하지 않았음).

## batch1-instrumentation — 현재 상태 (1차 목표: 검증 완료)

**결론부터: 이 브랜치 기준 백엔드는 실사용 검증까지 끝났고, 사용자 확정 코멘트는
"여태까지 본 것 중 최고의 품질"(2026-08-19, ~50분 연속 실캡처 시청 후).**

핵심 구성 (전부 실캡처로 검증됨, 자세한 시행착오는 `docs/log/SESSION_LOG_2026-08-19.md`):

- STT: faster-whisper **large-v3**, int8_float16 (`backend/config.py`)
- 환각 필터링: 정규식 전부 제거. `no_speech_prob`/`avg_logprob` 확률 임계값
  (`WHISPER_NO_SPEECH_HARD_THRESHOLD = 0.6`) + 임베딩 유사도 "Bag of Hallucinations" 게이트
  (`backend/hallucination_gate.py`, sentence-transformers) 이중 필터
- 문장 경계: `sentence_completion.py`가 버퍼 전체를 스캔해서 종결부호 탐지 (꼬리만 보던 버그 수정)
- 부분(partial) 번역은 deprecated — 최종(final) 문장만 번역, partial은 일본어 전사만 표시
- `FINALIZE_GRACE_MS = 200` (침묵 후 문장 미완성 시 추가 대기시간, 400→200으로 단축)
- 뮤직 게이트(`backend/music_gate.py`)는 코드만 남아있고 **비활성화 상태** — 실발화를 놓치는
  회귀가 있었음, 재활성화하려면 실캡처 오디오로 재보정 먼저 필요

**아직 안 한 것**:
- `docs/eval/EVAL.md` 정식 벤치마크로 large-v3를 아직 안 돌렸음 — 지금까지는 전부 라이브 정성 평가
- ㅋㅋㅋ 임의 첨가(LLM이 근거 없이 웃음 표시 추가) — 경미한 이슈로 후순위 확정, 미해결
- 탭 캡처 시 볼륨이 살짝 작아지는 문제 — 원인 미해결 (한 번 `autoGainControl` 끄는 걸로
  "고쳤다"가 번역이 완전히 끊기는 회귀를 냄, 되돌림 — `docs/log/SESSION_LOG_2026-08-19.md` 11번 참고)

## multi-speaker — 2차 목표, 보류 상태

턴 교대(turn-taking) 화자만 라벨링하는 1차 구현 완료 (`backend/speaker_id/`, ECAPA-TDNN +
온라인 코사인 클러스터링, 문장 확정 시 1회만 실행). 겹치는 발화는 이 방식으로 아예 못 다룬다.

문헌 조사 결과(2026-08-19): 진짜 겹침 분리(source separation)는 우리 제약(모노 1채널, 개인용
GPU 1장)에서 검증된 해법이 없음 — SOT류는 일본어 사전학습 모델이 없고, CSS는 다채널 전제,
SepFormer류는 실환경 일반화가 문서화된 약점. 상용 제품(Otter.ai 등)도 겹침 구간은 그냥
뭉개서 처리하는 게 현실. **사용자 판단: 지금 모델 수준에서는 챌린저블 → 로드맵 최후순위로
보류.** 재개 시 참고: 분리 시도보다 겹침 "감지 + 명시적 저신뢰 표시"가 현실적 다음 스텝.

## ui-chat-reply — UI 단계, 착수

`CLAUDE.md` 로드맵 4단계(UI). 확정된 방향:

- 영상 위 오버레이 자막 **안 함** — 시청 방해된다고 판단
- `chrome.sidePanel` **재시도 안 함** — 2026-08-19에 원인불명 activeTab 문제로 포기, 아직도 원인 모름
- 지금의 `chrome.windows.create` 분리 팝업 창(자유 이동 가능) 형태를 그대로 UI 베이스로 유지

이번에 추가한 것: **발신 방향(한국어→일본어) 채팅 번역** — 사용자가 직접 쓴 한국어 채팅을
현재 방송 맥락(`_final_history`)에 맞춰 일본어로 번역, 복사해서 스트리머에게 보내기 위한 용도.
같은 분리 창 하단에 섹션 추가 (입력창 + 번역 버튼 + 클릭-복사 출력), 버튼/엔터 트리거 방식
(실시간 타이핑 번역 아님). `backend/translation/llama_server_engine.py`에
`translate_ko_to_ja()` 새로 추가 — 기존 JA→KO 경로(글로서리/false-friend 노트 등 많이 튜닝됨)와
완전히 분리된 별도 프롬프트/그래머, 아직 실데이터 튜닝 전이라 초안 단계.
샘플 문장 2개로 스모크 테스트만 마침, 실제 방송에서 아직 안 써봄.

**창 비주얼 디자인**은 후속 `multi-tab-capture` 브랜치에서 확정·구현됐음(아래 절 참고) — 더는
착수 대기 상태가 아님.

## multi-tab-capture — UI 재디자인 + 멀티탭 지원, 실브라우저 검증 대기 (2026-08-20)

`ui-chat-reply`에서 분기. 두 가지를 한 브랜치에 묶었음 — 멀티탭 지원이 어차피 `popup.js`를
다시 손대야 해서, 싱글턴 버전을 거치지 않고 재디자인을 곧바로 탭별 버전으로 이식했다.

**1) UI 재디자인 (확정, Artifact 목업으로 사용자 승인받음)**
- 다크/라이트는 `prefers-color-scheme` 자동 전환(수동 토글 없음). 색상은 전부 CSS 커스텀
  프로퍼티 토큰 — 하드코딩 리터럴 없음.
- 헤더(캡처 버튼: 녹화●/일시정지❚❚ SVG 아이콘 + 상태 2줄 "캡처 중"/"일시정지" + 탭 제목 +
  펄스 라이브 점) / 로그 카드(파셜·파이널 시각 구분, 디버그 지표 막대 기본 숨김+토글) / 채팅
  답장 카드, 3단 레이아웃.
- 로그 영역은 `flex:1; min-height:0`이라 창 크기에 반응 — `html,body`는 `height:100vh`로
  둬서(고정 픽셀 금지) 실제 리사이즈가 그대로 반영되게 했음.

**2) 멀티탭 동시 캡처**
- `chrome.storage.session`의 단일 키(`captureState`/`transcriptLog`/`viewerWindowId`)를 전부
  탭별 맵(`captureSessions`/`transcriptLogs`/`viewerWindows`)으로 교체 — 탭마다 독립 세션.
- `extension/offscreen.js`의 전역 변수(audioContext/mediaStream/ws 등)를
  `Map<tabId, SessionState>`로 교체, 탭마다 `<audio>` 재생 엘리먼트를 동적 생성.
- 뷰어 창은 탭마다 별도 창(`popup.html?tabId=<id>`) — 사용자 확정 사항.
- `chrome.tabs.onRemoved`: 캡처 중이던 탭이 닫히면 세션+창 자동 정리.
- `chrome.windows.onRemoved`: 뷰어 창만 닫으면 창 id만 잊고 캡처는 백그라운드에서 계속(기존
  단일 세션 때와 같은 원칙).
- 백엔드(`backend/main.py`)는 원래 연결마다 독립 `AudioSession`을 만들던 구조라 **거의 손대지
  않음** — 확장 쪽 싱글턴만 리팩터링 대상이었음.
- 권한 추가 없음(`tabCapture`/`offscreen`/`activeTab`/`storage`로 충분, `tabs` 권한 불필요).

**⚠️ 실브라우저 검증 안 됨** — 이 세션엔 Chrome이 없어서 코드만 작성(`node --check`로 문법,
태그 밸런스 스크립트로 HTML 구조만 확인). **다음 세션/다른 머신에서 반드시 확인할 것**:
1. 서로 다른 유튜브 라이브 탭 A, B에서 각각 확장 아이콘 클릭 → 창 2개가 독립적으로 뜨고 각자
   자기 탭 소리만 캡처/번역하는지 (A 창에 B 자막이 새지 않는지가 핵심).
2. A만 정지해도 B는 영향 없이 계속되는지.
3. 캡처 중인 탭을 브라우저에서 직접 닫기 → 세션·창이 자동 정리되는지.
4. 같은 탭에서 아이콘을 다시 클릭 → 새 창이 아니라 기존 창이 포커스되는지.
5. 뷰어 창만 닫고 다시 아이콘 클릭 → 새 창에 로그 히스토리가 복원되는지.
6. 채팅 답장 번역이 각 창에서 자기 탭의 방송 맥락으로 나오는지(다른 탭과 안 섞이는지).
7. (신규 UI) 다크/라이트 자동 전환, 디버그 지표 토글, 캡처 중/일시정지 아이콘·상태 표시,
   창 리사이즈 시 로그 영역이 실제로 늘어나고 스크롤되는지.

**⚠️ GPU 사양 차이 주의**: `batch1-instrumentation`의 모델 선택(faster-whisper `large-v3`,
gemma-3-12b-it Q4_K_M)은 **RTX 4080 SUPER(16GB)** 기준으로 튜닝됐음(`docs/eval/MODEL_BENCHMARK_PLAN.md`
참고). 검증 예정 머신은 그보다 낮은 사양이라고 확인됨 — 단일 탭도 지연이 늘어날 수 있고,
멀티탭은 세션마다 같은 GPU를 나눠 쓰므로(백엔드가 세션당 `asyncio.to_thread`로 STT를 돌리긴
하지만 물리 GPU는 하나) 탭이 늘수록 세션당 처리 지연이 더 커질 것으로 예상됨. 반응이 느리면
버그로 오인하기 전에 먼저 GPU 사양 차이인지 확인할 것 — 필요하면 `faster-whisper` 모델을
`medium`으로 낮추는 것도 옵션(`backend/config.py`).

**커밋 상태**: 이 브랜치의 변경사항(`extension/background.js`, `offscreen.html`, `offscreen.js`,
`popup.html`, `popup.js`)은 **아직 커밋 안 됨**. `docs/planning/UI.md`(사용자가 작성한 UI 명세
+ "실제 활용 예시")도 아직 untracked. 다른 머신에서 이어가려면 커밋 후 푸시 필요.

## 실행 방법 (공통)

```
# 저장소 루트에서
uvicorn backend.main:app --port 8000
```
`SSL_CERT_FILE`이 빈 문자열로 설정돼 있으면 httpx/huggingface_hub SSL 컨텍스트 생성이 깨짐 —
매 백엔드 기동 전 `unset SSL_CERT_FILE`. VAD/임베딩 모델 첫 로드는 네트워크 필요(이후 캐시).
`llama-server`(gemma-3-12b-it, 포트 8080)는 별도 프로세스로 먼저/나중에 띄워도 됨 — 백엔드
startup이 `verify_contract()`로 probe만 하고 경고만 남김.

확장은 `chrome://extensions` → 개발자 모드 → "압축해제된 확장 프로그램 로드" → `extension/`.
코드 수정 후 반드시 리로드. 캡처 시작은 **툴바 아이콘 클릭 한 번**으로 바로 시작 + 분리 창이
뜬다 (팝업 단계 없음 — `extension/background.js`의 `action.onClicked`).
