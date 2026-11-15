"""Adversarial robustness (FGSM / PGD via ART) - thin wrapper around the app's own evaluator.

Proposal row: YOLO evaluated against FGSM and PGD with the Adversarial
Robustness Toolbox (no numeric target).

This script owns no attack code: it calls
app.security.adversarial_eval.run_detector_robustness_eval (security module:
real Ultralytics detector wrapped by ART PyTorchYolo, COCO val2017 person
images with official annotations, person AP@0.5 clean / FGSM / PGD, JPEG and
median-smoothing defences) and records what it returns. A run that did not
complete is NOT recorded.

Primary = detection retention under PGD = PGD AP@0.5 / clean AP@0.5.

    pip install adversarial-robustness-toolbox
    python eval/adversarial.py --images 50          # GPU: ~2-5 min; CPU: very slow
"""

from __future__ import annotations

import time

from common import (
    WEIGHTS_DIR,
    base_parser,
    cap_threads,
    load_or_freeze_manifest,
    log,
    manifest_ref,
    ml_lock,
    model_info,
    torch_threads,
    write_result,
)


def build_manifest(n: int) -> dict:
    from app.security.adversarial_eval import ensure_person_subset  # noqa: PLC0415

    samples = ensure_person_subset(n)
    if len(samples) < n:
        raise SystemExit(f"Only {len(samples)} COCO person images available, {n} requested")
    return {
        "dataset": "COCO val2017 images with a non-crowd person (official annotations)",
        "split": f"first {n} qualifying val2017 images",
        "source": "https://cocodataset.org/ (HF mirror LibreYOLO/coco-val2017-mini500)",
        "license": "COCO: CC BY 4.0 annotations, Flickr image terms",
        "items": [{"image_id": int(x["image_id"]), "file": x["file"], "person_boxes": len(x["boxes"])} for x in samples],
    }


def main() -> None:
    p = base_parser(__doc__.split("\n")[0])
    p.add_argument("--model", default=None, help="weight in model_weight/ (default: the shipped person model for the device)")
    p.add_argument("--images", type=int, default=50, help="COCO val2017 images with people")
    p.add_argument("--eps", type=float, default=8 / 255, help="L-inf budget (default 8/255)")
    p.add_argument("--pgd-steps", type=int, default=10)
    args = p.parse_args()
    started = time.time()
    cap_threads(args.threads)
    from app.security.adversarial_eval import run_detector_robustness_eval  # noqa: PLC0415

    if not args.model:
        from pathlib import Path  # noqa: PLC0415

        from app.config import settings  # noqa: PLC0415
        from common import resolve_device  # noqa: PLC0415

        args.model = Path(settings.person_model_for(resolve_device(args.device))[0]).name
    name = f"coco_val2017_person_{args.images}"
    manifest = load_or_freeze_manifest(name, lambda: build_manifest(args.images), refreeze=args.refreeze)
    with ml_lock():
        torch_threads(args.threads)
        r = run_detector_robustness_eval(model_name=args.model, n_images=args.images, eps=args.eps,
                                         pgd_steps=args.pgd_steps, device=args.device)
    log({k: r.get(k) for k in ("status", "reason", "clean", "fgsm", "pgd")})
    if r.get("status") != "completed":
        raise SystemExit(f"Adversarial eval not recorded: {r.get('reason') or r.get('error') or r.get('status')}")
    frozen = [it["image_id"] for it in manifest["items"]]
    if list(map(int, (r.get("dataset") or {}).get("image_ids") or [])) != frozen:
        raise SystemExit(f"The evaluator used other images than manifest {name} - not recorded (use --refreeze on purpose)")
    clean = (r.get("clean") or {}).get("ap50")
    pgd = (r.get("pgd") or {}).get("ap50")
    retention = pgd / clean if clean else None
    ds = r.get("dataset") or {}
    write_result(
        "adversarial",
        value=retention,
        metrics={**{k: v for k, v in r.items() if k not in ("dataset", "created_at")},
                 "detection_retention_pgd": None if retention is None else round(retention, 4),
                 "detection_retention_fgsm": round((r["fgsm"]["ap50"] / clean), 4) if clean else None},
        dataset={**manifest_ref(name, manifest), "person_boxes": ds.get("person_boxes")},
        model=model_info(WEIGHTS_DIR / args.model, imgsz=r.get("imgsz"), eps=r.get("params", {}).get("eps"),
                         pgd_steps=args.pgd_steps),
        device=r.get("device"),
        started=started,
        notes=["Attack and defence code are app.security.adversarial_eval's; this script records its report.",
               "White-box attacks on the deployed weights (gradients from the detector's own loss)."],
    )


if __name__ == "__main__":
    main()
