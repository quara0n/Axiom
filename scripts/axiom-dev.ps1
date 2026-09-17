# Start Axiom locally: the backend on 127.0.0.1:8000 and the dashboard on
# 127.0.0.1:3000. Both are detached, so closing this window does not stop them.
# Stop them with:  Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'uvicorn|next dev' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

$repo = Split-Path -Parent $PSScriptRoot
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$run = Join-Path $repo '.run'
New-Item -ItemType Directory -Path $run -Force | Out-Null

Start-Process -FilePath (Join-Path $repo '.venv\Scripts\python.exe') `
    -ArgumentList '-m', 'uvicorn', 'backend.app:app', '--host', '127.0.0.1', '--port', '8000' `
    -WorkingDirectory $repo -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $run "backend-$stamp.log") `
    -RedirectStandardError (Join-Path $run "backend-$stamp.err.log") | Out-Null

Start-Process -FilePath (Get-Command npm.cmd).Source -ArgumentList 'run', 'dev:local' `
    -WorkingDirectory $repo -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $run "next-dev-$stamp.log") `
    -RedirectStandardError (Join-Path $run "next-dev-$stamp.err.log") | Out-Null

Start-Sleep -Seconds 18
$health = try { (Invoke-WebRequest 'http://127.0.0.1:8000/api/health' -UseBasicParsing -TimeoutSec 8).StatusCode } catch { "down" }
$page = try { (Invoke-WebRequest 'http://127.0.0.1:3000/' -UseBasicParsing -TimeoutSec 8).StatusCode } catch { "down" }
Write-Host "Axiom      http://127.0.0.1:3000   ($page)"
Write-Host "API        http://127.0.0.1:8000   ($health)"
Write-Host "Logs       $run"
