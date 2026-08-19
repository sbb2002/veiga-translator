# live-translator

브라우저 탭 오디오(예: 일본어 유튜브 라이브)를 캡처해서 실시간으로 일본어를 인식하고
한국어로 번역해 보여주는 개인용 Chrome 확장 프로그램. 모든 처리는 로컬 GPU에서
실행되며 클라우드 API를 사용하지 않습니다.

전체 배경과 설계 근거는 `docs/PRD.md`, 아키텍처/빌드 순서는 `CLAUDE.md` 참고.

현재 진행 단계: 파이프라인 배관(캡처 → STT → 번역)은 완료. 정식 UI는 아직 없고 팝업에서
provisional/final 텍스트를 단순 표시하며, 지금은 전사/번역 **품질 개선 단계**입니다
(로드맵: `CLAUDE.md` §"Current roadmap", 개선 목록: `docs/IMPROVEMENT_BACKLOG.md`).

## 사전 준비물

- **Chrome 109+** (`chrome.offscreen` API 필요)
- **NVIDIA GPU + CUDA** — STT/번역 모두 GPU 추론 사용
- **Python 3.10+**
- **llama.cpp server** (CUDA 빌드) 실행 파일 — 번역용. 채택 모델은 **gemma-3-12b-it Q4_K_M**
  (GGUF, 선정 근거: `docs/EVAL_REPORT_gemma-3-12b-it_2026-08-18.md`). 둘 다 `.gitignore`에
  의해 저장소에는 포함되어 있지 않음 (`llama-server/`, `*.gguf`)

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
llama-server/llama-server.exe                      # llama.cpp 릴리스(CUDA 빌드)에서 다운로드
backend/models/google_gemma-3-12b-it-Q4_K_M.gguf   # 사용할 GGUF 모델
```

## 실행

세 개를 순서대로 띄웁니다.

**1) 번역 서버 (llama-server)**

```bash
llama-server/llama-server.exe -m backend/models/google_gemma-3-12b-it-Q4_K_M.gguf --port 8080 -ngl 999 -c 4096
```

**2) 백엔드 (FastAPI, 반드시 저장소 루트에서 실행 — `backend.*` 절대 import 때문)**

```bash
uvicorn backend.main:app --reload --port 8000
```

시작 시 glossary → VAD(silero) → STT 모델(faster-whisper, CUDA 확인 겸용)을 로드한 뒤,
번역 서버에 프로브 요청을 보내 연결과 **GBNF grammar 지원 여부**를 확인합니다
(`verify_contract` — grammar를 무시하는 서버라면 "한글 강제 비활성" 경고가 뜸). 번역
서버는 백엔드보다 늦게 켜도 됩니다(경고만 남고 기동은 계속).

**3) Chrome 확장**

1. `chrome://extensions` 접속
2. 우측 상단 "개발자 모드" 켜기
3. "압축해제된 확장 프로그램을 로드합니다" → `extension/` 폴더 선택
4. 일본어 유튜브 라이브 등 탭을 열고 확장 아이콘 클릭 → "Start Capture"
5. 팝업 창에 provisional(흐린 글씨)과 final(선명한 글씨) 일본어 원문 + 한국어 번역이
   실시간으로 표시됨

확장 코드를 수정한 뒤에는 `chrome://extensions`에서 해당 확장을 새로고침해야 합니다.

## 설정

`backend/config.py`에서 STT 모델 크기, VAD 침묵 임계값, 번역 서버 URL/타임아웃 등 튜닝
가능한 상수들을 관리합니다 (환경변수/dotenv 레이어는 아직 없음 — 개인 로컬 머신 전용이라
불필요).

고유명사 번역은 `backend/glossary.json`에 `{"일본어 표기": "한국어 표기"}`로 등록합니다.
매칭은 NFKC 정규화 후 이뤄지므로 전각/반각 표기 차이는 무시됩니다.

## 테스트 / 린트

정식 테스트/린트 러너는 아직 없습니다. 유일한 자가 체크는
`python -m backend.glossary` (glossary NFKC 매칭 검증, GPU 불필요).
