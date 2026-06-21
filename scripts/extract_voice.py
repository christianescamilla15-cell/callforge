"""Extract a clean voice reference from a video/audio file for cloning.

  # full audio of a file (to inspect):
  python scripts/extract_voice.py voice_sources/clip.mp4

  # trim one speaker's segment (start/end in seconds or mm:ss):
  python scripts/extract_voice.py voice_sources/clip.mp4 --start 0:12 --end 0:48 --name nora

Outputs a mono 24kHz WAV in voice_sources/refs/ ready to upload in the panel
(the + button) or clone via scripts/cartesia_clone.py. For multi-speaker
videos that need AUTOMATIC per-character separation, that's the diarization
step (pyannote) - tell me and I'll wire it.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

FFMPEG = r"C:\Users\DANNY\dev\tools\ffmpeg\ffmpeg-8.1.1-essentials_build\bin\ffmpeg.exe"
FFPROBE = r"C:\Users\DANNY\dev\tools\ffmpeg\ffmpeg-8.1.1-essentials_build\bin\ffprobe.exe"


def parse_args() -> dict:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    args = {"input": sys.argv[1], "start": None, "end": None, "name": None}
    i = 2
    while i < len(sys.argv):
        key = sys.argv[i].lstrip("-")
        if key in args and i + 1 < len(sys.argv):
            args[key] = sys.argv[i + 1]
            i += 2
        else:
            i += 1
    return args


def main() -> None:
    args = parse_args()
    src = Path(args["input"])
    if not src.exists():
        sys.exit(f"No existe: {src}")

    out_dir = Path("voice_sources/refs")
    out_dir.mkdir(parents=True, exist_ok=True)
    name = args["name"] or src.stem
    out = out_dir / f"{name}.wav"

    cmd = [FFMPEG, "-y", "-loglevel", "error"]
    if args["start"]:
        cmd += ["-ss", str(args["start"])]
    if args["end"]:
        cmd += ["-to", str(args["end"])]
    cmd += ["-i", str(src), "-ac", "1", "-ar", "24000", "-vn", str(out)]

    subprocess.run(cmd, check=True)

    dur = subprocess.check_output(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(out)], text=True
    ).strip()
    print(f"OK -> {out}  ({float(dur):.1f}s)")
    print("Subelo con el boton + del panel, o clona: "
          f"python scripts/cartesia_clone.py {out} {name}")


if __name__ == "__main__":
    main()
