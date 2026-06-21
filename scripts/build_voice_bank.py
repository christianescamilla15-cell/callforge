"""Build the cloned-voice phrase bank with Chatterbox (Fase 2 of ROADMAP_VOZ).

Synthesizes every phrase in callforge.infrastructure.voice.phrases with the
given reference voice and seeds the runtime TTS cache, so fixed phrases come
out in the cloned voice with zero serving code.

Run inside .venv-voice (needs torch + chatterbox), with the LLM idle:
  .venv-voice\\Scripts\\python.exe -X utf8 scripts\\build_voice_bank.py --ref voices\\christian.wav
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")
from callforge.infrastructure.voice.normalize import normalize_for_tts  # noqa: E402
from callforge.infrastructure.voice.phrases import FIXED_PHRASES  # noqa: E402
from callforge.infrastructure.voice.tts import tts_cache_key  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ref", required=True, help="reference WAV (10-20s, clean)")
    parser.add_argument("--cache-dir", default="cache/tts")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--language", default="es")
    parser.add_argument(
        "--engine", default="chatterbox", choices=["chatterbox", "qwen3tts"]
    )
    parser.add_argument(
        "--ref-text", default="", help="transcript of --ref (required by qwen3tts)"
    )
    args = parser.parse_args()

    ref = Path(args.ref)
    if not ref.exists():
        sys.exit(f"Reference audio not found: {ref}")
    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    import torch

    if args.engine == "qwen3tts":
        if not args.ref_text:
            sys.exit("--ref-text is required for qwen3tts (transcript of --ref)")
        import soundfile as sf
        from qwen_tts import Qwen3TTSModel

        print(f"Loading Qwen3-TTS on {args.device}...")
        model = Qwen3TTSModel.from_pretrained(
            "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
            device_map=args.device,
            dtype=torch.bfloat16 if args.device.startswith("cuda") else torch.float32,
            attn_implementation="sdpa",
        )
        language = {"es": "Spanish"}.get(args.language, args.language)

        def synth(text: str):
            wavs, sr = model.generate_voice_clone(
                text=text,
                language=language,
                ref_audio=str(ref),
                ref_text=args.ref_text,
            )
            wav = wavs[0] if isinstance(wavs, (list, tuple)) else wavs
            return wav, sr

        def save(wav, sr, target):
            sf.write(str(target), wav, sr)
            return len(wav) / sr
    else:
        import torchaudio
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS

        print(f"Loading Chatterbox on {args.device}...")
        model = ChatterboxMultilingualTTS.from_pretrained(device=args.device)

        def synth(text: str):
            wav = model.generate(
                text, language_id=args.language, audio_prompt_path=str(ref)
            )
            return wav, model.sr

        def save(wav, sr, target):
            torchaudio.save(str(target), wav.cpu(), sr, format="wav")
            return wav.shape[-1] / sr

    for phrase_id, text in FIXED_PHRASES.items():
        speakable = normalize_for_tts(text)
        started = time.perf_counter()
        with torch.inference_mode():
            wav, sr = synth(speakable)
        target = cache_dir / f"{tts_cache_key(text)}.wav"
        duration = save(wav, sr, target)
        print(
            f"  {phrase_id}: {duration:.1f}s audio "
            f"({time.perf_counter() - started:.1f}s) -> {target.name}"
        )

    print("\nVoice bank ready. The running service serves these from cache")
    print("with NO restart needed (CachedTTS reads the directory per request).")


if __name__ == "__main__":
    main()
