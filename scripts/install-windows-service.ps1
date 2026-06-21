# Installs CallForge as a Windows service using nssm.
# ASCII-only on purpose (PowerShell 5.1 reads .ps1 as ANSI).
# Run from an elevated PowerShell prompt.

param(
    [string]$ServiceName = "callforge",
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Host "Missing venv. Create it first:" -ForegroundColor Yellow
    Write-Host "  python -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install -e ."
    exit 1
}

$nssm = Get-Command nssm -ErrorAction SilentlyContinue
if ($null -eq $nssm) {
    Write-Host "nssm not found on PATH. Install it (e.g. winget install nssm) and retry." -ForegroundColor Yellow
    exit 1
}

nssm install $ServiceName $venvPython "-m" "uvicorn" "callforge.presentation.api.app:app" "--host" "0.0.0.0" "--port" "$Port"
nssm set $ServiceName AppDirectory $root
nssm set $ServiceName AppStdout (Join-Path $root "logs\callforge.out.log")
nssm set $ServiceName AppStderr (Join-Path $root "logs\callforge.err.log")
nssm set $ServiceName Start SERVICE_AUTO_START

New-Item -ItemType Directory -Force (Join-Path $root "logs") | Out-Null

nssm start $ServiceName
Write-Host "Service '$ServiceName' installed and started on port $Port." -ForegroundColor Green
Write-Host "Remember: env var changes require 'nssm restart $ServiceName' (not SCM stop/start)."
