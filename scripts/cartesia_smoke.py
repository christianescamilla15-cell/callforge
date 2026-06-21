"""Cartesia live smoke: list Spanish voices, then synthesize CallForge
support phrases and measure latency. Needs CARTESIA_API_KEY in env or .env.

  python scripts/cartesia_smoke.py            # list es voices + synth with first
  python scripts/cartesia_smoke.py <voice_id> # synth with a specific voice
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import httpx

BASE = "https://api.cartesia.ai"
VERSION = "2026-03-01"


def _api_key() -> str:
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


def main() -> None:
    key = _api_key()
    headers = {"Authorization": f"Bearer {key}", "Cartesia-Version": VERSION}
    client = httpx.Client(timeout=60)

    voices = client.get(
        f"{BASE}/voices",
        headers=headers,
        params={"language": "es", "limit": 20, "expand[]": "preview_file_url"},
    )
    voices.raise_for_status()
    data = voices.json()
    items = data.get("data", data) if isinstance(data, dict) else data
    print(f"=== {len(items)} voces en español ===")
    for v in items:
        print(f"  {v['id']}  {v.get('name','?'):28} {v.get('gender','?')}")

    voice_id = sys.argv[1] if len(sys.argv) > 1 else (items[0]["id"] if items else None)
    if not voice_id:
        sys.exit("No Spanish voices returned")
    print(f"\nUsing voice: {voice_id}")

    out_dir = Path("scripts/vc_out")
    out_dir.mkdir(exist_ok=True)
    phrases = [
        "Hola, soy el asistente de soporte. ¿En qué puedo ayudarte hoy?",
        (
            "Ya revisé tu caso y encontré el problema con tu conexión. Vamos a "
            "resolverlo paso a paso, no te preocupes."
        ),
    ]
    for i, text in enumerate(phrases):
        started = time.perf_counter()
        resp = client.post(
            f"{BASE}/tts/bytes",
            headers={**headers, "Content-Type": "application/json"},
            json={
                "model_id": "sonic-3.5",
                "transcript": text,
                "voice": {"mode": "id", "id": voice_id},
                "language": "es",
                "output_format": {
                    "container": "wav",
                    "encoding": "pcm_s16le",
                    "sample_rate": 44100,
                },
            },
        )
        elapsed = time.perf_counter() - started
        resp.raise_for_status()
        target = out_dir / f"cartesia_{i}.wav"
        target.write_bytes(resp.content)
        print(f"  phrase[{i}]: {elapsed:.2f}s -> {target} ({len(resp.content)} bytes)")

    print("\nEscucha scripts/vc_out/cartesia_*.wav")


if __name__ == "__main__":
    main()
