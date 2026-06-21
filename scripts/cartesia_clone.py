"""Clone Christian's voice into Cartesia (cuasi-human). Uses the clean 44s
reference. Prints the new voice id, then synthesizes + round-trips a test.

  python scripts/cartesia_clone.py [reference.wav] [name]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx

VERSION = "2026-03-01"
BASE = "https://api.cartesia.ai"


def _api_key() -> str:
    import os

    key = os.environ.get("CARTESIA_API_KEY", "")
    if not key:
        env = Path("C:/Users/DANNY/Desktop/callforge/.env")
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.startswith("CARTESIA_API_KEY="):
                    key = line.split("=", 1)[1].strip()
    if not key:
        sys.exit("Set CARTESIA_API_KEY (env or .env)")
    return key


KEY = _api_key()

ref = Path(sys.argv[1] if len(sys.argv) > 1 else "voices/christian_full.wav")
name = sys.argv[2] if len(sys.argv) > 2 else "Christian"

if not ref.exists():
    sys.exit(f"Reference not found: {ref}")

client = httpx.Client(timeout=120)
headers = {"Authorization": f"Bearer {KEY}", "Cartesia-Version": VERSION}

print(f"Cloning '{name}' from {ref} ({ref.stat().st_size // 1024} KB)...")
with open(ref, "rb") as f:
    resp = client.post(
        f"{BASE}/voices/clone",
        headers=headers,
        files={"clip": (ref.name, f, "audio/wav")},
        data={"name": name, "language": "es", "description": "Voz de Christian (CallForge)"},
    )

if resp.status_code not in (200, 201):
    print(f"CLONE FAILED [{resp.status_code}]: {resp.text[:400]}")
    sys.exit(1)

voice = resp.json()
voice_id = voice["id"]
print(f"OK -> cloned voice id: {voice_id}")
print(f"   name={voice.get('name')} language={voice.get('language')}")

# Synthesize a test phrase with the cloned voice
text = (
    "Hola, qué bueno que estás aquí. Tómate tu tiempo y cuéntame cómo te "
    "sientes hoy; te escucho con calma."
)
started = time.perf_counter()
tts = client.post(
    f"{BASE}/tts/bytes",
    headers={**headers, "Content-Type": "application/json"},
    json={
        "model_id": "sonic-3.5",
        "transcript": text,
        "voice": {"mode": "id", "id": voice_id},
        "language": "es",
        "output_format": {"container": "wav", "encoding": "pcm_s16le", "sample_rate": 44100},
    },
)
tts.raise_for_status()
out = Path("scripts/vc_out/cartesia_christian_clone.wav")
out.write_bytes(tts.content)
print(f"   synth {time.perf_counter() - started:.2f}s -> {out} ({len(tts.content)} bytes)")
print(f"\nPARA ACTIVAR: pon en .env  CARTESIA_VOICE_ID={voice_id}")
