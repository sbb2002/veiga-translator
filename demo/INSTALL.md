# 설치 매뉴얼 (다른 로컬 머신에서 데모/쇼케이스로 구동)

이 문서는 지금 개발 중인 이 저장소를 **사양이 되는 다른 컴퓨터 한 대**에서 그대로
띄워서 보여주기 위한 안내입니다. 여러 테스터에게 배포하기 위한 정식 설치 프로그램이
아닙니다 (그건 `docs/planning/BETA_PACKAGING_PLAN.md`의 별도 계획 — 아직 시작 안 함).
여기 있는 `demo/` 폴더의 스크립트들은 저장소 루트에 있는 `start.cmd`/`stop.cmd`/
`tray_launcher.ps1`과 완전히 같은 방식으로 동작하되, 개발 머신 하나에 고정된
설정(특히 conda 환경의 절대경로)을 없애 다른 컴퓨터에서도 그대로 쓸 수 있게 만든
버전입니다.

## 0. 사전 요구사항

| 항목 | 최소 | 비고 |
| --- | --- | --- |
| OS | Windows 10/11 | `tray_launcher.ps1`이 Windows 전용 (System.Windows.Forms 트레이 아이콘) |
| GPU | NVIDIA, CUDA 지원, VRAM 8GB+ | 개발 환경은 RTX 4080 SUPER(16GB). 낮으면 지연 증가 — README.md "사양" 참고 |
| GPU 드라이버 | 최신 NVIDIA 드라이버 | CUDA 12 계열 필요 (llama-server CUDA 빌드 기준) |
| Chrome | 109+ | `chrome.offscreen` API 필요 |
| Python | 3.10+ | venv 하나로 backend 전체 실행 |
| 디스크 | 20GB+ | 번역 모델(~7GB) + STT/VAD 모델(첫 실행 자동 다운로드) + Python 의존성 |
| 인터넷 | 최초 1회 필요 | STT 모델(Qwen3-ASR)과 VAD 모델(silero-vad)은 첫 실행 시 자동 다운로드 후 로컬 캐시됨 |

## 1. 저장소 복사

이 저장소 전체(`demo/` 폴더 포함)를 새 머신으로 복사합니다. git이 있으면 clone,
없으면 폴더 전체를 압축해서 옮겨도 됩니다. 아래 단계는 전부 **저장소 루트** 기준
상대 경로로 진행합니다.

```
live-translator/
├─ backend/
├─ demo/              ← 이 문서가 있는 폴더, 데모용 실행 스크립트
├─ extension/
├─ llama-server/       ← 3단계에서 채워 넣음
├─ start.cmd, tray_launcher.ps1, ...   ← 원본(개발 머신 전용, 여기선 안 씀)
```

## 2. Python 가상환경

저장소 루트에서:

```bash
python -m venv venv
venv\Scripts\activate

pip install -r backend/requirements.txt

REM torch + torchaudio는 CUDA 버전에 맞춰 별도 설치 (backend/vad.py의
REM silero-vad용 — 새 머신의 CUDA 버전에 맞는 인덱스로 바꿔도 됨)
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121
```

`demo/tray_launcher.ps1`은 저장소 루트의 `venv\Scripts\uvicorn.exe` (또는 `.venv\`)를
자동으로 찾습니다. 가상환경 폴더명을 다르게 하거나 conda를 쓰고 싶다면, 그 환경을
activate한 상태에서 `demo/start.cmd` 대신 `demo/tray_launcher.ps1`을 PowerShell에서
직접 실행하면 PATH의 `uvicorn`을 대신 찾습니다.

## 3. llama-server (번역 엔진)

[llama.cpp GitHub Releases](https://github.com/ggml-org/llama.cpp/releases)에서
**CUDA 빌드**를 받아 저장소 루트에 `llama-server/` 폴더를 만들고 그 안에
압축을 풉니다 (`llama-server.exe` + `ggml-cuda.dll` 등 DLL들이 `llama-server/`
바로 아래 있어야 함 — 원본 저장소의 `llama-server/` 폴더 구성과 동일).

## 4. 번역 모델 (GGUF)

채택 모델: **gemma-3-12b-it, Q4_K_M 양자화** (선정 근거:
`docs/eval/EVAL_REPORT_gemma-3-12b-it_2026-08-18.md`). Hugging Face에서
`google_gemma-3-12b-it-Q4_K_M.gguf` 파일을 받아 다음 경로에 둡니다 (bartowski
계정 등에서 이 명명 규칙의 GGUF 양자화본을 배포하는 경우가 많음 — 정확한 배포처는
직접 검색해서 확인):

```
backend/models/google_gemma-3-12b-it-Q4_K_M.gguf
```

## 5. STT / VAD 모델 — 별도 설치 불필요

- **STT** (`Qwen/Qwen3-ASR-1.7B-hf`): `backend/config.py`의 `STT_ENGINE = "qwen3-asr"`
  설정대로, 백엔드 최초 실행 시 `transformers`가 Hugging Face에서 자동 다운로드합니다.
- **VAD** (`silero-vad`): 최초 실행 시 `torch.hub`가 한 번 다운로드하고
  `~/.cache/torch/hub`에 캐시합니다.

둘 다 최초 1회만 인터넷이 필요하고, 이후에는 오프라인으로 동작합니다.

## 6. Chrome 확장 설치 (개발자 모드)

지금 단계에서는 Web Store에 올리지 않고 개발자 모드로 씁니다 — 원본 README.md와
동일한 절차입니다.

1. `chrome://extensions` → 우측 상단 "개발자 모드" 켜기
2. "압축해제된 확장 프로그램을 로드합니다" → 복사해온 저장소의 `extension/` 폴더 선택

## 7. 실행

`demo/start.cmd`를 더블클릭하면 번역 서버(llama-server) + 백엔드가 콘솔 창 없이
트레이 아이콘 하나로 뜹니다 (우클릭 메뉴로 로그 확인/종료). 다시 실행하면 기존
프로세스를 정리하고 새로 띄웁니다.

1. `demo/start.cmd` 더블클릭 → 트레이 아이콘이 뜰 때까지 대기 (모델 로드 때문에
   수십 초~1분 정도 걸릴 수 있음)
2. 일본어 유튜브 라이브 등을 열고 툴바의 확장 아이콘 클릭
   → 캡처가 바로 시작되고, 영상 위에 오버레이 패널이 뜹니다.
3. 종료할 땐 트레이 아이콘 우클릭 → "종료", 또는 `demo/stop.cmd` 더블클릭
   (트레이 아이콘이 사라졌거나 뭔가 꼬였을 때도 안전하게 강제 종료됨)

## 8. 문제 해결

- **트레이 아이콘이 안 뜨고 바로 사라짐**: `demo/tray_launcher_error.log` 확인.
  `uvicorn.exe를 찾을 수 없습니다` 에러면 2단계(venv) 다시 확인.
- **로그 위치**: `demo/backend_run.err.log` (백엔드), `demo/llama_server.err.log`
  (번역 서버) — 트레이 아이콘 우클릭 메뉴로도 바로 열림. 원본 저장소 루트의
  로그 파일과는 별도 파일이라 서로 섞이지 않습니다.
- **포트 충돌 (8000/8080)**: 같은 머신에서 원본 `start.cmd`(저장소 루트)와
  `demo/start.cmd`를 동시에 띄우면 포트가 겹칩니다 — 하나만 실행하세요.
- **GPU VRAM 부족 / 지연이 큼**: `backend/config.py`에서 STT 모델을 더 가벼운 것으로
  바꾸거나(`STT_ENGINE = "faster-whisper"`로 되돌리고 `WHISPER_MODEL_SIZE` 조정),
  llama-server 실행 인자(`demo/tray_launcher.ps1`의 `-c 8192`)를 낮춰보세요.
- **확장 코드를 수정한 뒤**: `chrome://extensions`에서 해당 확장을 새로고침해야
  반영됩니다.

## 삭제(제거)

이 설치를 깨끗이 되돌리는 방법은 `demo/UNINSTALL.md` 참고 (`demo/uninstall.cmd` 한 번
실행으로 venv/llama-server/모델/로그를 정리, Chrome 확장 삭제만 수동).

## 참고

- 이 `demo/` 스크립트들은 저장소 루트 스크립트(`start.cmd`, `tray_launcher.ps1` 등)의
  이식 가능(portable) 버전일 뿐, 기능은 동일합니다 — 개발 머신에서는 계속 루트
  스크립트를 쓰면 됩니다.
- 향후 정식 다중 사용자 배포(Web Store 게시, 설치 프로그램, 코드 서명 등)는
  `docs/planning/BETA_PACKAGING_PLAN.md`에서 별도로 다룹니다.
