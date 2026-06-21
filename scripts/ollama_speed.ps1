# Enable Ollama flash-attention + quantized KV cache (faster generation, less
# VRAM -> less offload under GPU contention). Preserves OLLAMA_MODELS.
# Run elevated. ASCII-only.
$nssm = "C:\Users\DANNY\dev\tools\nssm\nssm.exe"
& $nssm set ollama AppEnvironmentExtra `
    "OLLAMA_MODELS=C:\Users\DANNY\.ollama\models" `
    "OLLAMA_FLASH_ATTENTION=1" `
    "OLLAMA_KV_CACHE_TYPE=q8_0"
& $nssm restart ollama
Start-Sleep -Seconds 3
& $nssm restart callforge
Write-Host "ollama: flash-attention + q8_0 KV cache ON; both services restarted."
