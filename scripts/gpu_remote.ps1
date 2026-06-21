# Point CallForge's companion at a remote GPU "big brain" (or revert to local).
# ASCII-only (PowerShell 5.1 reads .ps1 as ANSI).
#
# RunPod (proxy URL, https, no port):
#   scripts\gpu_remote.ps1 -Url https://abc123-11434.proxy.runpod.net
# Vast.ai / LAN box (raw ip:port):
#   scripts\gpu_remote.ps1 -Ip 1.2.3.4
#   scripts\gpu_remote.ps1 -Off
#
# Default model = the uncensored Apache-2.0 30B-A3B MoE (near-70B quality,
# ~70-120 tok/s on a 24GB card, ~19-20GB VRAM). Override with -Model.
#
# Updates .env (OLLAMA_REMOTE_URL / OLLAMA_REMOTE_MODEL), health-checks the
# remote Ollama, and reminds you to restart. When the box is down, CallForge
# falls back to the LOCAL model automatically - you never lose the companion.

param(
    [string]$Url = "",
    [string]$Ip = "",
    [int]$Port = 11434,
    [string]$Model = "huihui_ai/qwen3-abliterated:30b-a3b-instruct-2507-q4_K_M",
    [switch]$Off
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root ".env"
$lines = Get-Content $envFile

function Set-EnvVar([string[]]$content, [string]$key, [string]$value) {
    $found = $false
    $out = foreach ($l in $content) {
        if ($l -match "^$key=") { $found = $true; "$key=$value" } else { $l }
    }
    if (-not $found) { $out += "$key=$value" }
    return $out
}

if ($Off) {
    $lines = Set-EnvVar $lines "OLLAMA_REMOTE_URL" ""
    $lines = Set-EnvVar $lines "OLLAMA_REMOTE_MODEL" ""
    Set-Content -Path $envFile -Value $lines -Encoding UTF8
    Write-Host "Remote brain OFF. CallForge will use the LOCAL model." -ForegroundColor Green
} else {
    if ($Url) {
        $url = $Url.TrimEnd("/")
    } elseif ($Ip) {
        $url = "http://${Ip}:${Port}"
    } else {
        Write-Host "Need -Url <https://...> or -Ip <address> (or -Off)." -ForegroundColor Yellow; exit 1
    }
    Write-Host "Checking remote Ollama at $url ..."
    try {
        $tags = Invoke-RestMethod "$url/api/tags" -TimeoutSec 15
        $models = ($tags.models | ForEach-Object { $_.name }) -join ", "
        Write-Host "  reachable. models: $models" -ForegroundColor Green
        if ($Model -and ($tags.models.name -notcontains $Model)) {
            Write-Host "  WARNING: '$Model' not pulled on the box yet. Run on the box: ollama pull $Model" -ForegroundColor Yellow
        }
    } catch {
        Write-Host "  NOT reachable: $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "  (is the box up, Ollama running, port $Port open / tunneled?)" -ForegroundColor Yellow
        exit 1
    }
    $lines = Set-EnvVar $lines "OLLAMA_REMOTE_URL" $url
    if ($Model) { $lines = Set-EnvVar $lines "OLLAMA_REMOTE_MODEL" $Model }
    $lines = Set-EnvVar $lines "LLM_PRIMARY" "ollama"
    Set-Content -Path $envFile -Value $lines -Encoding UTF8
    Write-Host "Remote brain ON -> $url (model: $Model)" -ForegroundColor Green
}

Write-Host ""
Write-Host "Apply it:  C:\Users\DANNY\dev\tools\nssm\nssm.exe restart callforge  (elevated)"
