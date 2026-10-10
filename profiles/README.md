# 하드웨어 프로파일

`profiles/<name>.yaml` 은 `backend/config.py` 상수를 덮어쓰고, 런처(`tray_launcher.ps1`)가
띄울 llama-server / whisper-server 실행 정보를 담는다. **아무 환경변수도 없으면 아무 동작도
하지 않는다** (기존 설정 100% 동일).

## 사용법

| 환경변수 | 설명 |
|---|---|
| `LT_PROFILE=<name>` | 해당 프로파일의 `config:` 를 적용한다. 미설정이면 no-op. |
| `LT_PROFILE_OVERLAYS=a,b` | `LT_PROFILE` 적용 후 이 목록을 순서대로 덮어쓴다 (예: `_measure`). |

적용되면 백엔드 로그에 `Profile 'p3' applied (N overrides)` 한 줄이 남고,
`backend.config.ACTIVE_PROFILE` 에 이름이 기록된다.

CLI (저장소 루트에서 실행):

```
python -m backend.profile list
python -m backend.profile validate [name]          # 전체 또는 1개 검증, 오류 시 exit 1
python -m backend.profile show <name>              # extends 해석 결과 출력
python -m backend.profile launch-args llama <name> # 런처용 JSON 한 줄
```

### 런처 연동

`$env:LT_PROFILE` 이 설정된 상태에서 `start.cmd` 로 실행하면 `tray_launcher.ps1` 이
`launch-args llama` 의 JSON 으로 llama-server 의 exe / model / port / args 를 대체한다.
미설정이면 기존 하드코딩 명령을 그대로 쓴다.
`whisper_server` 섹션이 있으면 `launch-args whisper` 가 성공할 때만 whisper-server 를 llama-server 보다 먼저
숨김 창으로 띄우고 `whisper_server.log` / `whisper_server.err.log` 로 리다이렉트한다 (섹션이 없으면 조용히 건너뜀).
포트 정리와 싱글 인스턴스 가드는 프로파일의 llama/whisper 포트까지 포함하며, 트레이 종료와 `stop.cmd` 는 whisper-server 도 정지한다.

## 스키마

```yaml
name: p3                  # 파일명(확장자 제외)과 같아야 한다
description: ...
vram_budget_gb: 10        # 정보용
extends: [_base]          # 선택. 먼저 적용할 프로파일 목록 (순환 금지)
config:                   # backend.config 의 대문자 상수 덮어쓰기
  STT_ENGINE: faster-whisper
  PARTIAL_UPDATE_INTERVAL_S: 1.0
llama_server:             # 런처 전용
  exe: llama-server/llama-server.exe   # 저장소 루트 기준 상대 경로 가능
  model: backend/models/google_gemma-3-12b-it-Q4_K_M.gguf
  port: 8080
  args: ["-ngl", "999", "-c", "8192", "-np", "1"]
whisper_server:           # STT_ENGINE=whisper-cpp 일 때 런처 전용
  exe: ...
  model: ...
  port: 8081
  args: []
```

검증 규칙:
- 최상위 키는 위 목록만 허용한다 (오타 방지).
- `config` 키는 `backend/config.py` 에 **이미 존재하는** 대문자 이름이어야 한다 (`STT_ENGNE` 같은 오타는 오류).
- 타입은 기존 값과 맞아야 한다. `float` 자리에는 정수를 넣어도 되고(실수로 변환), `bool` 자리에는 `bool` 만 가능하다.
- `ACTIVE_PROFILE` 은 덮어쓸 수 없다.

## 새 프로파일 추가 절차

1. `profiles/<name>.yaml` 을 만든다. 공통 부분은 `extends` 로 빼고, 차이만 적는다.
2. 덮어쓸 상수는 `backend/config.py` 에서 정확한 이름을 찾아 `config:` 에 넣는다.
3. `python -m backend.profile validate <name>` 으로 OK 가 뜨는지 확인한다.
4. `python -m backend.profile show <name>` 으로 해석 결과를 확인한다.
5. 측정이 필요하면 `LT_PROFILE_OVERLAYS=_measure` 를 함께 준다 (타임아웃 폴백 해제).
6. 실제 GPU 로 띄우기 전까지 프로파일의 품질/지연 수치를 추정으로 적지 않는다
   (`docs/planning/HARDWARE_PROFILES.md` 참고).
