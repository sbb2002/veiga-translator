# 03. 공정성 검토 및 재실행 프로토콜 (2026-08-28)

## 왜 이 문서가 생겼나

`report/01-full-results.md`은 **ReazonSpeech-NeMo-v2가 turbo보다 통계적으로 유의미하게
나쁘다**(CER 0.397 vs 0.287, 5개 카테고리 전부 밀림)고 결론지었다. 이후 산출물을 다시
들여다본 결과, 이 수치는 **모델 실력이 아니라 셋업 편향**을 상당 부분 잰 것으로 판단된다.
따라서 `docs/eval/EVAL.md` §6(모델 후보 비교 시 공정성 검토)을 신설하고, 이 서베이의
전사 단계를 공정 조건으로 재실행한다.

`report/01`의 **"turbo 교체 근거 없음"**이라는 최종 판단 자체는 유지될 가능성이 높지만
(아래 §4), **"ReazonSpeech가 유의미하게 나쁘다"는 개별 주장은 재실행 전까지 잠정(provisional)**
으로 강등한다.

---

## 0. 이번 재평가 범위와 순서 (사용자 결정 2026-08-28)

**이번에 재실행하는 것은 ReazonSpeech-NeMo-v2 한정이다.**

- turbo는 공정하게 측정됐고(§1), 그게 모든 결정의 축이다.
- Qwen3-ASR greedy→beam=5, granite repetition_penalty parity 재런은 **결론을 바꾸지
  않으므로**(§4) 이번엔 하지 않는다 — 스크립트에 opt-in 플래그(`--num-beams`,
  `--repetition-penalty`, `--out-suffix`)만 넣어뒀고, 필요할 때 별도로 돌린다.
- ReazonSpeech를 다시 보는 이유는 순위 정정보다 **스트리밍 네이티브 후보로 살아났기
  때문**(`docs/planning/TUNING_PLAN.md` TS-1e/TS-2e/TC-2c, `report` §5).

### 실행 순서

1. **ReazonSpeech 공정 재전사** — `reazonspeech` 공식 패키지 래퍼(VAD + 롱폼
   세그먼테이션 내장). 산출은 `out/reazonspeech-nemo-v2_fair/`(기존 것 안 건드림).
2. **채점 + 이 문서에 결과 추가** — `score_quantitative.py --method reazonspeech-nemo-v2_fair`,
   기존 vs fair 나란히 표. turbo/Qwen 대비 CI 겹침 여부 재판정. `report/01`·`README`의
   provisional 배너를 확정 문구로 교체(또는 여전히 밀리면 "공정 조건에서도 밀림"으로).
3. **결과 검토 후 판단** — ReazonSpeech가 공정 조건에서 상위권과 겹치면,
   `20260827_vad_stt_survey`의 파이프라인 통과 엔진 목록에 **reazonspeech를 3번째로
   추가**할지 결정한다. 밀리면 turbo/Qwen 2개 그대로.
4. **그다음** `20260827_vad_stt_survey` 진행 — 위 3의 엔진 목록 확정 후.

즉 `20260827_vad_stt_survey`는 이 ReazonSpeech 재평가 결과를 보고 착수한다.

---

## 1. 후보별 공정성 감사

`src/transcribe_*.py` 5개를 `EVAL.md` §6 체크리스트로 점검.

| 후보 | 추론 경로 | 디코딩 | 강건성 처리 | 판정 |
|---|---|---|---|---|
| **large-v3-turbo** | faster-whisper 네이티브 (문서화된 경로) | beam=5 (프로덕션 일치) | Whisper 엔진 내장: 30초 윈도잉 + `no_speech` 처리. `vad_filter=False`여도 비발화 구간을 "환각으로 메우고 진행" | ✅ 대표성 있음 |
| **Qwen3-ASR 0.6B / 1.7B** | model card `apply_transcription_request` (문서화) | **greedy** (`generate(max_new_tokens=256)`, `num_beams` 미지정) — **프로덕션은 `QWEN3_ASR_FINAL_NUM_BEAMS=5`** | 모델 자체가 비교적 강건, 클립 짧음(~7초) | ⚠️ **디코딩 미스매치** — turbo는 beam=5, Qwen은 greedy. Qwen 품질이 자기 프로덕션 설정보다 낮게 측정됐을 수 있음 |
| **granite-speech-4.1-2b** | model card chat template (문서화) | greedy (`do_sample=False`), **`repetition_penalty` / `no_repeat_ngram_size` 없음** + **영어 지시문** | 없음. 클립 짧음 | ⚠️ **반복 루프 미완화** — `report/01`의 "granite 반복 환각"(seg `1373_5567...` CER 5.087, "あ" 루프)이 바로 이것. 영어 지시문의 JA 출력 영향은 미검증 |
| **ReazonSpeech-NeMo-v2** | **bare `nemo_asr.models.ASRModel.from_pretrained().transcribe([path])`** — model card 최소 예제. **`reazonspeech` 공식 패키지의 추론 래퍼(VAD + 롱폼 세그먼테이션 내장)를 안 씀** | NeMo 기본 (greedy RNNT) | **없음.** VAD/트림/롱폼 처리 전무 | ❌ **잘못된 추론 경로.** 노이즈/음악/무음 패딩 클립에서 디코딩을 일찍 종료 → 문장 전체 대신 2~4글자 파편만 출력 |

### ReazonSpeech 파편 붕괴 증거 (`out/reazonspeech-nemo-v2/transcripts.jsonl`)

| REF | ReazonSpeech | turbo |
|---|---|---|
| じっくり探している獣医も戻ってきて凝視される | `ピッ!` | ご視聴ありがとうございました (환각) |
| 猫の配送までかなりお気に入りの絵になった | `プリン。` | ご視聴ありがとうございました (환각) |
| ロウソクゲージみたいなのある | `みたいな。` | ロウスクキウチみたいなの |
| じゃあロウソク全部俺が預かるから | `俺が預かる。` | 早速全部俺が預かる |
| これ離ればなれになったら終わりよ | `これ離れ離れになったら終わりよ。` (거의 완벽) | これ離れ離れだったら終わりよ |

- **깨끗한 발화는 거의 완벽** → 모델의 일본어 실력 자체는 정상.
- **turbo가 stock-phrase 환각을 내는 바로 그 클립들**(게임 카테고리, 음악/무음 다수)에서
  ReazonSpeech는 파편으로 붕괴. 둘 다 틀렸지만 **실패 양상이 다르고**, CER은 파편 붕괴(대량
  삭제)를 그대로 벌한다.
- 전체 통계: ReazonSpeech 정규화 출력 평균 길이가 참조의 76%(ref 23.8자 → 18.2자). 빈
  출력은 0건이지만 삭제 위주 오류.

### 서베이 전체에 걸친 공통 교란요인

**어느 후보에도 앞단 VAD 트림을 하지 않았다.** 서베이 설계상 5개 모두 동일하게 "raw 클립
통째 입력"이지만, 이건 **중립 조건이 아니다.** Whisper 계열은 비발화 구간 처리가 엔진에
내장돼 있고(대신 환각), bare Conformer-RNNT는 없다. 평가 클립 자체가 고정 ~7초 윈도 +
게임 카테고리의 음악/무음 패딩이라, Whisper 설계는 흡수하고 다른 아키텍처는 불리하게
실패하는 스트레스 테스트가 됐다.

---

## 2. 공정 재실행 프로토콜 — ReazonSpeech 한정 (이번 범위)

### 2.1 변경

**`transcribe_reazonspeech.py`를 공식 패키지 경로로 재작성**한다.
- `from reazonspeech.nemo.asr import load_model, transcribe, audio_from_path`
  (VAD + 롱폼 세그먼테이션 내장). 패키지 미설치 시 `pip install reazonspeech`
  (또는 GitHub `reazon-research/ReazonSpeech`의 `pkg/nemo-asr`).
- 오디오는 `audio_from_path(seg.wav_path)`로 로드(패키지가 리샘플/모노 처리). bare
  NeMo `transcribe([path])` 호출부를 `transcribe(model, audio)`로 교체.
- 래퍼가 자체 VAD를 하므로 별도 `vad_trim`은 **미적용**(이중 트림 방지).
- 산출은 **기존 것을 덮지 않는다** — `out/reazonspeech-nemo-v2_fair/transcripts.jsonl`.
  스키마 동일(`score_quantitative.py` 무수정 재사용).
- 원본 `transcribe_reazonspeech.py`는 남겨두고 `transcribe_reazonspeech_fair.py`
  신규 파일로, 또는 `--fair` 플래그 + `--out-suffix _fair`로. (구현 판단에 맡김.)

### 2.2 실행 순서 (GPU 박스, `reazonspeech` conda env)

```
# 1. 공정 재전사
python research/topic/20260826_stt_model_survey_gpu_full/src/transcribe_reazonspeech_fair.py
#    -> out/reazonspeech-nemo-v2_fair/transcripts.jsonl

# 2. 채점 (기존 스크립트 재사용)
python .../src/score_quantitative.py --method reazonspeech-nemo-v2_fair

# 3. turbo/Qwen과 CI 겹침 재판정
#    - 기존 out/turbo/ , out/qwen3-asr-1.7b/ 의 per-seg 결과와 페어드 부트스트랩
#    - analyze_ci_and_plot.py 에 reazonspeech-nemo-v2_fair 추가하거나 소규모 스크립트

# 4. 이 문서(report/03)에 "fair vs 기존" 표 + 해석 추가.
#    report/01·README 의 provisional 배너를 결과에 맞게 확정 문구로 교체.
```

### 2.3 보류 — Qwen/granite parity 재런 (이번엔 안 함)

결론(§4)을 바꾸지 않으므로 이번 범위에서 뺀다. 필요할 때 별도로:
- `transcribe_qwen3_asr.py --size 1.7b --num-beams 5 --out-suffix _fair`
- `transcribe_granite.py --repetition-penalty 1.15 --no-repeat-ngram-size 3 --out-suffix _fair`

(플래그는 이미 스크립트에 들어가 있고, 기본값은 커밋된 baseline을 정확히 재현한다.)
공통 VAD 트림(`src/vad_trim.py`, §3) + 5개 전면 재비교도 여기 묶어 보류.

---

## 3. `src/vad_trim.py` 명세 (§2.3 보류분 — 지금은 불필요)

ReazonSpeech-only 재평가엔 안 쓴다(공식 래퍼가 자체 VAD). 나중에 5개 전면 재비교를
할 때를 위한 명세.

```
목적: 클립에서 선행/후행(및 필요시 중간 긴) 비발화 구간을 제거한 오디오를 돌려준다.
입력: wav 경로 또는 float32 mono 16k ndarray
출력: float32 mono 16k ndarray (발화 구간만), + 메타 {orig_s, kept_s, n_speech_regions}
구현:
  - torch.hub.load("snakers4/silero-vad", "silero_vad") — backend/vad.py와 동일
  - silero utils의 get_speech_timestamps(audio, model, threshold=0.5,
    min_silence_duration_ms=300, speech_pad_ms=200) 사용
  - 검출된 speech 구간들을 concat. 구간 0개면 원본 그대로 반환 + kept_s=0 플래그
자체점검(--check, GPU 불필요): 합성 신호(무음+사인+무음)에서 가운데 구간만 남는지,
  전부 무음이면 원본 반환 + 플래그.
```

---

## 4. 재실행이 바꿀 것 / 안 바꿀 것 (예상)

- **바뀔 가능성 큼**: ReazonSpeech CER — 파편 붕괴가 공식 래퍼로 상당 부분 해소되면 turbo와의
  격차가 좁혀지거나 CI가 겹칠 수 있음. granite의 반복 루프 이상치 제거 → CI 폭 축소.
- **안 바뀔 가능성 큼**: **"turbo 교체 근거 없음"** 최종 판단. turbo는 속도 1위가 확고하고,
  품질도 Qwen 계열과 통계적 동급. 공정 재실행에서 다른 후보가 turbo를 **유의미하게 앞서지**
  않는 한 결론은 그대로다.
- **여전히 미해결**: TV 방송 코퍼스(ReazonSpeech) vs 잡담체 스트리머 발화라는 도메인 차이는
  공정 재실행으로도 안 없어진다. "ReazonSpeech가 이 도메인에 맞다"는 사전 기대의 검증은
  별개 문제.

---

## 5. 후속 연결 — `20260827_vad_stt_survey`와의 순서

**`20260827_vad_stt_survey`는 이 ReazonSpeech 재평가 결과를 본 뒤 착수한다** (사용자
결정, §0). 흐름:

1. 이 문서 §2 — ReazonSpeech 공정 재전사 + 채점 + 결과 기록.
2. 결과 판단:
   - ReazonSpeech가 공정 조건에서 turbo/Qwen과 CI가 겹치면 → `20260827_vad_stt_survey`의
     파이프라인 통과 엔진 목록에 **reazonspeech를 3번째로 추가**. `20260827` 쪽
     `DESIGN.md` §4 / `RUNBOOK.md` / `src/run_pipeline.py`의 엔진 목록·`qualitative_eval.py`의
     `RUNS` 갱신 필요.
   - 여전히 밀리면 → 엔진 목록은 turbo/Qwen 2개 그대로, `20260827` 그대로 진행.
3. 엔진 목록 확정 후 `20260827_vad_stt_survey` 진행.

`20260827_vad_stt_survey`가 **실제 파이프라인(앞단 VAD 있음)** 을 태우므로, 거기에
ReazonSpeech를 넣으면 이 문서가 지적한 파편 붕괴 조건 없이 — 스트리밍 네이티브 +
토큰 신뢰도 노출 이점(`docs/planning/TUNING_PLAN.md` TS-1e/TS-2e/TC-2c)까지 —
평가된다.
