# Portable variant of ../tray_launcher.ps1 for running this app on a
# DIFFERENT machine than the primary dev box (showcase/demo use, not the
# beta-packaging installer track — see docs/planning/BETA_PACKAGING_PLAN.md
# and demo/INSTALL.md). Same behavior (llama-server + uvicorn backend, no
# visible console windows, one tray icon with a right-click menu for
# logs/stop), with two differences from the original:
#
#   1. $root resolves to the REPO ROOT (one level up from demo/), since this
#      script lives in demo/ but backend/, llama-server/, backend/models/
#      etc. all live at the repo root — same layout the original script
#      assumes, just addressed from one directory deeper.
#   2. $backendExe is resolved dynamically instead of hardcoded to the dev
#      machine's own conda env path (C:\Users\User\miniconda3\...), which
#      obviously doesn't exist on another machine. See demo/INSTALL.md for
#      the expected venv layout this looks for.
#
# Invoked by demo/start.cmd via demo/tray_launcher.vbs.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$demoDir = $PSScriptRoot
$errorLog = Join-Path $demoDir "tray_launcher_error.log"

try {

$llamaExe = Join-Path $root "llama-server\llama-server.exe"
$modelPath = Join-Path $root "backend\models\google_gemma-3-12b-it-Q4_K_M.gguf"

# Resolve the backend's Python interpreter without hardcoding a path:
#   1. repo-root venv\ or .venv\ (created per demo/INSTALL.md step 2)
#   2. whatever `uvicorn` resolves to on PATH (an already-activated conda
#      env, e.g. if this script was launched from an Anaconda Prompt)
$backendExe = $null
foreach ($venvName in @("venv", ".venv")) {
    $candidate = Join-Path $root "$venvName\Scripts\uvicorn.exe"
    if (Test-Path $candidate) { $backendExe = $candidate; break }
}
if (-not $backendExe) {
    $onPath = Get-Command uvicorn.exe -ErrorAction SilentlyContinue
    if ($onPath) { $backendExe = $onPath.Source }
}
if (-not $backendExe) {
    throw ("uvicorn.exe를 찾을 수 없습니다. 저장소 루트에 venv\ 또는 .venv\ 가상환경을 만들고 " +
           "backend/requirements.txt를 설치했는지 확인하세요 (demo/INSTALL.md 2단계). " +
           "conda 환경을 쓴다면 그 환경을 activate한 상태에서 이 스크립트를 실행하세요.")
}
if (-not (Test-Path $llamaExe)) {
    throw "llama-server.exe가 없습니다: $llamaExe (demo/INSTALL.md 3단계 참고)"
}
if (-not (Test-Path $modelPath)) {
    throw "번역 모델 GGUF 파일이 없습니다: $modelPath (demo/INSTALL.md 4단계 참고)"
}

$backendLog = Join-Path $demoDir "backend_run.log"
$backendErrLog = Join-Path $demoDir "backend_run.err.log"
$llamaLog = Join-Path $demoDir "llama_server.log"
$llamaErrLog = Join-Path $demoDir "llama_server.err.log"
# Child PIDs are written here the moment both processes start, so stop.cmd
# (tray_stop.ps1) can always kill the stack even if this tray script dies
# before — or after — the icon exists. Removed on clean exit.
$pidFile = Join-Path $demoDir ".live-translator-pids"

# Single-instance guard, same rationale as the original script: one
# gemma-3-12b llama-server instance is already ~7GB of VRAM, and a second
# one starves the first until every translation call times out. Matched on
# repo root path (not demoDir) so this also catches an instance started the
# non-demo way from the repo root scripts.
$rootNorm = $root.TrimEnd('\')
Get-CimInstance Win32_Process -Filter "Name = 'llama-server.exe'" |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*$rootNorm*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'uvicorn.exe'" |
    Where-Object { $_.CommandLine -and $_.CommandLine -like '*backend.main:app*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
foreach ($port in 8080, 8000) {
    for ($i = 0; $i -lt 20; $i++) {
        if (-not (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 250
    }
}

# -np 1: the backend only ever issues one translation request at a time, so
# the default 4 parallel slots just reserve 4x the KV cache. One slot keeps
# VRAM headroom for the STT engine sharing the GPU.
$llamaProc = Start-Process -FilePath $llamaExe `
    -ArgumentList "-m", "`"$modelPath`"", "--port", "8080", "-ngl", "999", "-c", "8192", "-np", "1" `
    -WorkingDirectory $root -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $llamaLog -RedirectStandardError $llamaErrLog

# PyTorch's CUDA caching allocator never returns reserved blocks to the
# driver on its own, and the STT engine (Qwen3-ASR) feeds it a different
# tensor shape on nearly every call — over a long capture session that
# reserves more and more distinct-sized blocks. expandable_segments grows
# one resizable segment instead, which is the actual fix. See
# ../tray_launcher.ps1 for the full history on this.
$env:PYTORCH_CUDA_ALLOC_CONF = "expandable_segments:True"

$backendProc = Start-Process -FilePath $backendExe `
    -ArgumentList "backend.main:app", "--port", "8000" `
    -WorkingDirectory $root -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $backendLog -RedirectStandardError $backendErrLog

# Recovery path #1: record the PIDs before doing anything that can throw.
Set-Content -Path $pidFile -Value @($llamaProc.Id, $backendProc.Id) -Encoding ascii

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$notifyIcon = New-Object System.Windows.Forms.NotifyIcon
$notifyIcon.Icon = [System.Drawing.SystemIcons]::Application
$notifyIcon.Text = "live-translator demo (backend + llama-server)"
$notifyIcon.Visible = $true

$menu = New-Object System.Windows.Forms.ContextMenuStrip
$itemBackendLog = $menu.Items.Add("백엔드 로그 열기")
$itemLlamaLog = $menu.Items.Add("llama-server 로그 열기")
$menu.Items.Add("-") | Out-Null
$itemExit = $menu.Items.Add("종료 (두 서버 모두 정지)")
$notifyIcon.ContextMenuStrip = $menu

$itemBackendLog.Add_Click({ Start-Process notepad.exe $backendErrLog })
$itemLlamaLog.Add_Click({ Start-Process notepad.exe $llamaErrLog })

$stopAll = {
    foreach ($p in @($backendProc, $llamaProc)) {
        if ($p -and -not $p.HasExited) {
            Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        }
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    $notifyIcon.Visible = $false
    $notifyIcon.Dispose()
    [System.Windows.Forms.Application]::Exit()
}
$itemExit.Add_Click($stopAll)

$notifyIcon.ShowBalloonTip(
    3000, "live-translator demo",
    "백엔드 + 번역 서버 실행 중 (트레이 아이콘 우클릭으로 로그 확인/종료)",
    [System.Windows.Forms.ToolTipIcon]::Info
)

[System.Windows.Forms.Application]::Run()

} catch {
    $_ | Out-File -FilePath $errorLog -Encoding utf8
    $_.ScriptStackTrace | Out-File -FilePath $errorLog -Encoding utf8 -Append
    foreach ($p in @($backendProc, $llamaProc)) {
        if ($p -and -not $p.HasExited) {
            Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        }
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    exit 1
}
