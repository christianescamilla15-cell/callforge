# Installs the Chatterbox voice-cloning micro-service as a Windows service.
# ASCII-only on purpose (PowerShell 5.1 reads .ps1 as ANSI).
# Run from an elevated PowerShell prompt AFTER .venv-voice exists.

param(
    [string]$ServiceName = "callforge-voice",
    [int]$Port = 8002,
    [string]$Device = "cuda"
)

$ErrorActionPreference = "Stop"

$nssm = "C:\Users\DANNY\dev\tools\nssm\nssm.exe"
$root = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $root ".venv-voice\Scripts\python.exe"
$logs = Join-Path $root "logs"

if (-not (Test-Path $venvPython)) {
    Write-Host "Missing .venv-voice. See README (voice section)." -ForegroundColor Yellow
    exit 1
}

New-Item -ItemType Directory -Force $logs | Out-Null
New-Item -ItemType Directory -Force (Join-Path $root "voices") | Out-Null

$existing = Get-Service $ServiceName -ErrorAction SilentlyContinue
if ($null -eq $existing) {
    & $nssm install $ServiceName $venvPython "-m" "uvicorn" "voice_server:app" "--host" "127.0.0.1" "--port" "$Port"
    & $nssm set $ServiceName AppDirectory $root
    & $nssm set $ServiceName AppEnvironmentExtra "CHATTERBOX_DEVICE=$Device"
    & $nssm set $ServiceName AppStdout (Join-Path $logs "voice.out.log")
    & $nssm set $ServiceName AppStderr (Join-Path $logs "voice.err.log")
    & $nssm set $ServiceName Start SERVICE_AUTO_START
}
& $nssm restart $ServiceName

Write-Host "Service '$ServiceName' running on port $Port (device=$Device)." -ForegroundColor Green
Write-Host "Remember: env changes require 'nssm restart $ServiceName'."
