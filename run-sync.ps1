# run-sync.ps1 - 定时执行 sync-models-win.py (Windows Task Scheduler entry)
$ErrorActionPreference = "Continue"
$repo = "D:\Personal\Documents\默认项目\free-api-gateway"
$logDir = Join-Path $repo "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$log = Join-Path $logDir "sync-task-$stamp.log"
$env:PYTHONIOENCODING = "utf-8"
Set-Location $repo
try {
    & python "sync-models-win.py" *> $log
} catch {
    "ERROR: $($_.Exception.Message)" | Out-File -FilePath $log -Encoding utf8
}