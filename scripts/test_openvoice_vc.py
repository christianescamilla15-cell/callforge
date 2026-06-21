"""Experiment 4.5 (ROADMAP_VOZ): OpenVoice V2 tone-color conversion as a
real-time layer over Kokoro — Kokoro speaks, the converter re-timbres the
audio into Christian's voice. Measures RTF on this machine.

Run inside .venv-voice:
  .venv-voice\\Scripts\\python.exe -X utf8 scripts\\test_openvoice_vc.py [cpu|cuda]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

DEVICE = sys.argv[1] if len(sys.argv) > 1 else "cpu"

SRC_SAMPLE = Path("scripts/voice_ab/ef_dora_x10.wav")  # Kokoro voice (source timbre)
TGT_REF = Path("voices/christian_full.wav")  # Christian (target timbre)
OUT_DIR = Path("scripts/vc_out")
OUT_DIR.mkdir(exist_ok=True)

print(f"device={DEVICE}")

from huggingface_hub import snapshot_download  # noqa: E402

ckpt_root = Path(
    snapshot_download("myshell-ai/OpenVoiceV2", allow_patterns=["converter/*"])
)
converter_dir = ckpt_root / "converter"
print(f"checkpoints: {converter_dir}")

import torch  # noqa: E402

from openvoice.api import ToneColorConverter  # noqa: E402

converter = ToneColorConverter(str(converter_dir / "config.json"), device=DEVICE)
converter.load_ckpt(str(converter_dir / "checkpoint.pth"))

t0 = time.perf_counter()
src_se = converter.extract_se([str(SRC_SAMPLE)])
tgt_se = converter.extract_se([str(TGT_REF)])
print(f"speaker embeddings: {time.perf_counter() - t0:.1f}s (one-time)")

# Convert two Kokoro samples and measure steady-state RTF
import soundfile as sf  # noqa: E402

for i, sample in enumerate(
    [SRC_SAMPLE, Path("scripts/voice_ab/ef_dora_x095.wav")]
):
    out = OUT_DIR / f"converted_{i}.wav"
    info = sf.info(str(sample))
    duration = info.frames / info.samplerate
    t0 = time.perf_counter()
    with torch.inference_mode():
        converter.convert(
            audio_src_path=str(sample),
            src_se=src_se,
            tgt_se=tgt_se,
            output_path=str(out),
            message="@CallForge",
        )
    elapsed = time.perf_counter() - t0
    print(
        f"convert[{i}]: {elapsed:.2f}s para {duration:.1f}s de audio "
        f"(RTF {elapsed / duration:.3f}) -> {out}"
    )

print("OK - escucha scripts/vc_out/converted_*.wav")
