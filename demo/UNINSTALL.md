# 삭제(제거) 매뉴얼

`demo/INSTALL.md`로 설치한 내용을 깨끗이 지우는 방법입니다. 무엇이 자동으로
지워지고, 무엇을 직접 해야 하는지 표로 먼저 정리합니다.

| 항목 | 위치 | 자동 삭제 |
| --- | --- | --- |
| Python 가상환경 | `venv\` / `.venv\` (저장소 루트) | ✅ `uninstall.cmd` |
| llama-server 바이너리 | `llama-server\` (저장소 루트) | ✅ `uninstall.cmd` |
| 번역 모델 GGUF | `backend\models\*.gguf` | ✅ `uninstall.cmd` |
| 로그 / PID 파일 | `demo\*.log`, 저장소 루트 `*.log`, `.live-translator-pids` | ✅ `uninstall.cmd` |
| 실행 중인 프로세스 | llama-server.exe, uvicorn/python | ✅ `uninstall.cmd` (먼저 정지) |
| STT 모델 캐시 (Qwen3-ASR) | `%USERPROFILE%\.cache\huggingface\hub\` | ⚙️ `uninstall.cmd /model-cache` |
| 환각 필터 임베딩 모델 캐시 | `%USERPROFILE%\.cache\huggingface\hub\` | ⚙️ `uninstall.cmd /model-cache` |
| VAD 모델 캐시 (silero-vad) | `%USERPROFILE%\.cache\torch\hub\` | ⚙️ `uninstall.cmd /model-cache` |
| Chrome 확장 | 브라우저 자체 | ❌ 수동 (아래 참고) |
| 확장 브라우저 저장 데이터 | — | 해당 없음 (아래 참고) |
| 캡처한 세션 로그 / 라벨링 데이터 | `data/sessions/`, `data/flagged_segments.jsonl` | ❌ 의도적으로 보존 (아래 참고) |

## 1. 자동 삭제 — `demo/uninstall.cmd`

저장소 루트/데모 환경에 이 앱이 설치하거나 만든 것들을 지웁니다.

```
demo\uninstall.cmd          REM venv, llama-server, GGUF 모델, 로그만 삭제
demo\uninstall.cmd /model-cache   REM 위에 더해 전역 모델 캐시도 함께 삭제
```

- 실행하면 먼저 `demo\stop.cmd`와 동일하게 실행 중인 프로세스를 정지시킵니다.
- 지울 목록을 미리 보여주고 `y` 확인을 받은 뒤 삭제합니다.
- `/model-cache`를 붙이지 않으면 `%USERPROFILE%\.cache\huggingface`,
  `%USERPROFILE%\.cache\torch\hub`는 건드리지 않습니다 — 이 캐시 폴더는 이
  머신의 **다른 프로젝트와 공유**될 수 있는 위치라서, 기본값으로는 그대로
  둡니다. `/model-cache`를 붙이면 이 앱이 받은 모델(Qwen3-ASR, 환각 필터용
  임베딩 모델, silero-vad)에 해당하는 폴더만 정확히 찾아서 지우고, 캐시
  디렉터리 전체를 지우지는 않습니다.

## 2. 수동 삭제 — Chrome 확장

스크립트로 안전하게 자동화할 수 없는 유일한 부분입니다 (브라우저가 실행 중인
상태에서 프로필 파일을 직접 건드리는 건 위험함).

1. `chrome://extensions` 접속
2. "Live Translator" 확장 카드에서 **"삭제"** 클릭

확장이 브라우저에 남기는 별도 저장 데이터는 없습니다 — `chrome.storage.session`만
쓰는데(`extension/background.js`), 이건 애초에 브라우저를 껐다 켜면 Chrome이
알아서 비우는 세션 전용 저장소라 확장을 삭제하면 같이 사라집니다.

## 3. 의도적으로 남겨두는 것 — 사용자 데이터

- `data/sessions/` — 실제 캡처한 방송의 세션 로그(제목/URL/전사·번역 텍스트).
- `data/flagged_segments.jsonl` — 품질 라벨링 데이터.

이 둘은 "설치가 남긴 흔적"이 아니라 **사용해서 만들어진 콘텐츠**라 자동 삭제
대상에서 뺐습니다. 저장소 폴더 자체를 통째로 지우고 싶다면(완전 제거) 위
1~2단계를 먼저 한 뒤, 저장소 폴더를 그냥 탐색기에서 삭제하면 됩니다.

## 4. 완전 제거 체크리스트 (요약)

1. `demo\uninstall.cmd /model-cache` 실행 → y 확인
2. `chrome://extensions`에서 확장 삭제
3. (선택) 저장소 폴더 전체 삭제
