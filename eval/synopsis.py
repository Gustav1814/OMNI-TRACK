"""Video synopsis compression on long store footage.

Proposal criterion: synopsis compresses >= 30 min of footage by >= 10x while
keeping every activity tube.

Method: the app's own engine (app.ai.synopsis.VideoSynopsis, target 10x) runs
on one long clip. By default that clip is every CAVIAR corridor-camera
sequence concatenated in name order (same fixed camera, ~24 min - all the
corridor footage that exists) and written once to eval/.cache/. Pass --video
for your own >= 30 min recording. Reported: achieved compression ratio (the
primary number), the share of kept tubes that made it into the synopsis, and
processing time.

    OMNITRACK_ML_LOCK=/tmp/omnitrack-ml.lock python eval/synopsis.py [--video long.mp4]
"""

from __future__ import annotations

import time
from pathlib import Path

import caviar
from common import (
    EVAL_DIR,
    base_parser,
    cap_threads,
    load_or_freeze_manifest,
    log,
    manifest_ref,
    ml_lock,
    sha256_file,
    write_result,
)

CACHE = EVAL_DIR / ".cache" / "synopsis"
MANIFEST = "caviar_synopsis_corridor"


def build_manifest() -> dict:
    seqs = caviar.sequences()
    if not seqs:
        raise SystemExit("No CAVIAR sequences in backend/storage/demo_videos/caviar/ - copy them from the dev box")
    return {
        "dataset": "CAVIAR corridor camera, every sequence concatenated in name order",
        "split": "all",
        "source": "https://groups.inf.ed.ac.uk/vision/CAVIAR/CAVIARDATA1/",
        "license": "CAVIAR: free for research use with acknowledgement (EC IST-2001-37540)",
        "items": [{"sequence": s, "video": caviar.video_path(s, "cam1_corridor").name,
                   "sha256": sha256_file(caviar.video_path(s, "cam1_corridor"))} for s in seqs],
    }


def build_corridor_clip(dest: Path, manifest: dict) -> Path:
    import cv2  # noqa: PLC0415

    for it in manifest["items"]:  # the frozen sequences, byte-for-byte
        path = caviar.video_path(it["sequence"], "cam1_corridor")
        if not path.is_file() or sha256_file(path) != it["sha256"]:
            raise SystemExit(f"{path} is missing or differs from manifest {MANIFEST} (use --refreeze on purpose)")
    if dest.is_file():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part.avi")
    writer = None
    for seq in (it["sequence"] for it in manifest["items"]):
        for _, frame in caviar.iter_frames(caviar.video_path(seq, "cam1_corridor")):
            if writer is None:
                h, w = frame.shape[:2]
                writer = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"MJPG"), 25.0, (w, h))
            writer.write(frame)
    writer.release()
    tmp.replace(dest)
    return dest


def main() -> None:
    p = base_parser(__doc__.split("\n")[0])
    p.add_argument("--video", type=Path, default=None)
    p.add_argument("--target", type=float, default=10.0)
    args = p.parse_args()
    started = time.time()
    cap_threads(args.threads)
    manifest = None
    if args.video:
        video = args.video
        name = f"own footage ({video.name})"
    else:
        manifest = load_or_freeze_manifest(MANIFEST, build_manifest, refreeze=args.refreeze)
        video = build_corridor_clip(CACHE / f"caviar_cam1_corridor_{manifest['items_sha256'][:12]}.avi", manifest)
        name = f"CAVIAR corridor camera, all {manifest['n_items']} sequences concatenated"

    from app.ai.synopsis import VideoSynopsis  # noqa: PLC0415

    with ml_lock():
        out = CACHE / f"{video.stem}_synopsis.webm"
        out.parent.mkdir(parents=True, exist_ok=True)
        m = VideoSynopsis(compression_target=args.target, spool_dir=str(CACHE / "spool")).process_video(str(video), str(out))
    m = {k: v for k, v in m.items() if k not in ("output_path", "tubes_path")}
    log(str(m))
    minutes = (m.get("original_duration") or 0) / 60
    notes = ["Primary = original frames / synopsis frames as achieved (not the target)."]
    if minutes < 30:
        notes.append(f"Input is {minutes:.1f} min; the criterion asks for >= 30 min - rerun with --video on a longer "
                     "recording from the store cameras.")
    write_result(
        "synopsis",
        value=m.get("compression_ratio"),
        metrics=m,
        dataset={**(manifest_ref(MANIFEST, manifest) if manifest else {}),
                 "name": name, "split": f"{minutes:.1f} min", "clip": str(video.name),
                 "sha256": sha256_file(video), "n_frames": m.get("original_frames")},
        model={"name": "app.ai.synopsis.VideoSynopsis", "compression_target": args.target},
        device="cpu",
        started=started,
        notes=notes,
    )


if __name__ == "__main__":
    main()
