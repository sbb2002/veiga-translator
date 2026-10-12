# P1 하드웨어 프로파일 평가 (2026-10-12)

**판정: P1(iGPU/CPU급 노트북)은 실시간 사용 불가.** LLM 번역(gemma-3-4b)과 NMT 번역(NLLB-600M) 모두 파이프라인 지연 드리프트가 0을 넘는다.
(판정 규칙 `HARDWARE_PROFILES.md` §8.6 ②: 드리프트 > 0 = "실시간 지속 불가".) 한계와 미검증 항목은 §5.

측정 기기는 개발기(4080)가 아닌 **P1급 실기**: AMD Ryzen 3 PRO 5450U, Radeon iGPU(Vulkan, 공유 8.3GB), RAM 16GB **단일채널**.
문서의 P1 정의(Intel iGPU + i5급, 듀얼채널 권장)와는 다르다. 결과는 "P1급 AMD 노트북 + 단일채널"의 실측이다.

## 1. 결과 요약

| 구분 | 항목 | 결과 | 비교 | 판정 |
|---|---|---|---|---|
| L1 STT | 150클립 파이프라인 CER | 0.385 | A_turbo(4080, 과거 서베이) 0.373, 차이 +0.007 (95% CI −0.025~+0.039) | 동률 |
| L1 STT | `-ac 512` 창(10.24s) 초과 6클립 CER | 0.539 | 창 이내 0.409 | 약점 |
| L1 번역 | LLM gemma-3-4b chrF++ (120클립) | 22.8 | 12B 31.1, 쌍체 차이 −7.8 (CI −11.1~−4.3) | 열세 |
| L1 번역 | NMT NLLB-600M / 1.3B chrF++ | 17.5 / 18.6 | 4B 22.8 | 더 열세 |
| L1 번역 | LLM 괄호 지문 혼입 | 38/120 | | 결함 |
| L2 속도 | 4B 생성 / prefill | Vulkan 6.4 / 70 tok/s, CPU 7.4 / 40 tok/s | 단일채널 대역폭 한계(이론 상한 약 10 tok/s) | 병목 |
| L2 속도 | whisper 7s 클립 | CPU 29~58s, Vulkan 15.4s, Vulkan+`-ac 512` 2.2s | | Vulkan 유효 |
| L2 속도 | 파이프라인 STT RTF | 3.68 (final만 1.3) | A_turbo 0.19, A_qwen3 0.45 (4080) → 약 19배 / 8배 | 실시간 불가 |
| L2 속도 | 번역 | LLM RTF 1.38, NMT-600M 문장당 0.8s | 12B(4080) RTF 0.088 → LLM 약 16배 느림 | |
| L3 | LLM, 스모크 8클립 | d_final 29s → 213s (55s 분량), 드리프트 분당 약 316s | | 발산 (정확도 낮음) |
| L3 | NMT-600M, 30클립(189s) | d_final p50 124s / p95 173s, **드리프트 분당 +46.5s**, 처리에 422s | | **실시간 불가** |
| L3 | NMT 서비스 시간 | final당 STT 5.1s + 번역 1.5s = 6.6s | STT가 77% | STT가 병목 |
| L4 | Chrome 확장 실사용 | 미실시 | | pending |

같은 클립(60번부터) 앞 55초 구간에서 NMT의 d_final은 LLM의 약 0.45~0.65배(예: 54s 시점 98s 대 213s).
NMT 전환은 번역 구간을 약 8배 줄이지만 STT final(5.1s)이 남아 대기열이 계속 쌓인다.

## 2. 프로파일 가설 중 틀린 것 (yaml 수정 필요)

- `p1.yaml`의 `llama_server -c 2048`: 프롬프트 접두부가 약 2000토큰이라 `truncated = 1`이 발생한다. 4096이 필요하다 (측정은 4096).
- `whisper_server -bs 5`만으로는 인코더가 병목이다(7s 클립에 15s). 이번 측정은 `-ac 512`를 추가했다 (yaml에 없음, 10.24s 초과 발화는 품질 저하).
- 문서 §9.3 해소: whisper-server는 **요청별 `beam_size`를 지원**한다(호출 시간이 beam과 무관 = 인코더 병목). `verbose_json`이 **`no_speech_prob`/`avg_logprob`를 노출**한다(확률 필터 동작, 환각 게이트도 정상 차단 확인).
- 문서 §9.3 해소: Windows용 whisper.cpp **Vulkan 사전 빌드는 없다**. VS Build Tools + CMake + Vulkan SDK로 소스 빌드해야 한다(v1.9.5, 약 20분).

## 3. 부수 발견

- 프로덕션 타임아웃(STT partial 3s / final 15s)을 켠 상태에서는 150클립 중 48개가 빈 결과였다 (CER 0.638). `_measure` 오버레이로 풀면 0.385. 타임아웃 폴백이 P1에서 실제로 발동한다.
- 웹소켓이 `keepalive ping timeout`(1011)으로 2회 끊겼다 (CPU 포화 시 서버의 핑 응답 지연). 측정은 `--ws-ping-interval 3600 --ws-ping-timeout 3600`으로 우회했다. 확장 실사용에서도 재현될 수 있다.
- `run_pipeline.py` 하네스가 `AudioSession.gpu_lock`(커밋 806585b) 인자를 몰랐다 → 수정.
- 입력 데이터 앞쪽 일부 클립(7112 계열)은 거의 무음(최대 진폭 0.007)이다. VAD가 정상적으로 "발화 없음"으로 판정한다.

## 4. 이번 세션에서 추가·수정한 것

- `research/topic/20260827_vad_stt_survey/src/run_pipeline.py`: T1 `--engine profile` (config.STT_ENGINE 분기, 출력 `profile-<LT_PROFILE>[-<LT_RUN_TAG>]`), `gpu_lock` 수정.
- `scripts/hwprof/replay_latency.py`: T3 (WebSocket 실시간 재생, d_final·드리프트·첫 partial 지연, 이벤트 즉시 저장).
- `backend/translation/nllb_ct2_engine.py`, `config.TRANSLATION_ENGINE`/`NLLB_*`, `main.py` 엔진 선택 분기, `profiles/p1nmt.yaml` (실험용. 기본값이 `llama-server`라 기존 동작은 불변).
- `research/topic/20260826_translation_model_survey/src/translate_nllb_ct2.py`: NLLB 번역 측정.
- 결과 폴더: `20260827_vad_stt_survey/out/profile-p1-ac512{-measure,-PRODTIMEOUTS}`, `20260826_translation_model_survey/out/p1-*`·`p1nmt-*`, 본 폴더 `out/l3_*`. `ABORTED`/`PRODTIMEOUTS`가 붙은 것은 무효 실행을 보존한 것이다.

## 5. 한계와 미검증 (통과/탈락을 과대 해석하지 말 것)

- **기준선(P4)을 재측정하지 않았다.** 12B chrF++ 31.1, A_turbo, A_qwen3는 과거 서베이 값이다(§8.5 위반 소지). 정식 L1 판정에는 P4 재측정과 마진 δ 확정이 필요하다.
- L3는 NMT 30클립(189s)뿐이고 LLM은 8클립 스모크뿐이다. 기울기는 방향(발산)만 신뢰한다. 전체 150클립 L3는 미실시다.
- 번역 LLM 측정(120클립)은 VS 설치와 겹쳐 지연 수치가 오염됐다 (p50 5.5s / p95 12.7s는 참고용).
- 미측정: RAM 피크, AC 대 배터리, 10분 서멀, L4.
- 이 기기는 AMD iGPU + 단일채널이다. Intel iGPU/듀얼채널에서는 다를 수 있다 (특히 단일채널이면 대역폭이 절반).
- NMT는 서베이상 품질이 LLM보다 낮고(chrF++ 17.5 대 22.8), 정성 평가는 하지 않았다.

## 6. 다음 세션이 이어갈 지점

STT가 병목이므로 다음을 시도할 수 있다 (미실시): partial 주기 완화·비활성(`PARTIAL_UPDATE_INTERVAL_S`), 더 작은 whisper(`small`/`base`급), final `-bs 1`.
각각 품질 비용이 있어 L1 STT 재측정이 필요하다. 번들을 낮춘 뒤에도 번역 품질(L1)이 탈락 사유로 남을 가능성이 높다.
