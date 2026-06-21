"""Generate the voice A/B matrix: 3 Spanish Kokoro voices x 3 speeds.
Listen to scripts/voice_ab/*.wav and set the winner in .env
(TTS_VOICE / TTS_SPEED)."""
import sys
from pathlib import Path

sys.path.insert(0, "src")
from callforge.infrastructure.voice.normalize import normalize_for_tts  # noqa: E402
from callforge.infrastructure.voice.tts import KokoroTTS  # noqa: E402

SCRIPT = (
    "Hola, gracias por comunicarte con soporte. Entiendo que tu internet "
    "no funciona desde ayer. Vamos a resolverlo paso a paso: primero, "
    "desconecta el modem 60 segundos y vuelve a conectarlo. ¿La luz quedó "
    "fija en verde? Si el problema persiste, escalaré tu caso de inmediato."
)

VOICES = ["ef_dora", "em_alex", "em_santa"]
SPEEDS = [0.95, 1.0, 1.1]

out_dir = Path("scripts/voice_ab")
out_dir.mkdir(exist_ok=True)
text = normalize_for_tts(SCRIPT)

for voice in VOICES:
    for speed in SPEEDS:
        tts = KokoroTTS(
            "models/kokoro-v1.0.onnx", "models/voices-v1.0.bin",
            voice=voice, lang="es", speed=speed,
        )
        wav = tts.synthesize(text)
        name = f"{voice}_x{str(speed).replace('.', '')}.wav"
        (out_dir / name).write_bytes(wav)
        print(f"  {name}: {len(wav) // 1024} KB")

print(f"\nListos en {out_dir.resolve()} - escuchalos y elige.")
