# Cleanly removes everything a demo/showcase install of this app leaves
# behind on a machine (see demo/INSTALL.md for the install side, and
# demo/UNINSTALL.md for the full explanation of what this does and does
# NOT touch). Two tiers:
#
#   1. Repo-local generated files/folders (Python venv, llama-server
#      binaries, the downloaded GGUF translation model, log files, the
#      tray's pid file) — always offered, confirmed once unless -Force.
#   2. Globally-cached downloaded models OUTSIDE this repo, shared per
#      Windows user account (Hugging Face hub cache for the STT model +
#      the hallucination-gate embedding model, torch.hub cache for
#      silero-vad) — only removed with -IncludeModelCache, since other
#      unrelated projects on the same machine may share those cache
#      directories. Only the SPECIFIC model ids this app downloads are
#      ever targeted — never the whole cache folder.
#
# Deliberately does NOT touch:
#   - The Chrome extension itself — must be removed manually via
#     chrome://extensions (see demo/UNINSTALL.md). Scripting a browser
#     profile directly while Chrome may be running is unsafe.
#   - chrome.storage — the extension only ever uses chrome.storage.session
#     (see extension/background.js), which Chrome itself clears when the
#     browser closes. Nothing persists there to clean up.
#   - data/sessions/ and data/flagged_segments.jsonl — these are the user's
#     own captured/labeled content, not install artifacts. Delete the whole
#     repo folder by hand afterward if you want those gone too.

param(
    [switch]$IncludeModelCache,
    [switch]$Force
)

$root = Split-Path -Parent $PSScriptRoot
$demoDir = $PSScriptRoot

Write-Output "1) Stopping any running live-translator processes..."
& (Join-Path $demoDir "tray_stop.ps1")
# Also sweep the non-demo (repo-root) launcher's own pid file, in case both
# ../start.cmd and demo/start.cmd were used on this machine.
$rootPidFile = Join-Path $root ".live-translator-pids"
if (Test-Path $rootPidFile) {
    foreach ($line in Get-Content $rootPidFile) {
        $procId = 0
        if ([int]::TryParse($line.Trim(), [ref]$procId) -and $procId -gt 0) {
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        }
    }
    Remove-Item $rootPidFile -Force -ErrorAction SilentlyContinue
}

$targets = @(
    (Join-Path $root "venv"),
    (Join-Path $root ".venv"),
    (Join-Path $root "llama-server"),
    (Join-Path $root "backend\models"),
    (Join-Path $demoDir "tray_launcher_error.log")
)
$logGlobs = @(
    (Join-Path $demoDir "*.log"),
    (Join-Path $root "*.log")
)

Write-Output ""
Write-Output "2) The following repo-local items will be deleted:"
$targets | ForEach-Object { Write-Output "  - $_" }
$logGlobs | ForEach-Object { Write-Output "  - $_ (log files)" }

if (-not $Force) {
    $answer = Read-Host "`n계속 진행하시겠습니까? (y/N)"
    if ($answer -ne "y" -and $answer -ne "Y") {
        Write-Output "취소했습니다."
        exit 0
    }
}

foreach ($t in $targets) {
    Remove-Item -Path $t -Recurse -Force -ErrorAction SilentlyContinue
}
foreach ($g in $logGlobs) {
    Remove-Item -Path $g -Force -ErrorAction SilentlyContinue
}

if ($IncludeModelCache) {
    Write-Output ""
    Write-Output "3) Removing globally-cached downloaded models (shared per-user-account cache)..."
    $hfHub = Join-Path $env:USERPROFILE ".cache\huggingface\hub"
    $torchHub = Join-Path $env:USERPROFILE ".cache\torch\hub"
    # backend/config.py: QWEN3_ASR_MODEL_ID + HALLUCINATION_GATE_MODEL — kept
    # in sync with those two constants; update here if either changes.
    $hfModelDirs = @(
        "models--Qwen--Qwen3-ASR-1.7B-hf",
        "models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2"
    )
    foreach ($d in $hfModelDirs) {
        $p = Join-Path $hfHub $d
        if (Test-Path $p) {
            Write-Output "  - $p"
            Remove-Item -Path $p -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    # silero-vad: torch.hub caches it under a repo-named subfolder under
    # torch\hub — remove only that subfolder, never the whole torch\hub
    # directory (other projects may use torch.hub too).
    Get-ChildItem -Path $torchHub -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like "*silero-vad*" } |
        ForEach-Object {
            Write-Output "  - $($_.FullName)"
            Remove-Item -Path $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
        }
} else {
    Write-Output ""
    Write-Output "3) 전역 모델 캐시(Hugging Face/torch.hub)는 건드리지 않았습니다. 같이 지우려면 -IncludeModelCache 옵션으로 다시 실행하세요."
}

Write-Output ""
Write-Output "완료. Chrome 확장은 chrome://extensions 에서 직접 '삭제' 해야 합니다 (demo/UNINSTALL.md 참고)."
