$ErrorActionPreference = 'Stop'
$courseRoot = $PSScriptRoot
Set-Location -LiteralPath $courseRoot
$courseReady = $false
try { $null = Invoke-RestMethod http://127.0.0.1:8766/health -TimeoutSec 2; $courseReady = $true } catch {}
if (-not $courseReady) {
    Start-Process -FilePath (Join-Path $courseRoot '.venv/Scripts/python.exe') -WorkingDirectory $courseRoot -WindowStyle Hidden -ArgumentList @('src/data_server.py','--port','8766') -RedirectStandardOutput (Join-Path $courseRoot 'outputs/workbench-start.out.log') -RedirectStandardError (Join-Path $courseRoot 'outputs/workbench-start.err.log') | Out-Null
}
Write-Output 'Open http://127.0.0.1:8766/ for the research workbench. Basic prototype runs without a model service.'
