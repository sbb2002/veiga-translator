# 베타 배포 패키징 계획

작성 2026-08-30. 현재 버전을 베타테스트용으로 배포하기 위한 설계. 아직 구현 시작 안 함 —
다음 세션에서 이어서 진행. 작업은 `vanilla`에서 딴 **새 브랜치**에서 한다.

## 목표 / 제약

- 현재 버전을 베타로 만들어 **최대한 많은 테스터에게 배포**한다.
- 테스터의 설치·사용 번거로움을 최소화한다.
- **내가 백엔드를 호스팅하지 않는다** — "완전 로컬" 원칙 유지. 테스터 머신에서 STT+번역이
  전부 돈다.
- 현재의 마찰 지점 3가지: ① 크롬 익스텐션 개발자 모드 ② Python 백엔드 설치
  ③ llama.cpp 서버 + 모델 다운로드(+ GPU 요구).

## 결정 사항

### 1. 익스텐션 — Chrome Web Store 비공개(Unlisted) 게시

- 개발자 등록비 **계정당 1회 $5** (갱신 없음, 계정 하나로 20개까지). 심사 보통 1~3일.
- Unlisted = 스토어 검색·카테고리엔 안 뜨고 링크로만 설치. 일반 설치 버튼 + 자동 업데이트.
- 개발자 모드 unpacked 배포는 안 한다: 크롬 켤 때마다 경고 배너, 자동 업데이트 없음,
  폴더 지우면 죽음.
- `tabCapture` / `offscreen` / localhost WebSocket 정도면 심사 리젝 사유 거의 없음.
  `host_permissions`에 로컬 백엔드 주소만 명확히.

### 2. 설치팩 2종

#### 고품질팩

- **현재 버전 그대로.** 번역 `gemma-3-12b-it Q4_K_M` (llama.cpp) + STT
  `Qwen3-ASR-1.7B-hf` (transformers/CUDA).
- torch/transformers 의존성 포함 → 설치팩이 큼(PyInstaller 아님, 아래 참고). VRAM ~12GB급
  타깃.

#### 경량팩 — GPU 감지로 2티어

| 티어 | 타깃 GPU | 번역 | STT | 예상 지연 |
|---|---|---|---|---|
| 6GB | 1660 / 2060 / 3050 / 3060 노트북 | `gemma-3-4b-it Q4_K_M` (text-only GGUF) | `whisper large-v3-turbo` (whisper.cpp q5_0 ~570MB 또는 faster-whisper int8) | ~1.5~2.5s |
| 4GB (실험적 최소사양 / degraded) | GTX 1050 Ti | `gemma-3-4b-it Q4_K_S`, ctx 2048, KV cache `q8_0`, `--flash-attn`, `--n-gpu-layers` 일부만 + CPU offload | `faster-whisper small int8` **CPU** (또는 whisper.cpp base CPU) — GPU는 gemma가 전부 사용 | ~3~6s, 테스터에게 degraded로 고지 |

- 경량팩은 **gemma-3 패밀리 유지**가 핵심 이유: 프롬프트 포맷 / 한국어 전용 GBNF 문법 /
  글로서리 튜닝을 그대로 재사용. turbo STT도 이미 `config.STT_ENGINE = "faster-whisper"`
  fallback으로 코드에 존재.
- **Qwen2.5-3B-Instruct**(~2GB, JA→KO 강함)는 검토 후 베타에서는 보류: GBNF 문법 호환
  재검증 필요(Qwen3에서 문법 문제로 보류 이력) + 프롬프트/글로서리 재튜닝 비용. 코드에
  세 번째 모델 패밀리를 들이지 않는다.
- 4GB 티어는 목표 지연(1~2s)을 못 맞춤 — "돌아가긴 한다" 수준. 최소사양 안내에 명시.

### 3. 설치팩 구조 (Windows, Inno Setup)

```
live-translator-setup.exe
├─ backend/       임베디드 CPython + venv (의존성 설치 완료 상태로 번들)  ← PyInstaller로 얼리지 않음
├─ llama/         llama-server.exe (CUDA) + ggml-cuda DLL
├─ whisper/       whisper.cpp 바이너리 (STT를 torch에서 내릴 경우 — 아래 미결정 참고)
├─ launcher(트레이 앱)
│    - llama-server + backend 두 프로세스 창 없이 기동/종료, "백엔드 준비됨" 표시
│    - 첫 실행: GPU/VRAM/디스크 사전 점검 → 미달 시 친절히 안내 후 중단
│    - 첫 실행: 모델 자동 다운로드 (재개 가능, 진행바)
│    - 감지한 VRAM으로 --n-gpu-layers 자동 세팅 (4/6/8GB를 프로파일 하나로 커버 시도)
│    - 실행 시 백엔드 코드 버전 체크 → 업데이트 (채널 2, 아래)
└─ silero-vad 가중치 번들 (torch.hub 최초 네트워크 의존 제거)
```

- **모델 가중치는 설치팩에 넣지 않는다.** 첫 실행 시 다운로드(고품질 ~10GB, 경량 6GB급은
  더 작음).
- 기존 `tray_launcher.ps1` / `start.cmd` / `tray_launcher.vbs`(창 없이 llama-server +
  uvicorn을 NotifyIcon 하나로 묶는 런처)가 이미 있음 — launcher는 이걸 확장해서 만든다.
  주의: `.cmd`는 순수 ASCII 유지, `tray_launcher.ps1`은 한글 포함하므로 UTF-8 BOM 필수
  (HANDOFF.md 2026-08-22 항목 참고).
- **코드 서명**: 서명 안 된 exe는 Windows SmartScreen이 막음 → 널리 뿌리면 이탈률에
  크게 작용. OV 코드사인 인증서(연 $100~400) 검토, 아니면 최소한 "더 보기 → 실행"
  스크린샷을 설치 가이드에 포함.

### 4. 업데이트 채널 3개 분리 — 대부분의 패치는 설치팩 재배포 없이 나간다

| 채널 | 대상 | 패치 시 하는 일 | 빈도 |
|---|---|---|---|
| 1. 익스텐션 | Web Store Unlisted | manifest 버전 올려 zip 재업로드 → 테스터 자동 업데이트. **재설치 없음** | 자주 |
| 2. 백엔드 코드 | Python 소스 (STT 파이프라인, `sentence_completion`, 프롬프트 YAML, `config`) | GitHub Releases에 버전 태그 + 코드 zip → launcher가 실행 시 버전 체크, 변경 파일만 다운로드(수백 KB~MB), 재시작. **재설치 없음** | 가장 자주 |
| 3. 런타임/바이너리/런처 | 임베디드 Python, `llama-server.exe`, whisper.cpp, 트레이 앱 | 설치팩 재빌드 + 재배포, 테스터가 setup.exe 재실행 | 드묾 |

- **모델 교체도 채널 2로 나간다**: launcher가 `config`의 모델 목록과 로컬 캐시를 비교해
  "안 쓰는 모델 삭제 + 새 모델 다운로드"를 실행 시 자동 처리.
- **와이어 컨트랙트에 버전 필드**를 추가: 익스텐션 ↔ 백엔드 버전 스큐 발생 시 오버레이에
  "백엔드 업데이트 필요" 경고.
- 채널 3이 실제로 필요한 경우: `requirements.txt`에 새 의존성 추가(단, launcher가 venv에
  `pip install -r` 재실행하게 하면 회피 가능 — 느리지만 재설치 아님) / llama.cpp·whisper.cpp
  바이너리 버전 업 / 임베디드 Python 버전 업 / 트레이 앱 자체 수정.

## 미결정 — 다음 세션에서 먼저 정할 것

**STT를 torch/transformers에서 whisper.cpp(CUDA 빌드)로 내릴 것인가.**

- 장점: torch 의존성 통째로 제거 → 설치팩이 극적으로 가벼워지고 CUDA 버전 충돌 지원
  부담이 줄어듦. 번역은 이미 llama.cpp라 그대로.
- 비용: Qwen3-ASR 포기, whisper large-v3-turbo로 고정 (CLAUDE.md 기준 turbo는 Qwen3-ASR와
  품질 차이 noise 수준).
- 범위 결정 필요: **경량팩만** whisper.cpp로 가고 고품질팩은 현재대로 Qwen3-ASR+torch 유지할지,
  아니면 **양쪽 다** whisper.cpp로 통일할지. 고품질팩을 "현재 버전 그대로" 두면 torch는
  고품질 설치팩엔 어차피 포함됨.

## 작업 순서 (제안)

1. `vanilla`에서 새 브랜치 생성. Web Store Unlisted 게시 준비 ($5 등록, manifest 정리, 심사 제출).
2. 위 미결정(STT whisper.cpp 전환 범위) 결정.
3. launcher 트레이 앱: GPU 프리체크 + 첫 실행 모델 다운로더 + `--n-gpu-layers` 자동 세팅
   + 채널 2 코드 자동 업데이트.
4. Inno Setup 패키징 + 코드 서명.
5. 하드웨어 최소사양 한 줄로 테스터 사전 스크리닝 ("NVIDIA GPU VRAM 8GB+ 권장 / 4GB 실험적,
   디스크 20GB").
