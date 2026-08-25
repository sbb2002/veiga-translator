# 02. large-v3 vs kotoba-whisper-v2.0-faster vs large-v3-turbo — 정량/정성 비교

## 배경

`report/01-current-model-and-pipeline.md`에서 정리했듯, 현재 앱이 쓰는 large-v3는
정식 정량 벤치마크 없이 라이브 정성 관찰만으로 채택된 "작업 가설"이다. 이를 검증하는
첫 실험으로, `data/wav`+`data/json`(일본어-한국어 유튜브 방송 음성 코퍼스, 5개
카테고리 x 30개 = 150쌍, `tc_text`=일본어 정답 전사)에 대해 large-v3의 실제 전사
품질을 측정했다. 이어서 사용자가 대안 두 개를 순차로 요청해 같은 조건으로 비교했다:
① `kotoba-tech/kotoba-whisper-v2.0-faster`(distil-whisper 계열 일본어 특화
CTranslate2 모델), ② `large-v3-turbo`(large-v3와 인코더를 공유하고 디코더 레이어를
줄인 경량화 버전, faster-whisper 내장 별칭). 배경 동기: 이 앱 + 게임을 동시에 돌리면
GPU 경합으로 번역도 느려지고 게임도 렉이 심해진다는 실사용 불만 — "컴퓨팅 대비
품질"이 만족스럽지 않다는 문제의식에서, STT 단계의 리소스(속도)-품질 트레이드오프를
먼저 확인한다.

세 실험 모두 **앱(백엔드/확장)을 거치지 않고 STT 모델만 단독 호출**한다(사용자 지시,
2026-08-22) — VAD/glossary hotwords/hallucination gate 등 앱의 게이팅 레이어는 전부
배제하고, 모델 원본 출력을 그대로 채점한다.

## 방법

### 용어 정리

- **정량 비교**: STT 출력(hyp)과 `tc_text`(ja_ref)를 텍스트로 비교하는 자동 지표.
  둘 다 NFKC 정규화 + 구두점/공백 제거(`docs/eval/EVAL.md` §2.1과 동일 규칙) 후 채점.
  - **CER**: 문자 단위 편집거리 비율(`jiwer`). `EVAL.md`의 기존 STT 표준 지표.
  - **chrF++**: 문자 n-gram 기반 F-score(`sacrebleu.corpus_chrf`, word_order=2). 토큰화가
    필요 없어 일본어에 적합, MT 채점 표준.
  - **BLEU(char)**: `sacrebleu`의 `tokenize="char"` 옵션으로 문자 단위 n-gram 정밀도를
    측정. 일본어는 띄어쓰기가 없어 표준 단어 단위 BLEU를 쓰려면 별도 형태소
    분석기(MeCab 등)가 필요한데, 이번 1차 평가에서는 의존성을 늘리지 않기 위해 문자
    단위 토크나이즈로 대체했다(CJK 텍스트에 흔히 쓰이는 방식).
  - **ROUGE-L(F1)**: 최장 공통부분수열(LCS) 기반, 재현율 지향 지표(직접 구현, ~15줄).
    BLEU(정밀도 지향)와 상호 보완.
- **정성 비교**: 같은 언어(일본어 vs 일본어)의 **의미 보존 여부**를 LLM(gemma-3-12b-it,
  llama-server, 이미 떠 있는 별도 프로세스 — 이 앱과 무관하게 순수 텍스트 비교
  도구로만 사용) 채점 보조로 3단계 판정: **일치**(핵심 의미 동일) / **부분일치**(일부
  누락·변형되었으나 맥락은 통함) / **불일치**(핵심 의미가 다름). 근거 문장도 함께
  산출. **사용자가 이후 애매한 케이스만 스팟체크**하는 전제로, 150개 전수를 사람이
  직접 채점하지는 않았다.
- **RTF (Real-Time Factor)**: `전체 STT 소요시간 / 전체 오디오 길이`. 1.0 미만이면
  오디오 길이보다 빠르게 처리(예: RTF 0.1 = 10초 오디오를 1초에 처리).

### 실험 방법

1. `research/topic/20260822_stt_transcription_eval/src/method-1/`(large-v3),
   `method-2/`(kotoba-whisper-v2.0-faster), `method-3/`(large-v3-turbo)에서 각각:
   1. `transcribe.py` — `faster_whisper.WhisperModel`을 직접 호출(앱 미경유),
      `beam_size=5`, `condition_on_previous_text=False`(클립이 서로 독립적 — 세션
      내 실제 앱은 True로 씀, 여기선 데이터셋 특성상 다름), VAD/hotwords/
      initial_prompt/hallucination gate 전부 미사용. 150개 클립을 순회하며
      `out/method-N/transcripts.jsonl`에 hyp·no_speech_prob·avg_logprob·소요시간
      기록.
   2. `score_quantitative.py` — 위 4개 지표를 세그먼트별/카테고리별/전체로 산출.
   3. `judge_qualitative.py` — llama-server(gemma)에 `(ja_ref, hyp)` 쌍을 보내
      일치/부분일치/불일치 + 근거 산출.
2. 세 모델의 `out/method-N/quant_summary.json`, `qual_results.jsonl`,
   `transcripts.jsonl`(소요시간)을 종합.

### 평가 방법

- 정량 지표는 값 자체(카테고리별/전체)를 비교 — CER은 낮을수록, 나머지 3개는
  높을수록 좋음.
- **오차범위(95% CI)**: 페어드 부트스트랩(150개 세그먼트를 복원추출로 재표본,
  500회 반복). 코퍼스 단위 지표(CER/chrF++/BLEU/RTF)는 매 재표본마다 세그먼트를
  다시 합쳐 처음부터 재계산(세그먼트별 값의 단순 평균이 아님). 정성 판정 비율은
  Wilson score interval(표본이 작을 때 정규근사보다 정확) 사용.
- 정성 판정은 전체 분포(일치/부분일치/불일치 개수)와 카테고리별 분포를 비교.
- 게이팅 없이 원본 모델을 그대로 돌렸으므로, 정답에 없는 "ご視聴ありがとうございました"
  류 아웃트로 상투구 환각이 그대로 노출된다 — 이 건수도 별도로 집계해 세 모델의
  환각 경향을 비교한다(`hyp`가 해당 문구와 완전히 일치하는 세그먼트 수).

## 결과

### 정량 (전체 150세그먼트)

| 모델 | CER↓ (95% CI) | chrF++↑ (95% CI) | BLEU(char)↑ (95% CI) | ROUGE-L F1↑ (95% CI) | STT 총 소요 | RTF↓ (95% CI) | 아웃트로 환각 |
|---|---|---|---|---|---|---|---|
| **large-v3** | **0.289 ± 0.046** | **51.14 ± 6.67** | **68.72 ± 4.61** | **0.766 ± 0.041** | 527.3s | 0.753 ± 0.522 | 5/150 |
| kotoba-whisper-v2.0-faster | 0.318 ± 0.048 | 48.17 ± 5.68 | 63.04 ± 5.12 | 0.734 ± 0.042 | 70.5s | 0.080 ± 0.008 | **0/150** |
| large-v3-turbo | 0.292 ± 0.050 | 51.09 ± 6.32 | 68.29 ± 4.81 | 0.764 ± 0.043 | **46.3s** | **0.068 ± 0.018** | 6/150 |

large-v3-turbo가 핵심 발견이다 — **품질은 large-v3와 사실상 동급**(CER 0.292 vs
0.289, chrF++/BLEU/ROUGE-L 전부 오차범위 수준 차이)이면서 **처리 시간은 large-v3의
1/11.4**(46.3s vs 527.3s), kotoba-whisper보다도 근소하게 빠르다(RTF 0.068 vs
0.080). 다만 아웃트로 환각은 세 모델 중 가장 많다(6건 — 표본 150개 기준 4%,
large-v3의 5건과 통계적으로 유의미한 차이는 아님). large-v3의 RTF CI가 유독 넓은
것(±0.522)은 세그먼트별 처리시간 편차가 커서(일부 느린 세그먼트가 재표본마다
비중 있게 뽑힘)이지 측정 자체가 불안정하다는 뜻은 아니다 — 총 소요시간(527.3s
vs 46.3s)은 재표본과 무관한 실측값이라 turbo가 11.4배 빠르다는 결론 자체는
변하지 않는다.

### 카테고리별 CER

각 셀은 CER ± 95% CI(카테고리당 n=30, 부트스트랩 500회).

| 카테고리 | large-v3 | kotoba | large-v3-turbo |
|---|---|---|---|
| 게임 | 0.584 ± 0.110 | 0.625 ± 0.106 | 0.588 ± 0.102 |
| 여행 | 0.473 ± 0.102 | 0.528 ± 0.095 | 0.462 ± 0.091 |
| 음식,요리 | 0.272 ± 0.136 | 0.286 ± 0.120 | 0.285 ± 0.158 |
| 일상,소통 | 0.152 ± 0.043 | 0.186 ± 0.042 | 0.159 ± 0.043 |
| 패션,뷰티 | 0.114 ± 0.051 | 0.116 ± 0.046 | 0.120 ± 0.043 |

세 모델 모두 **게임 > 여행 > 음식,요리 > 일상,소통 > 패션,뷰티** 순으로 어려움이
동일하다(게임 카테고리가 압도적으로 어려움 — 배경 게임 효과음/여러 명이 겹쳐 말하는
구간이 많을 것으로 추정, 원인 분석은 미착수). 모델 간 격차보다 카테고리 간 격차가
훨씬 크다(CER 0.11~0.63). large-v3-turbo는 카테고리별로도 대체로 large-v3에 근접하고
일부(여행)는 오히려 더 낮은 CER을 보인다.

### 정성 (LLM 채점 보조, 150건)

비율 옆 괄호는 Wilson 95% CI(±%p, n=150).

| 모델 | 일치 | 부분일치 | 불일치 |
|---|---|---|---|
| large-v3 | 75 (50.0% ± 7.9%p) | 58 (38.7% ± 7.7%p) | 17 (11.3% ± 5.1%p) |
| kotoba-whisper | 64 (42.7% ± 7.8%p) | 65 (43.3% ± 7.8%p) | 21 (14.0% ± 5.6%p) |
| large-v3-turbo | 70 (46.7% ± 7.9%p) | 65 (43.3% ± 7.8%p) | 15 (10.0% ± 4.8%p) |

정성 판정도 정량과 같은 경향 — large-v3와 large-v3-turbo가 비슷한 수준(불일치율
10~11%)이고 kotoba-whisper가 소폭 뒤처진다(불일치율 14%). 세 모델 다 게임
카테고리에서 불일치가 몰린다(large-v3 9/30, kotoba 8/30, turbo 8/30).

## 결론

- **품질**: large-v3 ≈ large-v3-turbo > kotoba-whisper. large-v3-turbo는 정량·정성
  전 지표에서 large-v3와 통계적으로 구분하기 어려운 수준(150 표본 기준)이고,
  kotoba-whisper만 소폭(CER 기준 약 3%p, 불일치율 기준 3~4%p) 뒤처진다.
- **속도**: large-v3-turbo > kotoba-whisper ≫ large-v3. turbo가 large-v3 대비
  **11.4배**, kotoba 대비도 근소하게 더 빠르다.
- **환각**: kotoba-whisper만 이번 표본에서 아웃트로 상투구 환각이 0건이었고,
  large-v3(5건)와 large-v3-turbo(6건)는 비슷한 수준. 표본이 작아(150개) 5 vs 6건
  차이는 유의미하다고 보기 어렵지만, kotoba의 0건은 상대적으로 눈에 띈다. 게이팅
  없는 원본 비교이므로, 앱에 실제 배치하면 세 모델 다 hallucination gate가 추가로
  걸린다는 점은 동일.
- **종합 추천**: **large-v3-turbo가 이번 실험에서 가장 우월한 선택지**로 보인다 —
  large-v3와 품질은 동급이면서 GPU 점유 시간은 1/11.4. 사용자가 애초에 제기한
  문제(게임과 동시 구동 시 GPU 경합으로 번역 지연·게임 렉)를 정면으로 해결할 수
  있는 후보. kotoba-whisper는 환각 저항성 면에서 흥미롭지만 순수 품질에서
  large-v3-turbo에 밀린다.
- **주의**: ①표본 150개는 통계적으로 크지 않다(특히 5 vs 6건 환각 차이 같은 미세
  차이는 노이즈일 수 있음). ②이번 비교는 `condition_on_previous_text=False`로
  클립을 독립적으로 처리했는데, 실제 앱은 final 패스에서 True를 쓴다 — 스트리밍
  문맥 하에서의 차이는 별도 검증 필요. ③turbo는 `mobiuslabsgmbh` 커뮤니티 CT2
  변환판이라 공식 OpenAI 변환판과 미세 차이가 있을 수 있음(가중치 자체는 동일 모델
  기준).
- **다음 단계**: large-v3-turbo로 앱 설정(`backend/config.py`
  `WHISPER_MODEL_SIZE`)을 교체할지는 사용자 결정 필요 — 이번 리서치는 근거만
  마련했고 앱 코드는 건드리지 않았다(사용자 지시). 애매한 세그먼트(부분일치/
  판정실패)는 `out/method-N/qual_results.jsonl`에서 사용자 스팟체크 대상으로
  남겨둔다.

## 그림

`fig/`(세로막대 그래프, 오차막대=95% CI):
- `quant_metrics.png` — 정량 지표 4종(CER/chrF++/BLEU/ROUGE-L)
- `rtf.png` — RTF(처리 속도)
- `category_cer.png` — 카테고리별 CER
- `qualitative.png` — 정성 판정(일치/부분일치/불일치) 분포

## 레퍼런스

- 원본 산출물: `out/method-1/`(large-v3), `out/method-2/`(kotoba-whisper),
  `out/method-3/`(large-v3-turbo) — `transcripts.jsonl`(세그먼트별 원문),
  `quant_results.csv`(세그먼트별 4개 지표), `quant_summary.json`(집계),
  `qual_results.jsonl`(LLM 판정+근거).
- 실행 스크립트: `src/method-1/`, `src/method-2/`, `src/method-3/` (각 폴더
  `README.md`에 실행법).
- 파이프라인/설정 기준선: `report/01-current-model-and-pipeline.md`.
