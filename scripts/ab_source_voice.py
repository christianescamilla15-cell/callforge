"""A/B: which Kokoro source voice transfers more naturally to Christian's
timbre through the OpenVoice converter. Run inside .venv-voice."""
import time
from pathlib import Path

from huggingface_hub import snapshot_download
from openvoice.api import ToneColorConverter

ckpt = Path(
    snapshot_download("myshell-ai/OpenVoiceV2", allow_patterns=["converter/*"])
) / "converter"
converter = ToneColorConverter(str(ckpt / "config.json"), device="cpu")
converter.load_ckpt(str(ckpt / "checkpoint.pth"))

tgt_se = converter.extract_se(["voices/christian_full.wav"])

for source in ["ef_dora", "em_alex"]:
    # src_se must describe the ACTUAL source timbre being removed
    src_se = converter.extract_se([f"scripts/voice_ab/{source}_x10.wav"])
    started = time.perf_counter()
    converter.convert(
        audio_src_path=f"scripts/vc_out/src_{source}.wav",
        src_se=src_se,
        tgt_se=tgt_se,
        output_path=f"scripts/vc_out/AB_fuente_{source}.wav",
        message="@CallForge",
    )
    print(f"AB_fuente_{source}.wav listo ({time.perf_counter() - started:.1f}s)")

print("Escucha scripts/vc_out/AB_fuente_ef_dora.wav vs AB_fuente_em_alex.wav")
