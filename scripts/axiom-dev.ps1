# Start Axiom locally: the backend on 127.0.0.1:8000 and the dashboard on
# 127.0.0.1:3000. Both are detached, so closing this window does not stop them.
# Stop them with:  Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'uvicorn|next dev' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

$repo = Split-Path -Parent $PSScriptRoot
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$run = Join-Path $repo '.run'
New-Item -ItemType Directory -Path $run -Force | Out-Null

# Serve the page a run wrote, so a finished run can be opened in a browser. This is
# generated code running in your browser on this machine, not a sandbox; remove this
# line to keep the preview off.
$env:AXIOM_ALLOW_PREVIEW = "1"

# Let "Test it" run the checks a generated project declares, in its own workspace. That
# is the project's own test or build command as a local process with no provider
# credentials — faster feedback than a model call, and not a sandbox. Remove this line
# to keep execution off.
$env:AXIOM_ALLOW_EXECUTION = "1"

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
