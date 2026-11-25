# run-groq-proxy.ps1 - 启动 groq-proxy (Windows Task Scheduler entry)
$repo = "D:\Personal\Documents\默认项目\free-api-gateway"
$env:PYTHONIOENCODING = "utf-8"
$existing = Get-NetTCPConnection -LocalPort 3001 -State Listen -ErrorAction SilentlyContinue
if ($existing) { exit 0 }
Set-Location $repo
Start-Process -FilePath "python" -ArgumentList "groq-proxy.py" -WorkingDirectory $repo -WindowStyle Hidden