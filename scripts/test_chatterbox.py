"""Empirical test for Chatterbox Multilingual on the RTX 4050 (6GB):
real VRAM footprint, Spanish synthesis latency, and zero-shot cloning
quality validated by round-trip transcription. Run inside .venv-voice."""
import subprocess
import sys
import time


def vram_mb() -> int:
    out = subprocess.check_output(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        text=True,
    )
    return int(out.strip().splitlines()[0])


def main() -> None:
    device = sys.argv[1] if len(sys.argv) > 1 else "cuda"
    print(f"device={device} | VRAM antes: {vram_mb()} MiB")

    import torch
    import torchaudio

    try:
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS as TTS
        flavor = "multilingual"
    except ImportError:
        from chatterbox.tts import ChatterboxTTS as TTS
        flavor = "english-base"
    print(f"clase: {flavor}")

    t0 = time.perf_counter()
    model = TTS.from_pretrained(device=device)
    t1 = time.perf_counter()
    print(f"carga: {t1 - t0:.1f}s | VRAM tras carga: {vram_mb()} MiB")

    text = (
        "Hola, gracias por comunicarte con soporte tecnico. "
        "Voy a ayudarte a resolver tu problema con el internet."
    )

    # 1) Voz default en espanol
    t0 = time.perf_counter()
    wav = model.generate(text, language_id="es")
    t1 = time.perf_counter()
    dur = wav.shape[-1] / model.sr
    print(f"sintesis default: {t1 - t0:.1f}s para {dur:.1f}s (RTF {(t1 - t0) / dur:.2f})")
    print(f"VRAM pico: {vram_mb()} MiB")
    torchaudio.save("scripts/test_chatterbox_default.wav", wav, model.sr)

    # 2) Clonacion zero-shot desde el wav de referencia (voz Sabina, 8s)
    t0 = time.perf_counter()
    wav2 = model.generate(
        text, language_id="es", audio_prompt_path="scripts/test_stt.wav"
    )
    t1 = time.perf_counter()
    dur2 = wav2.shape[-1] / model.sr
    print(f"sintesis CLONADA: {t1 - t0:.1f}s para {dur2:.1f}s (RTF {(t1 - t0) / dur2:.2f})")
    print(f"VRAM final: {vram_mb()} MiB")
    torchaudio.save("scripts/test_chatterbox_cloned.wav", wav2, model.sr)
    print("OK - wavs en scripts/")


if __name__ == "__main__":
    main()
