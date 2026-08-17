# live-translator

브라우저 탭 오디오(예: 일본어 유튜브 라이브)를 캡처해서 실시간으로 일본어를 인식하고
한국어로 번역해 보여주는 개인용 Chrome 확장 프로그램. 모든 처리는 로컬 GPU에서
실행되며 클라우드 API를 사용하지 않습니다.

전체 배경과 설계 근거는 `docs/PRD.md`, 아키텍처/빌드 순서는 `CLAUDE.md` 참고.

현재 진행 단계: **Stage 2** (캡처 + STT + 번역, 아직 정식 UI는 없고 팝업에서 provisional/final
텍스트를 단순 표시).

## 사전 준비물

- **Chrome 109+** (`chrome.offscreen` API 필요)
- **NVIDIA GPU + CUDA** — STT/번역 모두 GPU 추론 사용
- **Python 3.10+**
- **llama-server** (llama.cpp) 실행 파일 — 번역용. GGUF 형식 LLM 모델 (예: Qwen2.5-7B-Instruct
  Q4_K_M) 별도 다운로드 필요. 둘 다 `.gitignore`에 의해 저장소에는 포함되어 있지 않음
  (`llama-server/`, `*.gguf`)

## 설치

저장소 루트(`live-translator/`)에서:

```bash
# 1. Python 의존성 설치
pip install -r backend/requirements.txt

# 2. torch + torchaudio는 별도 설치 (backend/vad.py의 silero-vad용, CUDA 버전에 맞게)
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121
```

첫 VAD 로드 시 `torch.hub`가 silero-vad 모델을 한 번 다운로드합니다 (이후
`~/.cache/torch/hub`에 캐시되어 오프라인 동작).

번역 엔진(`llama-server`)과 모델은 다음 경로에 배치:

```
llama-server/llama-server.exe          # llama.cpp 릴리스에서 다운로드
backend/models/Qwen2.5-7B-Instruct-Q4_K_M.gguf   # 사용할 GGUF 모델
```

## 실행

세 개를 순서대로 띄웁니다.

**1) 번역 서버 (llama-server)**

```bash
llama-server/llama-server.exe -m backend/models/Qwen2.5-7B-Instruct-Q4_K_M.gguf --port 8080 -ngl 999 -c 4096
```

**2) 백엔드 (FastAPI, 반드시 저장소 루트에서 실행 — `backend.*` 절대 import 때문)**

```bash
uvicorn backend.main:app --reload --port 8000
```

시작 시 STT 모델(faster-whisper) 로딩 및 CUDA 확인, llama-server 연결까지 마치면 준비 완료.

**3) Chrome 확장**

1. `chrome://extensions` 접속
2. 우측 상단 "개발자 모드" 켜기
3. "압축해제된 확장 프로그램을 로드합니다" → `extension/` 폴더 선택
4. 일본어 유튜브 라이브 등 탭을 열고 확장 아이콘 클릭 → "Start Capture"
5. 팝업 창에 provisional(흐린 글씨)과 final(선명한 글씨) 일본어 원문 + 한국어 번역이
   실시간으로 표시됨

확장 코드를 수정한 뒤에는 `chrome://extensions`에서 해당 확장을 새로고침해야 합니다.

## 설정

`backend/config.py`에서 STT 모델 크기, VAD 침묵 임계값, 번역 서버 URL 등 튜닝 가능한
상수들을 관리합니다 (환경변수/dotenv 레이어는 아직 없음 — 개인 로컬 머신 전용이라 불필요).

## 테스트 / 린트

아직 구성되어 있지 않습니다.
