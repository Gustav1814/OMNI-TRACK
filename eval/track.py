"""Multi-object tracking (HOTA / MOTA / IDF1 / ID switches) on CAVIAR with TrackEval.

Proposal row: person tracking quality (no numeric target; IDF1 reported).

Method
  * Frozen subset of CAVIAR sequences (both views each) chosen to cover
    crossing paths, groups and people entering/leaving the shop.
  * Frames are processed every --step frames (25 fps / 2 = 12.5 fps, close to
    the pipeline's processing rate) by the detector + the app's own tracker
    (app.ai.tracker.PersonTracker, ByteTrack + tentative/confirmed lifecycle).
    Only CONFIRMED tracks are scored - that is what the app shows and counts.
  * GT boxes shorter than --min-height px are removed together with any track
    box overlapping them (IoU >= 0.5), and track boxes shorter than
    --min-height are dropped (same ignore policy as eval/detect.py).
  * TrackEval (JonathonLuiten/TrackEval) HOTA, CLEAR and Identity metrics on IoU
    similarity, combined over all sequences.
  * Presets: "phase1" (shipped CPU config: yolo11s @ 640, detector conf 0.1,
    ByteTrack 0.5/0.1/0.6/0.8, 3 s buffer, 3 hits) - primary; "baseline" (the
    pre-remediation yolov8n @ 640 with conf 0.5).

    pip install -r eval/requirements.txt
    OMNITRACK_ML_LOCK=/tmp/omnitrack-ml.lock python eval/track.py
"""

from __future__ import annotations

import time

import numpy as np

import caviar
from common import (
    WEIGHTS_DIR,
    base_parser,
    box_iou,
    cap_threads,
    load_or_freeze_manifest,
    log,
    manifest_ref,
    ml_lock,
    model_info,
    resolve_device,
    torch_threads,
    write_result,
)

MANIFEST = "caviar_track"
SEQS = ["EnterExitCrossingPaths1", "OneStopMoveEnter1", "ThreePastShop1", "TwoEnterShop1"]
PRESETS = {
    "phase1": {"model": "yolo11s.pt", "imgsz": 640, "conf": 0.1},
    "baseline": {"model": "yolov8n.pt", "imgsz": 640, "conf": 0.5},
}
CAVIAR_FPS = 25.0


def trackeval_modules():
    """Import TrackEval with the numpy>=1.24 aliases it still references."""
    for alias, typ in (("float", float), ("int", int), ("bool", bool)):
        if alias not in np.__dict__:
            setattr(np, alias, typ)
    try:
        from trackeval.metrics import CLEAR, HOTA, Identity  # noqa: PLC0415
    except ImportError as exc:
        raise SystemExit("TrackEval missing: pip install -r eval/requirements.txt") from exc
    return HOTA({"PRINT_CONFIG": False}), CLEAR({"PRINT_CONFIG": False}), Identity({"PRINT_CONFIG": False})


def build_manifest(step: int, min_h: float) -> dict:
    items = []
    for seq in SEQS:
        for view in caviar.VIEWS:
            gt = caviar.load_gt(seq, view)
            items.append({"seq": seq, "view": view, "frames": max(gt) + 1, "step": step})
    return {
        "dataset": "CAVIAR shopping centre, tracking subset (4 sequences x 2 views)",
        "source": "https://homepages.inf.ed.ac.uk/rbf/CAVIARDATA1/",
        "license": "CAVIAR terms: free for research use with acknowledgement",
        "split": f"{', '.join(SEQS)}; every {step}th frame; GT h<{min_h:g}px = ignore",
        "params": {"step": step, "min_height_px": min_h},
        "items": items,
    }


def sequence_data(gt_frames, trk_frames, min_h: float) -> dict:
    """Build TrackEval's per-sequence dict from aligned lists of (ids, xyxy) per timestep."""
    gt_ids, tr_ids, sims = [], [], []
    for (g_id, g_box), (t_id, t_box) in zip(gt_frames, trk_frames):
        g_h = g_box[:, 3] - g_box[:, 1]
        small = g_h < min_h
        if small.any() and len(t_box):
            near_small = (box_iou(t_box, g_box[small]) >= 0.5).any(axis=1)
        else:
            near_small = np.zeros(len(t_box), bool)
        keep_t = ~near_small & ((t_box[:, 3] - t_box[:, 1]) >= min_h) if len(t_box) else np.zeros(0, bool)
        g_id, g_box = g_id[~small], g_box[~small]
        t_id, t_box = t_id[keep_t], t_box[keep_t]
        gt_ids.append(g_id)
        tr_ids.append(t_id)
        sims.append(box_iou(g_box, t_box) if len(g_box) and len(t_box) else np.zeros((len(g_box), len(t_box))))
    # TrackEval wants contiguous 0-based ids
    g_map = {v: i for i, v in enumerate(sorted({int(x) for a in gt_ids for x in a}))}
    t_map = {v: i for i, v in enumerate(sorted({int(x) for a in tr_ids for x in a}))}
    return {
        "num_timesteps": len(gt_ids),
        "num_gt_ids": len(g_map),
        "num_tracker_ids": len(t_map),
        "num_gt_dets": int(sum(len(a) for a in gt_ids)),
        "num_tracker_dets": int(sum(len(a) for a in tr_ids)),
        "gt_ids": [np.array([g_map[int(x)] for x in a], dtype=int) for a in gt_ids],
        "tracker_ids": [np.array([t_map[int(x)] for x in a], dtype=int) for a in tr_ids],
        "similarity_scores": sims,
    }


def run_preset(name: str, cfg: dict, manifest: dict, device: str, min_h: float) -> dict:
    from ultralytics import YOLO  # noqa: PLC0415

    from app.ai.tracker import PersonTracker  # noqa: PLC0415

    hota, clear, ident = trackeval_modules()
    model = YOLO(str(WEIGHTS_DIR / cfg["model"]))
    per_seq, t_inf, n_frames = {}, 0.0, 0
    for it in manifest["items"]:
        seq, view, step = it["seq"], it["view"], it["step"]
        gt = caviar.load_gt(seq, view)
        fps = CAVIAR_FPS / step
        tracker = PersonTracker(fps=fps)
        wanted = set(range(0, it["frames"], step))
        gt_frames, trk_frames = [], []
        for idx, frame in caviar.iter_frames(caviar.video_path(seq, view), wanted):
            t = time.perf_counter()
            r = model.predict(frame, imgsz=cfg["imgsz"], conf=cfg["conf"], iou=0.6, classes=[0],
                              device=device, verbose=False)[0]
            out = tracker.update(r.boxes.xyxy.cpu().numpy(), r.boxes.conf.cpu().numpy(), idx / CAVIAR_FPS)
            t_inf += time.perf_counter() - t
            n_frames += 1
            conf_tracks = out.confirmed
            trk_frames.append((np.array([tr.track_id for tr in conf_tracks], dtype=int),
                               np.array([tr.xyxy for tr in conf_tracks], dtype=float).reshape(-1, 4)))
            rows = gt.get(idx, np.zeros((0, 5)))
            gt_frames.append((rows[:, 0].astype(int), rows[:, 1:5]))
        data = sequence_data(gt_frames, trk_frames, min_h)
        per_seq[f"{seq}/{view}"] = {
            "HOTA": hota.eval_sequence(data),
            "CLEAR": clear.eval_sequence(data),
            "Identity": ident.eval_sequence(data),
        }
        log(f"  [{name}] {seq}/{view}: IDF1={per_seq[f'{seq}/{view}']['Identity']['IDF1']:.3f} "
            f"MOTA={per_seq[f'{seq}/{view}']['CLEAR']['MOTA']:.3f}")
    combined = {
        "HOTA": hota.combine_sequences({k: v["HOTA"] for k, v in per_seq.items()}),
        "CLEAR": clear.combine_sequences({k: v["CLEAR"] for k, v in per_seq.items()}),
        "Identity": ident.combine_sequences({k: v["Identity"] for k, v in per_seq.items()}),
    }
    summary = summarise(combined)
    summary.update({"preset": name, **cfg, "n_frames": n_frames, "ms_per_frame": round(1000 * t_inf / max(n_frames, 1), 1)})
    summary["per_sequence"] = {k: summarise(v) for k, v in per_seq.items()}
    return summary


def summarise(r: dict) -> dict:
    return {
        "HOTA": round(float(np.mean(r["HOTA"]["HOTA"])), 4),
        "DetA": round(float(np.mean(r["HOTA"]["DetA"])), 4),
        "AssA": round(float(np.mean(r["HOTA"]["AssA"])), 4),
        "MOTA": round(float(r["CLEAR"]["MOTA"]), 4),
        "MOTP": round(float(r["CLEAR"]["MOTP"]), 4),
        "IDF1": round(float(r["Identity"]["IDF1"]), 4),
        "IDSW": int(r["CLEAR"]["IDSW"]),
        "Frag": int(r["CLEAR"]["Frag"]),
        "MT": int(r["CLEAR"]["MT"]),
        "ML": int(r["CLEAR"]["ML"]),
        "FP": int(r["CLEAR"]["CLR_FP"]),
        "FN": int(r["CLEAR"]["CLR_FN"]),
    }


def main() -> None:
    p = base_parser(__doc__.split("\n")[0])
    p.add_argument("--preset", action="append", choices=sorted(PRESETS), default=None,
                   help="repeatable; first is primary (default phase1 then baseline)")
    p.add_argument("--step", type=int, default=2)
    p.add_argument("--min-height", type=float, default=24.0)
    args = p.parse_args()
    presets = args.preset or ["phase1", "baseline"]
    started = time.time()
    cap_threads(args.threads)
    trackeval_modules()  # fail fast before any heavy work
    manifest = load_or_freeze_manifest(MANIFEST, lambda: build_manifest(args.step, args.min_height),
                                       refreeze=args.refreeze)
    min_h = manifest["params"]["min_height_px"]
    results = []
    with ml_lock():
        torch_threads(args.threads)
        device = resolve_device(args.device)
        for name in presets:
            log(f"preset {name}: {PRESETS[name]} on {device}")
            results.append(run_preset(name, PRESETS[name], manifest, device, min_h))
            log(f"  {name}: " + ", ".join(f"{k}={results[-1][k]}" for k in ("HOTA", "MOTA", "IDF1", "IDSW")))
    primary = results[0]
    write_result(
        "tracking",
        value=primary["IDF1"],
        metrics={"primary": {k: v for k, v in primary.items() if k != "per_sequence"}, "presets": results},
        dataset=manifest_ref(MANIFEST, manifest),
        model=model_info(WEIGHTS_DIR / primary["model"], imgsz=primary["imgsz"], conf=primary["conf"],
                         tracker="app.ai.tracker.PersonTracker (ByteTrack)", scored="confirmed tracks only"),
        device=device,
        started=started,
        notes=[
            "TrackEval HOTA/CLEAR/Identity on IoU similarity; sequences combined TrackEval-style.",
            f"Frames processed every {manifest['params']['step']} frames ({CAVIAR_FPS / manifest['params']['step']:g} fps).",
            "Only confirmed tracks are scored (min 3 hits), so the first 2 processed frames of every track count as FN.",
        ],
    )


if __name__ == "__main__":
    main()
