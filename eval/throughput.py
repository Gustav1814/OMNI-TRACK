"""Live pipeline throughput per camera (run LAST, on the demo machine).

Proposal criterion: >= 20 processed FPS per camera with 4 cameras at 720p.

Method: the backend must already be running with the cameras streaming (e.g.
4 recorded 720p clips added as file cameras in Settings). This script only
reads the unauthenticated, read-only GET /api/pipeline/health endpoint every
--interval seconds for --duration seconds and reports, per camera, the median
processed_fps over the window; the primary number is the slowest camera.
Samples where a camera is not "ok" are counted and reported, not dropped.

    python eval/throughput.py --url http://localhost:8000 --duration 120
"""

from __future__ import annotations

import json
import time
import urllib.request

import numpy as np

from common import base_parser, log, write_result


def fetch(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.load(r)


def main() -> None:
    p = base_parser(__doc__.split("\n")[0])
    p.add_argument("--url", default="http://localhost:8000")
    p.add_argument("--duration", type=float, default=120.0)
    p.add_argument("--interval", type=float, default=2.0)
    p.add_argument("--warmup", type=float, default=20.0, help="seconds ignored at the start")
    p.add_argument("--hardware-note", default="", help="e.g. 'RTX 3060 Ti demo PC' when run remotely")
    args = p.parse_args()
    started = time.time()
    endpoint = args.url.rstrip("/") + "/api/pipeline/health"
    try:
        first = fetch(endpoint)
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"Backend not reachable at {endpoint}: {exc}") from exc
    if not first.get("cameras"):
        raise SystemExit("Pipeline reports no cameras - start the camera streams first.")
    time.sleep(args.warmup)
    samples: dict[int, list[float]] = {}
    not_ok: dict[int, int] = {}
    meta: dict[int, dict] = {}
    t_end = time.time() + args.duration
    while time.time() < t_end:
        for cam in fetch(endpoint).get("cameras", []):
            cid = cam["camera_id"]
            samples.setdefault(cid, []).append(float(cam.get("processed_fps") or 0.0))
            not_ok[cid] = not_ok.get(cid, 0) + (cam.get("status") != "ok")
            meta[cid] = {"zone": cam.get("zone"), "latency_ms": cam.get("latency_ms"),
                         "stream_fps": (cam.get("stream") or {}).get("fps")}
        time.sleep(args.interval)
    per_cam = {
        str(cid): {"median_fps": round(float(np.median(v)), 2), "min_fps": round(float(np.min(v)), 2),
                   "samples": len(v), "samples_not_ok": not_ok[cid], **meta[cid]}
        for cid, v in samples.items()
    }
    slowest = min(c["median_fps"] for c in per_cam.values())
    log(f"per camera: {per_cam}")
    write_result(
        "throughput",
        value=slowest,
        metrics={"min_camera_median_fps": slowest, "n_cameras": len(per_cam), "per_camera": per_cam,
                 "window_s": args.duration, "hardware_note": args.hardware_note or None},
        dataset={"name": f"{len(per_cam)} live camera streams", "split": f"{args.duration:g}s window",
                 "source": endpoint, "n_items": len(per_cam)},
        model={"name": "running OmniTrack pipeline"},
        device=None,
        started=started,
        notes=["hardware below is the machine that ran this script; it must be the backend host."],
    )


if __name__ == "__main__":
    main()
