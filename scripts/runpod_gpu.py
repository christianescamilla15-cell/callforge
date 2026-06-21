"""On-demand GPU brain via RunPod, in ONE command: deploy + pull + wire CallForge.

This automates the whole manual dance (deploy pod, install Ollama, serve, pull,
point CallForge) into `start`. It uses the official `ollama/ollama` image, which
already runs `ollama serve` on 0.0.0.0:11434 at boot -- so there's NO manual
terminal install/serve step. The script then pulls the model via the proxy and
writes OLLAMA_REMOTE_URL into .env for you.

Setup (one time):
  1. RunPod account + credit + API key.
  2. .venv\\Scripts\\python.exe -m pip install runpod
  3. Put RUNPOD_API_KEY=... in .env (or the environment).
  (Optional) Create a Network Volume to keep the model across stops, pass --volume <id>.

Usage:
  python scripts/runpod_gpu.py start                  # 30B-A3B on an A6000, then wires .env
  python scripts/runpod_gpu.py start --model huihui_ai/llama3.3-abliterated:70b-instruct-q4_K_M --gpu "NVIDIA RTX A6000" --disk 70
  python scripts/runpod_gpu.py stop                   # stop billing (container wiped unless --volume was used)
  python scripts/runpod_gpu.py terminate              # destroy the pod
  python scripts/runpod_gpu.py status

After `start` finishes it prints the restart command:
  nssm restart callforge   (elevated)   -- or run scripts\\gpu_remote.ps1 -Off to revert.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
STATE = ROOT / "data" / "runpod_pod.txt"

DEFAULT_MODEL = "huihui_ai/qwen3-abliterated:30b-a3b-instruct-2507-q4_K_M"
DEFAULT_GPU = "NVIDIA RTX A6000"   # 48GB, best value; see runpod.get_gpus() for ids
IMAGE = "ollama/ollama:latest"     # boots `ollama serve` on 0.0.0.0:11434 by itself
PORT = 11434


def _api_key() -> str:
    key = os.environ.get("RUNPOD_API_KEY", "")
    if not key and ENV.exists():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            if line.startswith("RUNPOD_API_KEY="):
                key = line.split("=", 1)[1].strip()
    if not key:
        sys.exit("Set RUNPOD_API_KEY (env or .env). See the header for setup.")
    return key


def _set_env(updates: dict[str, str]) -> None:
    """Update/append .env keys, preserving everything else."""
    lines = ENV.read_text(encoding="utf-8").splitlines() if ENV.exists() else []
    for key, value in updates.items():
        found = False
        for i, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[i] = f"{key}={value}"
                found = True
                break
        if not found:
            lines.append(f"{key}={value}")
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _curl(url: str, *, data: str | None = None, timeout: int = 30) -> tuple[int, str]:
    """HTTP via curl (the RunPod proxy WAF rejects python-urllib's UA; curl works)."""
    cmd = ["curl", "-s", "--max-time", str(timeout), "-o", "-", "-w", "\n%{http_code}", url]
    if data is not None:
        cmd[1:1] = ["-X", "POST", "-H", "Content-Type: application/json", "--data-binary", data]
    out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8").stdout or "\n0"
    body, _, code = out.rpartition("\n")
    return int(code or 0), body


def _wait_proxy(url: str, tries: int = 60) -> bool:
    for _ in range(tries):
        code, _ = _curl(f"{url}/api/version", timeout=15)
        if code == 200:
            return True
        time.sleep(5)
    return False


def _pull(url: str, model: str) -> bool:
    """Stream /api/pull, printing status transitions; returns True on success."""
    cmd = [
        "curl", "-s", "--max-time", "3600", "-X", "POST", f"{url}/api/pull",
        "-H", "Content-Type: application/json",
        "--data-binary", json.dumps({"model": model}),
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True, encoding="utf-8")
    last = ""
    ok = False
    for line in proc.stdout:  # type: ignore[union-attr]
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        status = d.get("status", "")
        if d.get("error"):
            print(f"  ERROR: {d['error']}")
            return False
        if status == "success":
            ok = True
        if status != last:
            print(f"  {status}")
            last = status
    proc.wait()
    return ok


def start(args: argparse.Namespace) -> None:
    import runpod

    runpod.api_key = _api_key()
    kwargs = dict(
        name="callforge-brain",
        image_name=IMAGE,
        gpu_type_id=args.gpu,
        gpu_count=1,
        ports=f"{PORT}/http",
        container_disk_in_gb=args.disk,
        env={"OLLAMA_HOST": "0.0.0.0"},
    )
    if args.cloud:
        kwargs["cloud_type"] = args.cloud
    if args.volume:
        kwargs["network_volume_id"] = args.volume
        kwargs["volume_mount_path"] = "/root/.ollama"  # where ollama/ollama stores models

    print(f"Deploying {args.gpu} (disk {args.disk}GB, image {IMAGE})...")
    pod = runpod.create_pod(**kwargs)
    pod_id = pod["id"]
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(pod_id)
    url = f"https://{pod_id}-{PORT}.proxy.runpod.net"
    print(f"pod {pod_id} -> {url}")

    print("Waiting for RUNNING...")
    for _ in range(60):
        time.sleep(5)
        info = runpod.get_pod(pod_id)
        status = info.get("desiredStatus") or info.get("status")
        if status == "RUNNING":
            break
        print(f"  ...{status}")

    print("Waiting for Ollama to answer through the proxy...")
    if not _wait_proxy(url):
        sys.exit("Ollama never answered. Check the pod in the RunPod console.")

    print(f"Pulling {args.model} (datacenter download)...")
    if not _pull(url, args.model):
        sys.exit("Pull failed. Pod is up; retry the pull or check disk space.")

    _set_env({
        "OLLAMA_REMOTE_URL": url,
        "OLLAMA_REMOTE_MODEL": args.model,
        "LLM_PRIMARY": "ollama",
    })
    print(f"\nREADY. CallForge wired to {url}")
    print(f"  model: {args.model}")
    print("Apply it (elevated):  C:\\Users\\DANNY\\dev\\tools\\nssm\\nssm.exe restart callforge")
    print("Revert to local:      scripts\\gpu_remote.ps1 -Off  (+ restart)")


def stop(args: argparse.Namespace) -> None:
    import runpod

    runpod.api_key = _api_key()
    pod_id = args.pod or (STATE.read_text().strip() if STATE.exists() else "")
    if not pod_id:
        sys.exit("No pod id (pass it: stop <id>)")
    runpod.stop_pod(pod_id)
    print(f"pod {pod_id} stopped. GPU billing ended.")
    print("NOTE: without --volume the container (and model) is wiped; next start re-pulls.")


def terminate(args: argparse.Namespace) -> None:
    import runpod

    runpod.api_key = _api_key()
    pod_id = args.pod or (STATE.read_text().strip() if STATE.exists() else "")
    if not pod_id:
        sys.exit("No pod id (pass it: terminate <id>)")
    runpod.terminate_pod(pod_id)
    if STATE.exists():
        STATE.unlink()
    print(f"pod {pod_id} terminated.")


def status(args: argparse.Namespace) -> None:
    import runpod

    runpod.api_key = _api_key()
    pod_id = args.pod or (STATE.read_text().strip() if STATE.exists() else "")
    if not pod_id:
        print("no tracked pod")
        return
    info = runpod.get_pod(pod_id)
    print(f"pod {pod_id}: {info.get('desiredStatus') or info.get('status')}")


def main() -> None:
    p = argparse.ArgumentParser(description="On-demand RunPod GPU brain for CallForge")
    sub = p.add_subparsers(dest="action", required=True)

    s = sub.add_parser("start", help="deploy + pull + wire CallForge")
    s.add_argument("--model", default=DEFAULT_MODEL)
    s.add_argument("--gpu", default=DEFAULT_GPU, help='e.g. "NVIDIA RTX A6000"')
    s.add_argument("--disk", type=int, default=60, help="container disk GB (>= model size + 15)")
    s.add_argument("--volume", default="", help="Network Volume id (model persists across stop)")
    s.add_argument("--cloud", default="COMMUNITY", help="COMMUNITY (cheap) | SECURE | ''")
    s.set_defaults(func=start)

    for name, fn in (("stop", stop), ("terminate", terminate), ("status", status)):
        sp = sub.add_parser(name)
        sp.add_argument("pod", nargs="?", default="")
        sp.set_defaults(func=fn)

    args = p.parse_args()
    try:
        import runpod  # noqa: F401
    except ImportError:
        sys.exit("pip install runpod  (in .venv) then retry.")
    args.func(args)


if __name__ == "__main__":
    main()
