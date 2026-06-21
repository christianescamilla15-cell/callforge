# Local CI pipeline: unit tests -> boot a real instance -> live smoke -> report.
# ASCII-only on purpose (PowerShell 5.1 reads .ps1 as ANSI).
# Usage: powershell -File scripts\ci.ps1   (from anywhere)

param([int]$Port = 8901)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$failed = $false
$server = $null

Write-Host "=== STAGE 1: unit tests ===" -ForegroundColor Cyan
Push-Location $root
& $python -m pytest -q --tb=short tests
$testsExit = $LASTEXITCODE
Pop-Location
if ($testsExit -ne 0) {
    Write-Host "PIPELINE FAILED: unit tests" -ForegroundColor Red
    exit 1
}

Write-Host "=== STAGE 2: boot instance on :$Port ===" -ForegroundColor Cyan
$env:DATABASE_URL = "sqlite:///" + (Join-Path $env:TEMP "callforge_ci.db")
# Keep CI hermetic + deterministic: no cloud TTS, and exercise the SUPPORT
# pipeline (companion mode + ollama-primary are covered by unit tests).
$env:TTS_ENGINE = "kokoro"
$env:COMPANION_MODE = "false"
$env:LLM_PRIMARY = "groq"
Remove-Item -Force (Join-Path $env:TEMP "callforge_ci.db") -ErrorAction SilentlyContinue
$server = Start-Process -FilePath $python -ArgumentList "-m","uvicorn","callforge.presentation.api.app:app","--port","$Port" -WorkingDirectory $root -WindowStyle Hidden -PassThru

$ready = $false
foreach ($i in 1..30) {
    Start-Sleep -Seconds 1
    try {
        $h = Invoke-RestMethod "http://127.0.0.1:$Port/api/v1/health" -TimeoutSec 3
        if ($h.status -eq "ok") { $ready = $true; break }
    } catch {}
}
if (-not $ready) {
    Write-Host "PIPELINE FAILED: instance did not become healthy" -ForegroundColor Red
    if ($server) { Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue }
    exit 1
}
Write-Host "instance healthy"

Write-Host "=== STAGE 3: live smoke ===" -ForegroundColor Cyan
& $python -X utf8 (Join-Path $root "scripts\pipeline_smoke.py") "http://127.0.0.1:$Port"
if ($LASTEXITCODE -ne 0) { $failed = $true }

Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue

if ($failed) {
    Write-Host "=== PIPELINE FAILED (smoke) ===" -ForegroundColor Red
    exit 1
}
Write-Host "=== PIPELINE PASSED ===" -ForegroundColor Green
