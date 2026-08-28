# 실행 매뉴얼 — VAD-STT 갭 측정 실험 (다른 세션용)

이 문서는 **GPU가 있는 환경의 새 세션**에서 이 실험을 처음부터 끝까지 돌리고
보고서를 쓰는 절차다. 설계 근거·의사결정은 [`DESIGN.md`](DESIGN.md), 스크립트별
상세는 각 파일 docstring 참고. 모든 명령은 **리포 루트에서** 실행한다.

---

## 0. 한눈에 (전체 순서)

```bash
# 리포 루트, 브랜치 vanilla, STT용 conda env 활성화(아래 §1) 전제

# S1. 가상 클럭 검증 (PASS해야 나머지 신뢰 가능)
python research/topic/20260827_vad_std_survey/src/run_pipeline.py --compare-clocks --limit 10

# S2. 파이프라인 통과 → 전사결과 A (엔진 2개, 각 ~150클립)
python research/topic/20260827_vad_std_survey/src/run_pipeline.py --engine qwen3-asr-1.7b
python research/topic/20260827_vad_std_survey/src/run_pipeline.py --engine turbo

# S3. 정량 채점 4회
python research/topic/20260827_vad_std_survey/src/score_quantitative.py --run A_qwen3-asr-1.7b --transcripts research/topic/20260827_vad_std_survey/out/qwen3-asr-1.7b/pipeline_transcripts.jsonl
python research/topic/20260827_vad_std_survey/src/score_quantitative.py --run A_turbo          --transcripts research/topic/20260827_vad_std_survey/out/turbo/pipeline_transcripts.jsonl
python research/topic/20260827_vad_std_survey/src/score_quantitative.py --run B_qwen3-asr-1.7b --transcripts research/topic/20260826_stt_model_survey_gpu_full/out/qwen3-asr-1.7b/transcripts.jsonl
python research/topic/20260827_vad_std_survey/src/score_quantitative.py --run B_turbo          --transcripts research/topic/20260826_stt_model_survey_gpu_full/out/turbo/transcripts.jsonl

# S4. 임베딩 채점 4회 (S3와 같은 --run/--transcripts 조합)
python research/topic/20260827_vad_std_survey/src/score_embedding.py --run A_qwen3-asr-1.7b --transcripts research/topic/20260827_vad_std_survey/out/qwen3-asr-1.7b/pipeline_transcripts.jsonl
python research/topic/20260827_vad_std_survey/src/score_embedding.py --run A_turbo          --transcripts research/topic/20260827_vad_std_survey/out/turbo/pipeline_transcripts.jsonl
python research/topic/20260827_vad_std_survey/src/score_embedding.py --run B_qwen3-asr-1.7b --transcripts research/topic/20260826_stt_model_survey_gpu_full/out/qwen3-asr-1.7b/transcripts.jsonl
python research/topic/20260827_vad_std_survey/src/score_embedding.py --run B_turbo          --transcripts research/topic/20260826_stt_model_survey_gpu_full/out/turbo/transcripts.jsonl

# S5. 통계 + 그림
python research/topic/20260827_vad_std_survey/src/analyze_stats.py

# S6. 정성 채점
python research/topic/20260827_vad_std_survey/src/qualitative_eval.py sample
#   → out/qualitative_sample.txt 를 읽고 out/qualitative_scores.json 을 손으로 채운다 (§6)
python research/topic/20260827_vad_std_survey/src/qualitative_eval.py agg

# S7. 보고서 작성 (§7)
```

---

## 1. 사전 조건

| 항목 | 확인/조치 |
|---|---|
| 리포 | 이 리포지토리 루트. 브랜치 **`vanilla`** (`git branch --show-current`). 프로덕션 config(`backend/config.py`) 기본값 그대로 사용 — 건드리지 말 것. |
| GPU | NVIDIA + CUDA. `python -c "import torch; print(torch.cuda.is_available())"` → `True`. |
| STT 환경 | 프로젝트 STT용 conda env 활성화. `docs/log/HANDOFF.md` / `CLAUDE.md` 참고 (관례상 `live-translator`, torch cu121). `backend/requirements.txt` 설치돼 있어야 함. |
| 추가 패키지 | `run_pipeline`: `soundfile`, `scipy` (보통 이미 있음). `score_embedding`: `pip install sentence-transformers` (첫 실행 시 `intfloat/multilingual-e5-large` 자동 다운로드). `score_quantitative`/`analyze_stats`: `jiwer`, `sacrebleu`, `matplotlib`. |
| llama-server | **불필요.** 이 실험은 STT 전용. 번역 엔진은 no-op으로 주입됨. |
| CJK 폰트 (선택) | `cer_by_category.png`의 한글 카테고리명이 □로 나오면 `fonts-nanum` 또는 Noto CJK 설치. 통계 수치엔 영향 없음. |

### 입력 데이터 존재 확인

```bash
# 데이터셋 150쌍
python research/topic/20260827_vad_std_survey/src/common.py
#   → "loaded 150 segments, {... 30 each}" + "ok"

# 비교 대상 B (20260826 서베이 산출물, git에 커밋돼 있음 — 150줄 × 2)
wc -l research/topic/20260826_stt_model_survey_gpu_full/out/qwen3-asr-1.7b/transcripts.jsonl \
      research/topic/20260826_stt_model_survey_gpu_full/out/turbo/transcripts.jsonl
```

B 파일이 없으면 그 토픽의 전사 단계를 먼저 돌려야 한다
(`research/topic/20260826_stt_model_survey_gpu_full/README.md` 참고). 정상 상태에선
커밋돼 있으므로 재생성 불필요.

### 스크립트 자체점검 (GPU 불필요, 아무 데서나)

```bash
for f in common virtual_clock run_pipeline score_quantitative score_embedding analyze_stats qualitative_eval; do
  python research/topic/20260827_vad_std_survey/src/$f.py --check 2>&1 | tail -1
done
```
전부 `ok` 여야 한다 (`qualitative_eval`은 전사 파일 생기기 전엔 "ok (… skipping …)").

---

## 2. 디렉터리 지도

```
research/topic/20260827_vad_std_survey/
  DESIGN.md, README.md, RUNBOOK.md(이 파일)
  src/
    common.py            데이터셋 로더 + normalize_ja
    virtual_clock.py     time.monotonic 셰임 + _process_frame 래퍼
    run_pipeline.py      오프라인 하네스 (backend.* import — 이 토픽의 의도적 예외)
    score_quantitative.py
    score_embedding.py
    analyze_stats.py
    qualitative_eval.py
  out/                   (실행하면 생성)
    <engine>/pipeline_transcripts.jsonl         S2 산출: 전사결과 A
    clock_validation.json                       S1 산출
    <run>/quant_per_segment.jsonl + quant_summary.json     S3 산출
    <run>/embedding_per_segment.jsonl + embedding_summary.json  S4 산출
    stats_summary.json                          S5 산출
    qualitative_sample.txt                      S6 산출 (채점용 대조표)
    qualitative_scores.json                     S6 수동 작성
  fig/                   (S5가 생성) gap_quality.png, rtf.png, cer_by_category.png
  report/                (직접 작성) 01-gap-results.md, 02-qualitative-eval.md

<engine> ∈ {qwen3-asr-1.7b, turbo}
<run>    ∈ {A_qwen3-asr-1.7b, B_qwen3-asr-1.7b, A_turbo, B_turbo}
```

---

## 3. 단계별 실행과 검증

### S1. 가상 클럭 검증 — `run_pipeline.py --compare-clocks --limit 10`

첫 10클립을 (a) 가상 클럭, (b) 실시간 `asyncio.sleep` 두 방식으로 돌려 전사결과 A가
같은지 본다. `out/clock_validation.json`에 세그먼트별 CER + `max_cer`/`mean_cer`/`pass`.

- **합격**: `pass: true` (`max_cer <= 0.01`). → S2로.
- **불합격**: 가상 클럭이 파이프라인 타이밍을 왜곡한다는 뜻. 이후 전체 실행을
  `--realtime`으로 돌리는 것을 검토(150클립 × 2엔진 = 실제 오디오 길이 총합만큼
  소요, 대략 30–60분/엔진). 원인·수치를 보고서 한계 절에 기록.
- 소요: ~10클립 × (가상 즉시 + 실시간 클립길이) ≈ 수 분.

### S2. 파이프라인 통과 → 전사결과 A — `run_pipeline.py --engine <engine>`

각 엔진으로 150클립을 실제 `AudioSession`에 0.3초 청크로 흘려넣고, 방출된 `final`
이벤트를 모아 전사결과 A를 만든다. `out/<engine>/pipeline_transcripts.jsonl`
(클립당 1줄).

- 진행 로그: `[i/150] <seg_id> n_finals=… :: <hyp 앞부분>`
- 중간에 죽어도 재실행하면 이미 처리된 seg_id는 건너뛴다(파일 append). 처음부터
  다시 하려면 해당 `pipeline_transcripts.jsonl` 삭제 후 재실행.
- `--limit N`은 테스트용(파일 덮어쓰기). 본 실행은 `--limit` 없이.
- 산출 필드: `seg_id, category, duration_s, ja_ref, ko_ref, hyp(=A), n_finals,
  n_final_events, n_dropped_finals, n_partial_calls, stt_elapsed_s(=partial+final 합),
  stt_elapsed_s_final, stt_elapsed_s_partial, finalize_reason_counts{hard_cap,
  grace_expired, silence_complete, strong_boundary}`
- **점검**: 줄 수 == 150. `n_finals == 0`인 클립 수를 세어 둔다(전부 RMS/환각 게이트
  드롭 → 빈 A). `finalize_reason_counts` 합이 0이 아닌지(로깅이 잡히는지) 확인.
- 소요: 엔진별로 다름. qwen3-asr가 turbo보다 느림(RTF ~0.1 vs ~0.03). partial
  재전사가 클립당 여러 번 일어나므로 서베이보다 총 STT 시간이 많다.

### S3. 정량 채점 — `score_quantitative.py --run <run> --transcripts <path>` ×4

`normalize_ja`(NFKC + 구두점/공백 제거) 후 CER·chrF++(word_order=2)·BLEU-char·
ROUGE-L F1. 20260826 서베이와 **동일 지표·동일 정규화**.

- 산출: `out/<run>/quant_per_segment.jsonl` (per-seg, `ref_norm`/`hyp_norm` 문자열
  포함 — S5가 bootstrap 재표본마다 corpus 지표를 다시 계산하는 데 씀) +
  `out/<run>/quant_summary.json` (overall + 카테고리별 corpus + `rtf`).
- **점검**: `B_*`의 `quant_summary.json`의 CER가 20260826 서베이 보고서
  (`report/01-full-results.md`)의 값과 일치해야 한다(같은 입력·같은 코드). 어긋나면
  정규화나 입력 경로 문제.
- 소요: 분 단위.

### S4. 임베딩 채점 — `score_embedding.py --run <run> --transcripts <path>` ×4

`multilingual-e5-large`로 `hyp`(A 또는 B)와 `ja_ref`(C)의 코사인 유사도.
양쪽에 `"query: "` 프리픽스(대칭). 빈 `hyp` → `cos_sim = 0.0`.

- 산출: `out/<run>/embedding_per_segment.jsonl` (`seg_id, category, cos_sim`) +
  `embedding_summary.json`.
- **점검**: `_empty_hyp` 수가 S2에서 센 `n_finals==0` 수와 대략 일치.
- 소요: GPU면 분 단위. `sentence-transformers` 미설치면 여기서 멈춤 → 설치 후 재실행.

### S5. 통계 + 그림 — `analyze_stats.py`

엔진별로 A vs B를 150 seg_id에 페어드로 놓고:
- **paired bootstrap 2000회** (`seed=20260827`). corpus 지표(CER/chrF++/BLEU)는
  재표본마다 재계산, per-seg 평균 지표(ROUGE-L/cos_sim)는 재표본 평균.
- 지표별로 A·B·차(A−B)의 점추정 + 95% 백분위 CI + bootstrap 양측 p.
- 엔진 내부에서 지표들의 p에 **Holm 보정**.
- **판정**: `verdict = "reached"` ⟺ (차 CI가 0 포함) **and** (Holm p > 0.05). 아니면 `"gap"`.
- RTF: 4 run의 `rtf` + 엔진별 `rtf(A)/rtf(B)` 비율 + 그 비율의 bootstrap CI.

산출:
- `out/stats_summary.json` — `{engine: {metric: {point_A, point_B, point_diff,
  ci_A, ci_B, ci_diff, p_raw, p_holm, verdict, ...}}, "rtf": {...}}`
- stdout — 엔진별 마크다운 표 + RTF 표 + 엔진별 평문 요약 (보고서에 그대로 붙일 수 있음)
- `fig/gap_quality.png` (지표별 A vs B, CI 에러바), `fig/rtf.png` (4 run, 로그축),
  `fig/cer_by_category.png` (엔진별 카테고리별 A vs B)

**점검**: 모든 metric에 `p_holm >= p_raw`. `verdict`가 전부 `reached`면 "VAD-STT가
통청취 STT 품질에 도달". 하나라도 `gap`이면 그 지표·방향을 보고서 결론의 근거로.

### S6. 정성 채점

#### S6-1. 대조표 생성 — `qualitative_eval.py sample`

`out/qualitative_sample.txt` 생성. 150블록(카테고리 순 → seg_id 순), 각 블록:
```
### [001] <seg_id>  (<category>)
JA_REF : <일본어 라벨 C>
KO_REF : <한국어 참고역>
  A_qwen3-asr-1.7b : <전사 A>
  B_qwen3-asr-1.7b : <전사 B>
  A_turbo          : <전사 A>
  B_turbo          : <전사 B>
```

#### S6-2. 루브릭 (DESIGN §8.2)

**대조 기준: 각 전사문(A 또는 B)을 라벨 C와 나란히 놓고** 아래를 매긴다.
`KO_REF`는 의미 확인용으로만 참고.

**축 1 — 자연스러움·가독성** (1~5):
- 5 매우 그렇다 / 4 그렇다 / 3 그럭저럭 / 2 그렇지 않다 / 1 매우 그렇지 않다
- 표기·읽기 흐름을 본다. 의미가 같아도 표기 가독성이 낮으면 감점.
  예: "2026년 8월" > "이천이십육년 팔월", 숫자/단위/고유명사 표기, 띄어쓰기,
  깨진 문자열·중복 어절.

**축 2 — 문장부호 엄수성** (1~5):
- `?`, `!`, `.`(。) 등을 문맥에 맞게 썼는가. 의문문인데 마침표, 여러 문장이
  붙었는데 구분 없음 → 감점. (정량 지표는 구두점을 무시하므로 이 축이 유일한
  구두점 평가다.)

**환각** (0 또는 1):
- `1` = 원문(C)에 없는 내용을 그럴듯하게 지어냄. `0` = 아님.
- 점수가 아니라 플래그. 엔진별·카테고리별 **환각률**로 집계된다.

#### S6-3. `out/qualitative_scores.json` 작성

스키마 (4 run × 150 seg_id × 3 필드 ≈ 30KB):
```json
{
  "A_qwen3-asr-1.7b": {
    "<seg_id>": {"naturalness": 1-5, "punctuation": 1-5, "hallucination": 0|1},
    ...  (sample의 150개 seg_id 전부, 하나도 빠지면 agg가 거부)
  },
  "B_qwen3-asr-1.7b": { ... 150 ... },
  "A_turbo":          { ... 150 ... },
  "B_turbo":          { ... 150 ... }
}
```
- seg_id 목록·순서는 `qualitative_sample.txt`의 블록과 동일.
- `naturalness`/`punctuation`은 정수 1~5, `hallucination`은 0 또는 1. 범위 벗어나면
  `agg`가 assert로 멈추고 어느 run/seg_id인지 알려준다.
- 600블록(4×150)을 다 채운다. DESIGN에서 표본이 아니라 **전수**로 합의됨.

#### S6-4. 집계 — `qualitative_eval.py agg`

stdout 마크다운:
- run별 평균(자연스러움·문장부호) + 환각률
- 카테고리별 표 3종
- **A vs B, 엔진별**: `naturalness`·`punctuation`에 Wilcoxon signed-rank
  (`zero_method="pratt"`, 양측), 4개 p(2축 × 2엔진)에 Holm 보정. 판정 `tied` /
  `differ (A better)` / `differ (B better)`.
- 환각률 A−B, 엔진별: paired bootstrap 2000 (`seed=20260827`) + 95% CI + p.
- 점수 분포(1/2/3/4/5)

---

## 4. 결과 해석

- **1차 목표 달성** = 모든 정량 지표에서 `verdict == "reached"` **그리고** 정성
  Wilcoxon이 `tied` (또는 A가 더 나음). 즉 세그먼테이션 스택을 거쳐도 통청취
  STT 대비 품질 손실이 통계적으로 유의하지 않음.
- **갭 존재** = 하나 이상 `gap`. 어느 지표에서, 어느 방향으로, 어느 카테고리에서
  큰지 정리. `pipeline_transcripts.jsonl`의 `finalize_reason_counts` /
  `n_dropped_finals` / `n_partial_calls` 분포로 원인 가설을 세운다 (예: `hard_cap`
  다발 → 세그먼트가 너무 길게 잘림 / `n_dropped_finals` 다발 → 게이트가 과하게
  버림). 이게 후속 레이어별 ablation(DESIGN §1.3, §9)의 착수 근거가 된다.
- **RTF**: A는 partial 재전사 때문에 B보다 높게 나오는 게 정상. 비율과 그 CI를 보고.

---

## 5. 보고서 작성 (`report/`)

20260826 두 토픽의 `report/`를 형식 참고. **README 유의사항**을 반드시 지킬 것:

- [ ] 모든 정량·정성 수치에 **CI(또는 오차)** 병기
- [ ] 두 후보 비교 시 **사용한 검정과 p-value** 명기 (정량=paired bootstrap p +
      Holm, 정성=Wilcoxon signed-rank + Holm; α=0.05)
- [ ] 결과는 **표**로, 표마다 짧은 설명
- [ ] 표를 **그래프(png)**로 (`fig/`의 3개 활용) 
- [ ] 보고서 본문이 결과 표·그림을 **실제로 인용**

### `report/01-gap-results.md` (정량)
1. 배경 3–4줄 + DESIGN §10 한계 요약(아래 §6)
2. `stats_summary.json` 기반 엔진별 A vs B 표 (metric | A [CI] | B [CI] | A−B [CI] |
   p_raw | p_holm | verdict) — `analyze_stats.py` stdout를 그대로 써도 됨
3. `fig/gap_quality.png`, `fig/cer_by_category.png` 삽입 + 해설
4. RTF 표 + `fig/rtf.png`
5. `pipeline_transcripts.jsonl` 집계: 엔진별 평균 `n_finals`, `n_dropped_finals`,
   `n_partial_calls`, `finalize_reason_counts` 분포 — 갭 원인 논의
6. 결론: 1차 목표 달성 여부 + (갭이면) 후속 ablation 우선순위 제안

### `report/02-qualitative-eval.md` (정성)
1. 루브릭 요약(§6-2)
2. `agg` 출력의 run별 평균·환각률 표
3. 카테고리별 표 3종
4. A vs B Wilcoxon + Holm 표, 환각률 차 bootstrap 표
5. 점수 분포
6. 대표 사례 몇 개(`qualitative_sample.txt`에서 인용) — A가 B보다 나쁜/좋은 전형
7. 결론

### 마무리
- `README.md`의 산출물 목록·결과 요약 갱신 (20260826 README 형식 참고)
- `git add` 후 커밋. 커밋 메시지 끝에
  `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`
- 푸시는 사용자가 요청할 때만.

---

## 6. 보고서에 반드시 명시할 한계 (DESIGN §10)

1. 평가셋이 이미 문장 단위 짧은 클립 → run-on / 선제분할 / hard cap 경로를 거의
   자극하지 못함. 측정된 갭 = "VAD 앞뒤 트림 + RMS/환각 게이트 + partial/final
   beam 차 + 세그먼테이션"의 합이지 "문장 중간 오절단"이 아님.
2. 가상 클럭은 프로덕션이 GPU 부하로 실시간을 못 따라가는 상황을 재현 못 함
   (S1이 PASS면 현재 조건에선 동등 가정 가능).
3. A−B 디코딩 파라미터 교란요인: B는 통청취 1회 전사, A의 final은 beam=5.
   완전 동일하진 않을 수 있음 → 갭의 일부로 간주하고 언급.
4. turbo는 no_speech_prob/avg_logprob 환각 필터가 활성, qwen3-asr는 inert (비대칭).
5. Claude 단독 정성 채점. 1~5는 순서형이라 t-검정 대신 Wilcoxon.

---

## 7. 트러블슈팅

| 증상 | 원인 / 조치 |
|---|---|
| `ModuleNotFoundError: backend` (run_pipeline) | 리포 루트에서 실행하지 않음. `cd <repo root>` 후 재실행. |
| `run_pipeline`이 `soundfile`/`scipy` 없다고 함 | STT env에 `pip install soundfile scipy`. |
| `score_embedding`이 `sentence_transformers` 없다고 함 | `pip install sentence-transformers`. 첫 실행 시 모델 다운로드(네트워크 필요, 1회). |
| `finalize_reason_counts`가 전부 0 | `audio_session` 로거가 INFO로 안 잡힘. 하네스가 clip마다 INFO 강제하게 돼 있으니, 그래도 0이면 `backend/audio_session.py`의 `logger.info("finalize trigger=...")` 포맷이 바뀐 것 → `run_pipeline.py`의 `finalize_trigger_re` 정규식 갱신. |
| `pipeline_transcripts.jsonl` 줄 수 < 150 | 중간 실패. 재실행하면 이어서 함. 처음부터면 파일 삭제 후 재실행. |
| `analyze_stats`가 `FileNotFoundError` | S3/S4를 4 run 전부 안 돌림. |
| `qualitative_eval agg`가 seg_id mismatch로 assert | `qualitative_scores.json`에 빠졌거나 남는 seg_id가 있음. 메시지가 어느 것인지 알려줌. `sample`을 다시 생성해 대조. |
| `cer_by_category.png` 한글이 □ | CJK 폰트 미설치. 수치엔 영향 없음. `fonts-nanum` 설치 후 S5만 재실행. |
| `analyze_stats` U+2212 글리프 경고 | 무해. 그림 정상 생성됨. |

---

## 8. 대략 소요 시간 (GPU 기준, 참고)

| 단계 | 대략 |
|---|---|
| S1 클럭 검증 (10클립) | 수 분 |
| S2 파이프라인 (엔진 2개, 150클립) | turbo 빠름, qwen3-asr 느림. 가상 클럭이라 벽시계 대기는 없고 STT 연산 시간이 지배적. 수십 분 규모. |
| S3 정량 (4 run) | 분 단위 |
| S4 임베딩 (4 run) | 분 단위 (+ 모델 최초 다운로드) |
| S5 통계 | 1–3분 (bootstrap 2000 × 2엔진 × corpus 재계산) |
| S6 정성 채점 (수동, 600블록) | 사람 작업. 가장 오래 걸림. |
