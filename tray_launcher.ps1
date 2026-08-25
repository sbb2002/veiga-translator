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

$llamaProc = Start-Process -FilePath $llamaExe `
    -ArgumentList "-m", "`"$modelPath`"", "--port", "8080", "-ngl", "999", "-c", "8192" `
    -WorkingDirectory $root -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $llamaLog -RedirectStandardError $llamaErrLog

$backendProc = Start-Process -FilePath $backendExe `
    -ArgumentList "backend.main:app", "--port", "8000" `
    -WorkingDirectory $root -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $backendLog -RedirectStandardError $backendErrLog

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$notifyIcon = New-Object System.Windows.Forms.NotifyIcon
$notifyIcon.Icon = [System.Drawing.SystemIcons]::Application
$notifyIcon.Text = "live-translator (backend + llama-server)"
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
    exit 1
}
