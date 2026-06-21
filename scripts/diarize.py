"""Speaker diarization -> one clean reference clip per detected speaker.

Splits a multi-speaker recording into who-speaks-when (sherpa-onnx, offline,
no HF token), then for each speaker cuts their longest clean continuous
segment from the 24kHz source as a clone-ready WAV in voice_sources/refs/.

  .venv-voice\\Scripts\\python.exe scripts\\diarize.py voice_sources/section_16k.wav
  ... --source voice_sources/section_24k.wav --num-speakers 0  (0 = auto)
"""
from __future__ import annotations

import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import sherpa_onnx

FFMPEG = r"C:\Users\DANNY\dev\tools\ffmpeg\ffmpeg-8.1.1-essentials_build\bin\ffmpeg.exe"
SEG = "models/diarization/sherpa-onnx-pyannote-segmentation-3-0/model.onnx"
EMB = "models/diarization/embedding.onnx"
MIN_REF_SECONDS = 12.0   # don't bother with clips shorter than this
MAX_REF_SECONDS = 60.0   # cap a reference clip


def _arg(flag, default=None):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    wav16k = sys.argv[1]
    source = _arg("--source", "voice_sources/section_24k.wav")
    num_speakers = int(_arg("--num-speakers", "0"))  # 0 -> auto via threshold

    import wave

    with wave.open(wav16k) as w:
        sr = w.getframerate()
        import numpy as np

        samples = (
            np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
            / 32768.0
        )

    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=SEG),
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=EMB),
        clustering=sherpa_onnx.FastClusteringConfig(
            num_clusters=num_speakers if num_speakers > 0 else -1,
            threshold=0.5,
        ),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    sd = sherpa_onnx.OfflineSpeakerDiarization(config)
    print(f"Diarizando {wav16k} ({len(samples)/sr:.0f}s)... esto tarda un poco.")
    result = sd.process(samples).sort_by_start_time()

    # Aggregate per speaker. For cloning we don't need ONE long continuous
    # segment: gather the speaker's cleanest segments and concatenate them
    # into a ~40s reference. Uses total talk time, not a single best slice.
    MIN_SEG = float(_arg("--min-seg", "2.0"))     # ignore tiny noise blips
    TARGET_REF = 40.0                              # reference audio per speaker
    MIN_TOTAL = float(_arg("--min-total", "18.0")) # speakers who talk this much
    segs_by_spk = defaultdict(list)
    totals = defaultdict(float)
    for seg in result:
        dur = seg.end - seg.start
        totals[seg.speaker] += dur
        if dur >= MIN_SEG:
            segs_by_spk[seg.speaker].append((seg.start, seg.end, dur))

    out_dir = Path("voice_sources/refs")
    out_dir.mkdir(parents=True, exist_ok=True)
    top = sorted(totals, key=lambda s: totals[s], reverse=True)
    print(f"\nDetectados {len(totals)} clusters; armo referencia de los principales:")

    made = []
    rank = 0
    for spk in top:
        if totals[spk] < MIN_TOTAL:
            continue
        # pick longest segments until we reach TARGET_REF seconds
        chosen, acc = [], 0.0
        for start, end, dur in sorted(segs_by_spk[spk], key=lambda s: s[2], reverse=True):
            take = min(dur, MAX_REF_SECONDS)
            chosen.append((start, start + take))
            acc += take
            if acc >= TARGET_REF:
                break
        if acc < MIN_REF_SECONDS:
            continue
        chosen.sort()
        out = out_dir / f"voz_{rank}.wav"
        # build a concat of the chosen ranges from the 24k source
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            parts = []
            for i, (s, e) in enumerate(chosen):
                p = Path(tmp) / f"p{i}.wav"
                subprocess.run(
                    [FFMPEG, "-y", "-loglevel", "error", "-ss", str(s), "-to", str(e),
                     "-i", source, "-ac", "1", "-ar", "24000", str(p)],
                    check=True,
                )
                parts.append(p)
            listf = Path(tmp) / "list.txt"
            listf.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts))
            subprocess.run(
                [FFMPEG, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                 "-i", str(listf), "-c", "copy", str(out)],
                check=True,
            )
        print(f"  voz_{rank}: {totals[spk]:.0f}s totales (cluster {spk}) -> {acc:.0f}s de referencia")
        made.append((rank, out, acc))
        rank += 1
        if rank >= 6:  # top 6 voices is plenty to choose from
            break

    print(f"\n{len(made)} referencias listas en voice_sources/refs/:")
    for rank, out, d in made:
        print(f"  {out}  (~{d:.0f}s)")
    print("\nEscucha cada una y clona la/las que quieras:")
    print("  python scripts/cartesia_clone.py voice_sources/refs/voz_0.wav nombre")


if __name__ == "__main__":
    main()
