# Installs CallForge + Ollama as Windows services via nssm.
# ASCII-only on purpose (PowerShell 5.1 reads .ps1 as ANSI).
# Must run elevated.

$ErrorActionPreference = "Stop"

$nssm = "C:\Users\DANNY\dev\tools\nssm\nssm.exe"
$root = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
$ollamaExe = "C:\Users\DANNY\AppData\Local\Programs\Ollama\ollama.exe"
$logs = Join-Path $root "logs"

New-Item -ItemType Directory -Force $logs | Out-Null

# --- Ollama service ---
# LocalSystem has its own profile, so point OLLAMA_MODELS at the user's models.
$existing = Get-Service ollama -ErrorAction SilentlyContinue
if ($null -eq $existing) {
    & $nssm install ollama $ollamaExe serve
    & $nssm set ollama AppEnvironmentExtra "OLLAMA_MODELS=C:\Users\DANNY\.ollama\models"
    & $nssm set ollama AppStdout (Join-Path $logs "ollama.out.log")
    & $nssm set ollama AppStderr (Join-Path $logs "ollama.err.log")
    & $nssm set ollama Start SERVICE_AUTO_START
}
& $nssm restart ollama

# --- CallForge service ---
$existing = Get-Service callforge -ErrorAction SilentlyContinue
if ($null -eq $existing) {
    & $nssm install callforge $venvPython "-m" "uvicorn" "callforge.presentation.api.app:app" "--host" "0.0.0.0" "--port" "8000"
    & $nssm set callforge AppDirectory $root
    & $nssm set callforge AppStdout (Join-Path $logs "callforge.out.log")
    & $nssm set callforge AppStderr (Join-Path $logs "callforge.err.log")
    & $nssm set callforge Start SERVICE_AUTO_START
}
& $nssm restart callforge

Write-Host "Done. Services:"
Get-Service ollama, callforge | Format-Table Name, Status -AutoSize
