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

### 실행 순서 — 완료 (2026-08-28)

1. ~~ReazonSpeech 공정 재전사~~ **완료** — `src/transcribe_reazonspeech_fair.py`
   (공식 `reazonspeech.nemo.asr` 래퍼, CPU) → `out/reazonspeech-nemo-v2_fair/`.
2. ~~채점 + CI + 결과 기록~~ **완료** — `score_quantitative.py` + `src/analyze_fair.py`.
   결과·해석은 **§2.5** 참고.
3. ~~결과 검토 후 판단~~ **완료** — fair는 bare보다 유의미하게 개선됐으나 turbo/Qwen보다
   4개 지표 전부 유의미하게 뒤짐(§2.5). → **`20260827_vad_stt_survey` 엔진 목록은
   turbo / Qwen3-ASR-1.7B 2개 유지**. (게임 외 카테고리 대등 + 스트리밍 이점 →
   `20260827` 1차 후 파이프라인 3번째 엔진 재검토 여지는 있음.)
4. **`20260827_vad_stt_survey` 착수 가능** — 엔진 목록 2개로 확정.

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
- **`load_model(device="cpu")`** — 이번 재평가는 CPU에서 돈다(§0). NeMo는 기본 fp32라
  CPU/GPU 수치 차는 부동소수점 축약 순서 수준(집계 CER 노이즈 안).
- 오디오는 `audio_from_path(seg.wav_path)`로 로드(패키지가 리샘플/모노 처리). bare
  NeMo `transcribe([path])` 호출부를 `transcribe(model, audio)`로 교체.
- 래퍼가 자체 VAD를 하므로 별도 `vad_trim`은 **미적용**(이중 트림 방지).
- `stt_elapsed_s`는 계속 기록하되 **CPU 측정임을 필드/파일명에 남긴다**(§4 한계).
- 산출은 **기존 것을 덮지 않는다** — `out/reazonspeech-nemo-v2_fair/transcripts.jsonl`.
  스키마 동일(`score_quantitative.py` 무수정 재사용).
- 원본 `transcribe_reazonspeech.py`는 남겨두고 `transcribe_reazonspeech_fair.py`
  신규 파일로, 또는 `--fair` 플래그 + `--out-suffix _fair`로. (구현 판단에 맡김.)

### 2.2 실행 순서 (CPU, `reazonspeech` conda env)

```
# 1. 공정 재전사 (CPU, ~20-60분)
python research/topic/20260826_stt_model_survey_gpu_full/src/transcribe_reazonspeech_fair.py
#    -> out/reazonspeech-nemo-v2_fair/transcripts.jsonl

# 2. 채점 (기존 스크립트 재사용) — 품질 지표만 유효
python .../src/score_quantitative.py --method reazonspeech-nemo-v2_fair

# 3. turbo/Qwen과 CI 겹침 재판정 (CER/chrF++/BLEU/ROUGE-L)
#    - 기존 out/turbo/ , out/qwen3-asr-1.7b/ 의 per-seg 결과와 페어드 부트스트랩
#    - analyze_ci_and_plot.py 에 reazonspeech-nemo-v2_fair 추가하거나 소규모 스크립트
#    - RTF 컬럼은 "CPU, report/01 GPU 수치와 비교 불가"로 표기 (§4)

# 4. 이 문서(report/03)에 "fair vs 기존" 품질 표 + 해석 추가.
#    report/01·README 의 provisional 배너를 결과에 맞게 확정 문구로 교체.
```

### 2.3 보류 — Qwen/granite parity 재런 (이번엔 안 함)

결론(§4)을 바꾸지 않으므로 이번 범위에서 뺀다. 필요할 때 별도로:
- `transcribe_qwen3_asr.py --size 1.7b --num-beams 5 --out-suffix _fair`
- `transcribe_granite.py --repetition-penalty 1.15 --no-repeat-ngram-size 3 --out-suffix _fair`

(플래그는 이미 스크립트에 들어가 있고, 기본값은 커밋된 baseline을 정확히 재현한다.)
공통 VAD 트림(`src/vad_trim.py`, §3) + 5개 전면 재비교도 여기 묶어 보류.

---

## 2.5 결과 — CPU 공정 재실행 (2026-08-28, 완료)

`src/transcribe_reazonspeech_fair.py`(공식 `reazonspeech.nemo.asr` 래퍼: VAD +
롱폼 세그먼테이션, beam search. CPU, `PYTHONUTF8=1 TORCHDYNAMO_DISABLE=1` — Windows
cp949 로케일에서 torch inductor 템플릿 로딩이 `UnicodeDecodeError`로 죽는 것 회피)
→ `out/reazonspeech-nemo-v2_fair/` (150쌍, 빈 출력 0건, CPU 7.5분).
채점 `score_quantitative.py --method reazonspeech-nemo-v2_fair`,
CI `src/analyze_fair.py`(paired bootstrap 500회, SEED=42 — `report/01`과 동일
방법론이라 turbo/Qwen CI가 그대로 재현됨).

### 전체 (150쌍, 95% CI = paired bootstrap 500회)

| 방법 | CER↓ | chrF++↑ | BLEU-char↑ | ROUGE-L F1↑ | RTF↓ |
|---|---|---|---|---|---|
| large-v3-turbo (GPU) | 0.287 ± 0.048 | 51.14 ± 6.71 | 68.27 ± 4.86 | 0.764 ± 0.043 | 0.033 ± 0.005 |
| Qwen3-ASR-1.7B (GPU) | 0.293 ± 0.054 | 57.04 ± 4.99 | 68.73 ± 5.33 | 0.747 ± 0.047 | 0.106 ± 0.018 |
| ReazonSpeech bare (GPU, 원래) | 0.397 ± 0.048 | 41.67 ± 5.88 | 55.20 ± 5.62 | 0.664 ± 0.044 | 0.067 ± 0.003 |
| **ReazonSpeech fair/wrapper (CPU)** | **0.345 ± 0.058** | **46.76 ± 6.31** | **64.47 ± 4.91** | **0.724 ± 0.045** | 0.651 ± 0.033 \*(CPU)\* |

\* fair RTF는 **CPU 측정** — `report/01`의 GPU RTF와 비교 불가(§4 한계).

### 페어드 차이 (A − B), 95% CI — CI가 0을 포함하면 "차이 없음"

| 비교 (A − B) | CER | chrF++ | BLEU-char | ROUGE-L F1 |
|---|---|---|---|---|
| fair − bare | −0.052 [−0.094, −0.002] | +5.09 [3.12, 7.86] | +9.27 [6.35, 12.69] | +0.061 [0.035, 0.090] |
| fair − turbo | +0.058 [0.022, 0.102] | −4.38 [−7.52, −1.58] | −3.80 [−6.69, −0.89] | −0.040 [−0.064, −0.018] |
| fair − Qwen3-ASR-1.7B | +0.052 [0.019, 0.103] | −10.28 [−13.02, −2.29] | −4.27 [−7.17, −1.50] | −0.023 [−0.045, −0.003] |

**4개 지표 전부, 세 비교 전부 CI가 0을 제외한다.**

### 카테고리별 CER (95% CI, n=30)

| 카테고리 | turbo (GPU) | Qwen3-ASR-1.7B (GPU) | ReazonSpeech bare (GPU) | ReazonSpeech fair (CPU) |
|---|---|---|---|---|
| 게임 | 0.588 ± 0.107 | 0.631 ± 0.100 | 0.717 ± 0.094 | **0.737 ± 0.257** |
| 여행 | 0.462 ± 0.094 | 0.442 ± 0.088 | 0.546 ± 0.057 | **0.458 ± 0.072** |
| 음식,요리 | 0.253 ± 0.117 | 0.313 ± 0.129 | 0.358 ± 0.111 | 0.349 ± 0.129 |
| 일상,소통 | 0.159 ± 0.048 | 0.048 ± 0.018 | 0.296 ± 0.071 | **0.166 ± 0.042** |
| 패션,뷰티 | 0.120 ± 0.043 | 0.230 ± 0.058 | 0.212 ± 0.051 | 0.212 ± 0.053 |

그림: `fig/fair_reazonspeech.png`(4지표 × 4방법, 95% CI 오차막대),
`fig/fair_reazonspeech_category_cer.png`(카테고리별 CER, 4방법).

### 해석

1. **공식 래퍼가 파편 붕괴를 실제로 고쳤다.** `fair − bare` 페어드 차가 4개 지표
   전부 CI가 0을 제외(CER −0.052, chrF++ +5.09, BLEU +9.27, ROUGE-L +0.061).
   특히 `일상,소통` CER 0.296 → 0.166 — bare가 문두를 통째로 버리던 것을 래퍼의
   VAD/롱폼이 회복. **bare NeMo `transcribe()`가 ReazonSpeech를 과소평가했다는
   §1의 지적이 정량으로 확인됨.**
2. **그래도 fair는 turbo/Qwen보다 여전히 유의미하게 나쁘다.** `fair − turbo`,
   `fair − Qwen` 모두 4개 지표 CI가 0을 제외(CER +0.05~0.06). **"turbo 교체 근거
   없음"은 유지.**
3. **단, "5개 카테고리 전부 밀림"은 더 이상 아니다.** 공정 조건에서 fair는
   `여행`(0.458 ≈ turbo 0.462), `일상,소통`(0.166 ≈ turbo 0.159),
   `패션,뷰티`(0.212 < Qwen 0.230)에서 turbo/Qwen과 대등하다. 전체 격차는
   **`게임` 카테고리에 집중**된다(fair CER 0.737, CI ±0.257 — BGM/효과음 위주로
   발화가 거의 없는 클립에서 beam search가 반복 루프 환각: `ピンポンピンポン…`,
   `キョキョキョ…`). `음식,요리`(0.349)도 turbo(0.253)보다 뒤짐.
4. 종합: `report/01`의 "ReazonSpeech가 유의미하게 나쁘다"는 **부분은 셋업 편향,
   부분은 실재**. 파편 붕괴가 격차의 절반가량을 설명하고(turbo와의 CER 격차
   0.11 → 0.058), 나머지는 게임 카테고리 음악 구간 취약성 + 도메인 불일치(TV 방송
   vs 잡담체 스트리머)로 추정.

### `20260827_vad_stt_survey` 엔진 결정 (§0 순서 3)

fair가 전체 4개 지표에서 turbo/Qwen보다 **유의미하게 뒤짐(§해석 2)** →
엄격 기준 미달. **`20260827_vad_stt_survey` 엔진 목록은 turbo / Qwen3-ASR-1.7B
2개 유지**하고 그대로 진행한다.

단서(별도 후속 여지, 이번 착수는 막지 않음): 게임 외 카테고리에서 대등하고,
실제 파이프라인의 VAD + RMS 게이트 + HallucinationGate가 게임 반복 루프를 걸러낼
여지가 있으며, 스트리밍 네이티브 + 토큰 신뢰도 노출 이점(`docs/planning/TUNING_PLAN.md`
TS-1e/TS-2e/TC-2c)이 있으므로 — `20260827` 1차 결과 후 "reazonspeech를 파이프라인
3번째 엔진으로" 재검토할 가치는 있다.

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

## 4. 재실행 전 예상 vs 실측

실측 결과·해석은 **§2.5**. 재실행 전 예상은 대체로 맞았다:

- **예상: "래퍼로 파편 붕괴 해소, turbo와 격차 좁혀짐"** → **맞음.** `fair − bare`
  4개 지표 CI 전부 0 제외, CER 격차 0.11 → 0.058.
- **예상: "turbo 교체 근거 없음은 유지"** → **맞음.** `fair − turbo` 4개 지표 CI
  전부 0 제외(fair가 유의미하게 나쁨).
- **예상 밖(부분)**: "5개 카테고리 전부 밀림"이 아니게 됨 — 공정 조건에서 게임 외
  3개 카테고리는 turbo/Qwen과 대등. 격차가 게임 카테고리(BGM 반복 루프)에 집중.
- **여전히 미해결**: TV 방송 코퍼스 vs 잡담체 스트리머 도메인 차이는 재실행으로도
  안 없어진다.

### 한계 — RTF는 CPU 측정 (2026-08-28)

이번 ReazonSpeech 공정 재전사는 **CPU에서 실행**하므로 (사용자 결정), `stt_elapsed_s` /
RTF는 `report/01`의 **GPU RTF 표(turbo 0.033, ReazonSpeech GPU 0.067 등)와 비교 불가**.
품질 지표(CER/chrF++/BLEU/ROUGE-L)는 디바이스 무관하게 유효하다 — NeMo 기본 fp32,
CPU/GPU 차이는 부동소수점 축약 순서 수준.

- ReazonSpeech의 **GPU RTF는 `report/01`에 이미 있음**(0.067, bare NeMo). 공식 래퍼는
  silero-VAD + 롱폼 세그먼테이션 오버헤드가 추가되지만 그 증분은 작을 것으로 예상
  (silero는 경량). 정확한 GPU RTF는 ReazonSpeech가 품질에서 경쟁력이 확인돼
  파이프라인 엔진 후보로 넘어갈 때 GPU 스팟체크로 확정한다.
- `report/01`에서 5개 후보 전부 GPU RTF < 0.16 (실시간 처리 여유)임은 이미 확인됨 —
  속도는 이번 재평가의 판단 대상이 아니다(품질/파편 붕괴가 대상).

---

## 5. 후속 연결 — `20260827_vad_stt_survey`

**결정 완료(§2.5)**: fair가 공정 조건에서도 turbo/Qwen보다 4개 지표 전부 유의미하게
뒤짐 → `20260827_vad_stt_survey` 엔진 목록은 **turbo / Qwen3-ASR-1.7B 2개 유지**.
`20260827`은 이제 착수 가능(엔진 목록 변경 없음).

**별도 후속 여지 (막지 않음)**: `20260827_vad_stt_survey`는 **실제 파이프라인
(앞단 VAD + RMS 게이트 + HallucinationGate)** 을 태운다. 이번 실패의 주원인인 게임
카테고리 반복 루프는 그 게이트들이 걸러낼 여지가 있고, 게임 외 카테고리는 이미 대등하며,
스트리밍 네이티브 + 토큰 신뢰도 노출 이점(`docs/planning/TUNING_PLAN.md` TS-1e/TS-2e/
TC-2c)이 있다. → `20260827` 1차 결과가 나온 뒤 "reazonspeech를 파이프라인 3번째 엔진으로"
재검토할 가치가 있다. 그때 `20260827`의 `DESIGN.md` §4 / `RUNBOOK.md` / `src/run_pipeline.py`
엔진 목록 + `qualitative_eval.py`의 `RUNS`를 갱신한다.
