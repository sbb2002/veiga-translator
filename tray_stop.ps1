# Force-stops the whole live-translator stack — llama-server, the uvicorn
# backend, and the tray process — even when the tray icon is missing (the
# tray script crashed, or a server was launched by hand outside it). Run via
# stop.cmd (double-click) or directly.
$ErrorActionPreference = "Continue"
$root = $PSScriptRoot.TrimEnd('\')
$killed = @()

function Stop-One($processId, $label) {
    $p = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if ($p) {
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
        $script:killed += "$processId ($label)"
    }
}

# 1) PIDs the tray recorded when it started its children.
$pidFile = Join-Path $root ".live-translator-pids"
if (Test-Path $pidFile) {
    foreach ($line in Get-Content $pidFile) {
        $procId = 0
        if ([int]::TryParse($line.Trim(), [ref]$procId) -and $procId -gt 0) {
            Stop-One $procId "from .live-translator-pids"
        }
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

# 2) Signature sweep for anything the PID file missed — orphans from a
#    crashed tray, a hand-launched backend, a stale earlier session. Scoped
#    to THIS repo path so other projects' Python/servers are never touched.
Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and (
        ($_.Name -eq 'llama-server.exe' -and $_.CommandLine.Contains($root)) -or
        ($_.Name -in 'python.exe', 'uvicorn.exe', 'nohup.exe' -and $_.CommandLine.Contains('backend.main:app')) -or
        ($_.Name -eq 'powershell.exe' -and $_.CommandLine.Contains('tray_launcher.ps1'))
    )
} | ForEach-Object { Stop-One $_.ProcessId $_.Name }

if ($killed.Count) {
    Write-Output ("Stopped:`n  " + ($killed -join "`n  "))
} else {
    Write-Output "Nothing was running."
}
