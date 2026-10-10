# Launches llama-server + the uvicorn backend with no visible console
# windows, and represents both as a single system-tray icon (right-click
# menu: view logs / stop). Invoked by start.cmd via
# `start "" powershell -WindowStyle Hidden -File tray_launcher.ps1`.

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$errorLog = Join-Path $root "tray_launcher_error.log"

try {

$llamaExe = Join-Path $root "llama-server\llama-server.exe"
$modelPath = Join-Path $root "backend\models\google_gemma-3-12b-it-Q4_K_M.gguf"
$backendExe = "C:\Users\User\miniconda3\envs\live-translator\Scripts\uvicorn.exe"

$backendLog = Join-Path $root "backend_run.log"
$backendErrLog = Join-Path $root "backend_run.err.log"
$llamaLog = Join-Path $root "llama_server.log"
$llamaErrLog = Join-Path $root "llama_server.err.log"
$whisperLog = Join-Path $root "whisper_server.log"
$whisperErrLog = Join-Path $root "whisper_server.err.log"
# Child PIDs are written here the moment both processes start, so stop.cmd
# (tray_stop.ps1) can always kill the stack even if this tray script dies
# before — or after — the icon exists. Removed on clean exit.
$pidFile = Join-Path $root ".live-translator-pids"

# Hardware profile (profiles/README.md): when $env:LT_PROFILE is set, the
# llama-server exe / model / port / args come from that profile, and a
# whisper_server section (if present) also starts whisper-server. Unset = the
# defaults below, which are the same command line as before profiles existed.
# This runs before the single-instance guard so the profile's ports are known.
# -np 1: the backend only ever issues one translation request at a time
# (partial translation is deprecated, finals are awaited sequentially), so the
# default 4 parallel slots just reserve 4x the KV cache. One slot keeps VRAM
# headroom for the STT engine sharing the GPU.
$llamaPort = "8080"
$llamaArgs = @("-ngl", "999", "-c", "8192", "-np", "1")
$whisperLaunch = $null
$whisperProc = $null
if ($env:LT_PROFILE) {
    $pythonExe = Join-Path (Split-Path -Parent (Split-Path -Parent $backendExe)) "python.exe"
    Push-Location $root
    try {
        $launchJson = & $pythonExe -m backend.profile launch-args llama $env:LT_PROFILE
        if ($LASTEXITCODE -ne 0) { throw "profile '$env:LT_PROFILE': launch-args llama failed" }
        # whisper_server is optional: a non-zero exit just means the profile has no such section.
        $whisperJson = & $pythonExe -m backend.profile launch-args whisper $env:LT_PROFILE
        if ($LASTEXITCODE -eq 0) { $whisperLaunch = (@($whisperJson) -join "") | ConvertFrom-Json }
    } finally {
        Pop-Location
    }
    $launch = (@($launchJson) -join "") | ConvertFrom-Json
    if ([System.IO.Path]::IsPathRooted($launch.exe)) { $llamaExe = $launch.exe } else { $llamaExe = Join-Path $root $launch.exe }
    if ([System.IO.Path]::IsPathRooted($launch.model)) { $modelPath = $launch.model } else { $modelPath = Join-Path $root $launch.model }
    $llamaPort = "$($launch.port)"
    $llamaArgs = @($launch.args)
}

# Single-instance guard. Exactly one llama-server (translation) and one backend
# may run for this repo at a time — a second gemma-3-12b instance alone is
# ~7GB of VRAM and on a 16GB GPU it starved the real one until every
# translation call timed out (2026-08-29). A stale tray or a double-clicked
# start.cmd used to leave duplicates behind; now re-running start.cmd is a
# clean restart. Match only processes belonging to THIS repo path.
$rootNorm = $root.TrimEnd('\')
Get-CimInstance Win32_Process -Filter "Name = 'llama-server.exe'" |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*$rootNorm*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'uvicorn.exe'" |
    Where-Object { $_.CommandLine -and $_.CommandLine -like '*backend.main:app*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
if ($whisperLaunch) {
    # whisper-server only exists when a profile asks for it, so only sweep it then.
    Get-CimInstance Win32_Process -Filter "Name = 'whisper-server.exe'" |
        Where-Object { $_.CommandLine -and $_.CommandLine -like "*$rootNorm*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}
# Default ports are 8080 (llama) and 8000 (backend). A profile adds its own
# llama and whisper ports; without a profile this is exactly 8080, 8000.
$cleanupPorts = @(8080, 8000)
if ($env:LT_PROFILE) { $cleanupPorts += [int]$llamaPort }
if ($whisperLaunch) { $cleanupPorts += [int]$whisperLaunch.port }
foreach ($port in ($cleanupPorts | Select-Object -Unique)) {
    for ($i = 0; $i -lt 20; $i++) {
        if (-not (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 250
    }
}

# whisper-server (only when the profile has whisper_server) starts before
# llama-server, hidden and logged to whisper_server*.log like the others.
if ($whisperLaunch) {
    if ([System.IO.Path]::IsPathRooted($whisperLaunch.exe)) { $whisperExe = $whisperLaunch.exe } else { $whisperExe = Join-Path $root $whisperLaunch.exe }
    if ([System.IO.Path]::IsPathRooted($whisperLaunch.model)) { $whisperModel = $whisperLaunch.model } else { $whisperModel = Join-Path $root $whisperLaunch.model }
    $whisperArgs = @()
    if ($whisperLaunch.args) { $whisperArgs = @($whisperLaunch.args) }
    $whisperProc = Start-Process -FilePath $whisperExe `
        -ArgumentList (@("-m", "`"$whisperModel`"", "--port", "$($whisperLaunch.port)") + $whisperArgs) `
        -WorkingDirectory $root -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $whisperLog -RedirectStandardError $whisperErrLog
}

$llamaProc = Start-Process -FilePath $llamaExe `
    -ArgumentList (@("-m", "`"$modelPath`"", "--port", $llamaPort) + $llamaArgs) `
    -WorkingDirectory $root -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $llamaLog -RedirectStandardError $llamaErrLog

# PyTorch's CUDA caching allocator never returns reserved blocks to the
# driver on its own, and the STT engine (Qwen3-ASR, backend/stt/qwen3_asr_engine.py)
# feeds it a different tensor shape on nearly every call (variable-length
# partial/final audio buffers) — over a long capture session that reserves
# more and more distinct-sized blocks instead of reusing one, so VRAM used by
# this process climbs for as long as it stays up even with no true leak
# (2026-08-30, confirmed live: ~95% of a 16GB card after a few hours,
# starting well below that right after launch). expandable_segments grows
# one resizable segment instead of hoarding many fixed-size ones, which is
# the actual fix for this shape-varying workload (torch>=2.1; this repo runs
# 2.6). Set only for the backend — llama-server is a separate C++ binary
# that doesn't read this var.
$env:PYTORCH_CUDA_ALLOC_CONF = "expandable_segments:True"

$backendProc = Start-Process -FilePath $backendExe `
    -ArgumentList "backend.main:app", "--port", "8000" `
    -WorkingDirectory $root -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $backendLog -RedirectStandardError $backendErrLog

# Recovery path #1: record the PIDs before doing anything that can throw.
$pidLines = @($llamaProc.Id, $backendProc.Id)
if ($whisperProc) { $pidLines += $whisperProc.Id }
Set-Content -Path $pidFile -Value $pidLines -Encoding ascii

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$notifyIcon = New-Object System.Windows.Forms.NotifyIcon
$notifyIcon.Icon = [System.Drawing.SystemIcons]::Application
$notifyIcon.Text = "live-translator (backend + llama-server)"
$notifyIcon.Visible = $true

$menu = New-Object System.Windows.Forms.ContextMenuStrip
$itemBackendLog = $menu.Items.Add("백엔드 로그 열기")
$itemLlamaLog = $menu.Items.Add("llama-server 로그 열기")
# Only offered when whisper-server was actually started.
$itemWhisperLog = $null
if ($whisperProc) { $itemWhisperLog = $menu.Items.Add("whisper-server 로그 열기") }
$menu.Items.Add("-") | Out-Null
$itemExit = $menu.Items.Add("종료 (두 서버 모두 정지)")
$notifyIcon.ContextMenuStrip = $menu

$itemBackendLog.Add_Click({ Start-Process notepad.exe $backendErrLog })
$itemLlamaLog.Add_Click({ Start-Process notepad.exe $llamaErrLog })
if ($itemWhisperLog) { $itemWhisperLog.Add_Click({ Start-Process notepad.exe $whisperErrLog }) }

$stopAll = {
    foreach ($p in @($backendProc, $llamaProc, $whisperProc)) {
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
    3000, "live-translator",
    "백엔드 + 번역 서버 실행 중 (트레이 아이콘 우클릭으로 로그 확인/종료)",
    [System.Windows.Forms.ToolTipIcon]::Info
)

[System.Windows.Forms.Application]::Run()

} catch {
    $_ | Out-File -FilePath $errorLog -Encoding utf8
    $_.ScriptStackTrace | Out-File -FilePath $errorLog -Encoding utf8 -Append
    # Never leave orphaned servers with no icon to stop them: if the icon
    # setup (or anything else after the two Start-Process calls) throws, kill
    # whatever we already started before exiting.
    foreach ($p in @($backendProc, $llamaProc, $whisperProc)) {
        if ($p -and -not $p.HasExited) {
            Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        }
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    exit 1
}
