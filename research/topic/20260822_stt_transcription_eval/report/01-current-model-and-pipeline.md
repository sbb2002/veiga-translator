# 01. 현재 STT 모델·파이프라인 레이어 기술

## 배경

`docs/eval/EVAL.md`에 STT 채점 방법론(CER, 카테고리 분리 등)은 이미 있지만, large-v3
STT 자체에 대한 정식 정량 평가는 한 번도 돌지 않았다. 현재 모델·임계값들은 전부
라이브 정성 관찰(`data/flagged_segments.jsonl` 라벨링)로 정해진 "작업 가설" 상태다
(`CLAUDE.md` STT 항목). 실험(정량/정성 채점, 설정 A/B)을 시작하기 전에, 지금 실제로
무엇이 어떤 값으로 돌고 있는지를 코드 근거와 함께 먼저 고정해둔다 — 이후 모든 실험의
기준선(baseline) 문서 역할.

## 방법

실험(측정)은 없음 — `backend/config.py`, `backend/stt/faster_whisper_engine.py`,
`backend/vad.py`, `backend/sentence_completion.py`, `backend/audio_session.py`,
`extension/offscreen.js`를 읽고 코드에 근거해 현재 파이프라인을 그대로 기술한다.

## 결과

### 모델

| 항목 | 값 | 근거 |
|---|---|---|
| 엔진 | faster-whisper (CTranslate2, CUDA) | `backend/stt/faster_whisper_engine.py` |
| 모델 크기 | `large-v3` | `config.WHISPER_MODEL_SIZE` |
| device / compute_type | `cuda` / `int8_float16` | `config.WHISPER_DEVICE`, `config.WHISPER_COMPUTE_TYPE` |
| 언어 | `ja` 고정 | `config.WHISPER_LANGUAGE` |
| beam size | fast(partial)=1, final=5 | `config.WHISPER_FAST_BEAM_SIZE`, `WHISPER_FINAL_BEAM_SIZE` |
| `condition_on_previous_text` | final만 True, fast는 False | `faster_whisper_engine.py:68` |
| `initial_prompt` | final만 `previous_context` 전달 가능하나 **실제로는 호출부에서 안 넘김(죽은 배선, 백로그 S2)** | `IMPROVEMENT_BACKLOG.md` S2 |
| `hotwords`(glossary) | final만, glossary 전체 키워드 문자열 | `faster_whisper_engine.py:70`, `glossary.py:47` — fast/initial_prompt에는 의도적으로 미사용(환각 유발 확인됨) |
| `word_timestamps` | 항상 False | 사용 계획 없음 |
| `vad_filter` | 항상 False | 세그멘테이션은 자체 VAD(Silero)가 담당 |

모델 선택 경위: `small`→garbled(정성 관찰)→`medium`(2026-08-19 환각/뭉개짐으로 신뢰
상실, `data/flagged_segments.jsonl`)→`large-v3`(현재, **정식 벤치마크 미실시** — 이번
연구가 그 검증에 해당).

### 파이프라인 레이어 (오디오 입력 → STT 텍스트 출력)

1. **캡처/리샘플 (확장, `extension/offscreen.js`)** — `chrome.tabCapture` → 16kHz
   `AudioContext`로 직접 생성해 Chrome 네이티브 안티앨리어싱 리샘플러 사용(백로그 S1,
   과거엔 선형보간이라 앨리어싱 있었음 — 구현 완료) → AudioWorklet에서 mono PCM16,
   0.3초 청크 단위 WebSocket 전송.
2. **VAD (`backend/vad.py`, Silero)** — 32ms(512 샘플 @16kHz) 프레임 단위
   speech-probability, threshold 0.5. RNN 상태 기반이라 스트림 단위로 유지, 발화
   경계 "후보" 신호만 제공(상태 기계는 아래 3번이 담당).
3. **Utterance 세그멘테이션 상태 기계 (`backend/audio_session.py`)**
   - `VAD_SILENCE_MS=600ms` 무음 → finalize 후보.
   - `FINALIZE_GRACE_MS=200ms`: `sentence_completion.looks_complete()`가 "미완"으로
     보면 추가 유예 후 강제 finalize(무한 대기 아님).
   - `MAX_UTTERANCE_SECONDS=10s` 하드 캡: 무음이 없어도 강제 자름.
   - `has_strong_sentence_boundary()`로 무음 없이 말이 이어지는 화자를 버퍼 전체
     스캔(꼬리만이 아님, 2026-08-19 수정)해 선제 분할.
4. **STT 디코딩** — 위 "모델" 표의 fast/final 두 패스.
5. **신뢰도 게이팅 (`faster_whisper_engine.py`, `config.py`)** — 3중:
   - 하드: `no_speech_prob >= 0.6` → 무조건 드랍(`WHISPER_NO_SPEECH_HARD_THRESHOLD`).
   - 소프트(현재 하드와 같은 값이라 사실상 미작동, config.py 주석에 명시):
     `no_speech_prob >= 0.6 AND avg_logprob <= -1.0`.
   - RMS 무음 바닥 `AUDIO_RMS_SILENCE_FLOOR=0.006` — STT 신뢰도와 독립적으로 원본
     오디오 진폭 자체를 체크(`audio_session.py`).
   - 임베딩 유사도 "Bag of Hallucinations" 게이트(`hallucination_gate.py`,
     `paraphrase-multilingual-MiniLM-L12-v2`, threshold 0.78) — 위 두 신호로 못 거르는
     아웃트로 상투구 패밀리("ご視聴ありがとうございました" 등) 차단. 이 임베딩 모델은
     현재 `cuda:0`에 로드됨(런타임 로그로 확인, 리소스 절약 후보로 백로그에 기록됨).
   - 음악 게이트(`music_gate.py`)는 2026-08-19 **당일 비활성화**(`MUSIC_GATE_ENABLED
     = False`) — 정상 발화까지 걸러내는 회귀가 나와서 끔. 현재 미적용.
6. **Glossary** — STT 힌트로는 `hotwords`만 최종 패스에 사용(위 참고), `initial_prompt`
   경로는 의도적으로 배선 안 함. 현재 등록 1건(`ティーワイ`)뿐.
7. **문장 완결성 보정 (`sentence_completion.py`)** — 정규식 기반, silence 트리거를
   보정하는 두 번째 독립 신호(CLAUDE.md 설계 원칙과 일치). `。！？`는 꼬리뿐 아니라
   버퍼 전체에서 검사(무휴지 화자 대응, 위 3번과 동일 로직 재사용).

### 아직 미해결/보류로 남은 부분 (이후 실험 설계 시 참고)

- S2: `previous_context` 배선 안 됨 — 세그먼트 경계 고유명사/표기 일관성 미보정.
- S3: hard cap 절단이 무음 경계 무시하고 즉시 자름(빈도 미실측).
- A(EVAL_REPORT_2026-08-18 §5): 오디오 게인 정규화 미구현 — 8/18 리포트 기준 STT
  오류의 82.7%가 여기서 전파된다고 기록됨. **정량 평가에서 가장 먼저 확인해볼 가치가
  있는 항목.**
- glossary 1건뿐 — 이번 데이터셋(고유명사 거의 없는 잡담/게임 대사 위주로 보임)에는
  영향이 작을 가능성.

## 결론

현재 STT는 large-v3(int8_float16, CUDA)를 최종 패스 beam=5·초기프롬프트 미사용·
hotwords만 사용하는 설정으로 돌고 있고, 그 앞뒤로 VAD 기반 세그멘테이션 + 3중 신뢰도
게이팅이 감싸는 구조다. 모델 자체도, 게이팅 임계값들도 전부 라이브 정성 관찰로만
검증됐고 정량 벤치마크는 없었다 — `topic/.../report/02-*.md`(다음 보고서)에서 이
설정 그대로(앱 파이프라인 없이 STT 모델 자체만) `data/wav`+`data/json` 150쌍에 대해
정량/정성 평가를 진행한다.

## 레퍼런스

- `backend/config.py`, `backend/stt/faster_whisper_engine.py`, `backend/vad.py`,
  `backend/sentence_completion.py`, `backend/audio_session.py`
- `docs/planning/IMPROVEMENT_BACKLOG.md` (S1~S4, A)
- `docs/eval/EVAL.md`
