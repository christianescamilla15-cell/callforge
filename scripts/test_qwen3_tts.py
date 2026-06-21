"""Experiment: Qwen3-TTS 0.6B on the RTX 4050 - the candidate for the
quality leap WITHOUT new hardware. Measures load, VRAM, warm RTF, and
clones Christian's voice from the 15s reference. Run inside .venv-voice."""
import subprocess
import sys
import time

DEVICE = sys.argv[1] if len(sys.argv) > 1 else "cuda:0"

REF_AUDIO = "voices/christian.wav"
REF_TEXT = (
    "Hola, ¿qué tal? Me llamo Cristian, tengo 28 años, actualmente vivo en la "
    "Ciudad de México. Me gusta hacer un poco de deporte, un poco de "
    "calistenia. También puedo decir que me gusta de vez en cuando investigar "
    "artículos científicos"
)

TEXTS = [
    "Hola, gracias por comunicarte con soporte. ¿En qué puedo ayudarte hoy?",
    (
        "Ya revisé tu caso y encontré el problema con tu conexión. Vamos a "
        "resolverlo paso a paso, no te preocupes."
    ),
]


def vram_mb() -> int:
    out = subprocess.check_output(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        text=True,
    )
    return int(out.strip().splitlines()[0])


print(f"device={DEVICE} | VRAM antes: {vram_mb()} MiB")

import soundfile as sf  # noqa: E402
import torch  # noqa: E402
from qwen_tts import Qwen3TTSModel  # noqa: E402

t0 = time.perf_counter()
# flash-attn no compila en Windows -> sdpa
model = Qwen3TTSModel.from_pretrained(
    "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
    device_map=DEVICE,
    dtype=torch.bfloat16,
    attn_implementation="sdpa",
)
print(f"carga: {time.perf_counter() - t0:.1f}s | VRAM tras carga: {vram_mb()} MiB")

for i, text in enumerate(TEXTS):
    t0 = time.perf_counter()
    wavs, sr = model.generate_voice_clone(
        text=text,
        language="Spanish",
        ref_audio=REF_AUDIO,
        ref_text=REF_TEXT,
    )
    elapsed = time.perf_counter() - t0
    wav = wavs[0] if isinstance(wavs, (list, tuple)) else wavs
    duration = len(wav) / sr
    sf.write(f"scripts/vc_out/qwen3tts_{i}.wav", wav, sr)
    print(
        f"clone[{i}]: {elapsed:.2f}s para {duration:.1f}s "
        f"(RTF {elapsed / duration:.2f}) | VRAM: {vram_mb()} MiB | sr={sr}"
    )

print("OK - escucha scripts/vc_out/qwen3tts_*.wav")
