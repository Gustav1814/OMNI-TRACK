"""
Download + sha256-verify every model weight OmniTrack needs (the small .pt files are also kept in git; the rest are not).

Examples:
  .venv/bin/python scripts/download_default_weights.py            # everything the app needs
  .venv/bin/python scripts/download_default_weights.py --verify   # check what is on disk
  .venv\\Scripts\\python scripts/download_default_weights.py yolo26m.pt sam2_b.pt
  .venv\\Scripts\\python scripts/download_default_weights.py https://host/model.pt
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path

# Every weight the app can load is listed here with a pinned sha256, so a fresh
# clone is reproducible and every file on disk can be checked.
#   PINNED - public upstream file (Ultralytics release, third-party GitHub/HF release)
#   CUSTOM - team-trained, no public upstream: served from this repo's GitHub release
_UL = "https://github.com/ultralytics/assets/releases/download"
PINNED = {
    # --- Ultralytics official COCO detectors (person = class 0) ---
    "yolov8n.pt": (f"{_UL}/v8.3.0/yolov8n.pt", "f59b3d833e2ff32e194b5bb8e08d211dc7c5bdf144b90d2c8412c47ccfc83b36"),
    "yolo11n.pt": (f"{_UL}/v8.3.0/yolo11n.pt", "0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1"),
    "yolo11s.pt": (f"{_UL}/v8.3.0/yolo11s.pt", "85a76fe86dd8afe384648546b56a7a78580c7cb7b404fc595f97969322d502d5"),
    "yolo11m.pt": (f"{_UL}/v8.3.0/yolo11m.pt", "d5ffc1a674953a08e11a8d21e022781b1b23a19b730afc309290bd9fb5305b95"),
    "yolo26n.pt": (f"{_UL}/v8.4.0/yolo26n.pt", "9b09cc8bf347f0fc8a5f7657480587f25db09b34bf33b0652110fb03a8ad4fef"),
    # Ultralytics yolo11n-pose (COCO keypoints), stored under its legacy local name
    "general_pe_n_11_pt.pt": (f"{_UL}/v8.3.0/yolo11n-pose.pt", "869e83fcdffdc7371fa4e34cd8e51c838cc729571d1635e5141e3075e9319dc0"),
    # --- Third-party ---
    # YOLO11n face detector trained on WIDER FACE (akanametov/yolo-face, release 1.0.0)
    "face11n.pt": (
        "https://github.com/akanametov/yolo-face/releases/download/1.0.0/yolov11n-face.pt",
        "9420a9d4933940d2202f511d45df1750e3c1546dd9efc7a3985caae3bbbf7ed2",
    ),
    # HSEmotion EfficientNet-B0, AffectNet 8 classes (facial emotion; see app/ai/emotion.py)
    "enet_b0_8_best_afew.onnx": (
        "https://github.com/av-savchenko/face-emotion-recognition/raw/main/models/affectnet_emotions/onnx/enet_b0_8_best_afew.onnx",
        "7aa2ea31c1311f4f8aa9d3fdb085d418dd4e7a48c4b9ed41df8c044f91d0213f",
    ),
    # OSNet-AIN x1.0 trained on MSMT17 (cross-domain person Re-ID; see app/ai/reid.py)
    "osnet_ain_x1_0_msmt17.pth": (
        "https://huggingface.co/kaiyangzhou/osnet/resolve/main/"
        "osnet_ain_x1_0_msmt17_256x128_amsgrad_ep50_lr0.0015_coslr_b64_fb10_softmax_labsmth_flip_jitter.pth",
        "8a07e8da38946f7cee37f4561617bf8b6d2fe8f3a4027852893ea092e46d919f",
    ),
    # FairFace ResNet-34 (joojs/fairface, CC BY 4.0): 18 logits = race 0:7, gender 7:9, age 9:18.
    # Official Google Drive file from the project README. Only gender + age are exported (see DERIVED).
    "res34_fair_align_multi_7_20190809.pt": (
        "https://drive.usercontent.google.com/download?id=11y0Wi3YQf21a_VcspUV4FwqzhMcfaVAB&export=download&confirm=t",
        "37f2e74c0e7da196f8edd7dae0c88cffdbef7923beebebcc27381f0ad0fdb2c1",
    ),
}


def _export_fairface(src: Path, target: Path) -> None:
    """FairFace state dict -> ONNX with only the gender (2) and age-band (9) heads; the race head is dropped."""
    import torch
    import torchvision

    torch.set_num_threads(int(os.getenv("OMP_NUM_THREADS", "4")))
    net = torchvision.models.resnet34(num_classes=18)
    net.load_state_dict(torch.load(src, map_location="cpu", weights_only=True))
    net.eval()

    class GenderAge(torch.nn.Module):
        def __init__(self, body: torch.nn.Module):
            super().__init__()
            self.body = body

        def forward(self, x):
            logits = self.body(x)
            return logits[:, 7:9], logits[:, 9:18]

    part = target.with_name(target.name + ".part")
    try:
        torch.onnx.export(
            GenderAge(net), (torch.zeros(1, 3, 224, 224),), str(part), dynamo=False, opset_version=17,
            input_names=["input"], output_names=["gender", "age"],
            dynamic_axes={"input": {0: "n"}, "gender": {0: "n"}, "age": {0: "n"}},
        )
        part.replace(target)
    finally:
        part.unlink(missing_ok=True)


# Files built locally from a pinned download (exporter output bytes vary by torch version, so the
# pinned source is what is verified): name -> (pinned source name, builder)
DERIVED = {
    "fairface_res34.onnx": ("res34_fair_align_multi_7_20190809.pt", _export_fairface),
}

# Team-trained weights (no public upstream; provenance from the checkpoint's train_args):
#   fire-smoke.pt                 - YOLOv8n fine-tuned 150 epochs on the Roboflow "fire-smoke-yolov8" v1 set
#   general_product_detection.pt  - YOLOv8n fine-tuned 90 epochs at 512 px on the Roboflow "ALOT_OF_PRODUCTS" v1 set
# They are downloaded from OMNITRACK_WEIGHTS_URL/<name> (default: this repo's
# "weights-v1" GitHub release). Publish them once with:
#   gh release create weights-v1 backend/model_weight/fire-smoke.pt backend/model_weight/general_product_detection.pt
CUSTOM = {
    "fire-smoke.pt": "73c2a9831506b79834b3d3ac20cc40422bb0d781d6e0c29f5f57612b35fcd5b3",
    "general_product_detection.pt": "26a39610a0611725339c58534ccb69b62757baabbd79fe23c203e1222f5c7710",
}
WEIGHTS_RELEASE_URL = os.getenv(
    "OMNITRACK_WEIGHTS_URL", "https://github.com/Gustav1814/OMNI-TRACK/releases/download/weights-v1"
).rstrip("/")

# What the app needs at runtime (config.py defaults + specialist streams).
_REQUIRED = [
    "yolov8n.pt", "yolo11n.pt", "yolo11s.pt", "yolo26n.pt", "general_pe_n_11_pt.pt",
    "face11n.pt", "enet_b0_8_best_afew.onnx", "osnet_ain_x1_0_msmt17.pth",
    "res34_fair_align_multi_7_20190809.pt", "fairface_res34.onnx", *CUSTOM,
]


def _default_seeds(profile: str) -> list[str]:
    if profile == "workstation":
        return [*_REQUIRED, "yolo11m.pt"]
    return list(_REQUIRED)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _expected(name: str) -> str | None:
    if name in PINNED:
        return PINNED[name][1]
    return CUSTOM.get(name)


def _fetch(url: str, target: Path, digest: str | None) -> None:
    """Download to <target>.part, verify, then atomically move into place."""
    import urllib.request

    part = target.with_name(target.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "omnitrack-weights/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp, part.open("wb") as fh:
            while chunk := resp.read(1 << 20):
                fh.write(chunk)
        if digest and _sha256(part) != digest:
            raise RuntimeError(f"checksum mismatch (expected {digest[:12]}...)")
        part.replace(target)
    finally:
        part.unlink(missing_ok=True)


def main() -> None:
    backend = Path(__file__).resolve().parent.parent
    out = backend / "model_weight"
    out.mkdir(parents=True, exist_ok=True)
    parser = argparse.ArgumentParser(description="Download and sha256-verify OmniTrack model weights.")
    parser.add_argument("sources", nargs="*", help="Model asset names or URLs (default: everything the app needs)")
    parser.add_argument("--profile", default=os.getenv("RUNTIME_PROFILE", "laptop"), choices=["laptop", "workstation"])
    parser.add_argument("--verify", action="store_true", help="Only verify checksums of files already present")
    args = parser.parse_args()

    sources = args.sources or _default_seeds(args.profile)
    failures: list[str] = []

    for src in sources:
        name = Path(src).name
        target = out / name
        digest = _expected(name)
        if target.is_file():
            if digest and _sha256(target) != digest:
                failures.append(f"{name}: present but sha256 differs from the pinned value (delete it to re-download)")
            else:
                print(f"OK       {name}" + ("" if digest else " (not pinned)"))
            continue
        if args.verify:
            failures.append(f"{name}: missing")
            continue
        try:
            if name in DERIVED:
                src_name, build = DERIVED[name]
                src_path = out / src_name
                if not src_path.is_file():
                    _fetch(PINNED[src_name][0], src_path, PINNED[src_name][1])
                elif _sha256(src_path) != PINNED[src_name][1]:
                    raise RuntimeError(f"{src_name} sha256 differs from the pinned value")
                build(src_path, target)
                print(f"BUILT    {name} from {src_name}")
                continue
            if name in PINNED:
                _fetch(PINNED[name][0], target, digest)
            elif name in CUSTOM:
                _fetch(f"{WEIGHTS_RELEASE_URL}/{name}", target, digest)
            elif src.startswith(("http://", "https://")):
                _fetch(src, target, None)
            else:
                os.chdir(out)
                from ultralytics.utils.downloads import attempt_download_asset

                attempt_download_asset(src)
                if not target.is_file():
                    raise RuntimeError("Ultralytics has no asset with this name")
            print(f"FETCHED  {name}" + (" (sha256 verified)" if digest else ""))
        except Exception as exc:  # noqa: BLE001 - report every failure, then exit non-zero
            if name in CUSTOM:
                failures.append(
                    f"{name}: {exc}. This is a team-trained model with no public upstream. It must be published "
                    f"as a release asset at {WEIGHTS_RELEASE_URL}/{name} (see CUSTOM in this script), or set "
                    "OMNITRACK_WEIGHTS_URL to wherever it is hosted, or copy it into backend/model_weight/ by hand."
                )
            else:
                failures.append(f"{name}: {exc}")

    if failures:
        print("\nFAILED:")
        for line in failures:
            print(f"  - {line}")
        raise SystemExit(1)
    print(f"\nAll {len(sources)} weight(s) present and verified in {out}")


if __name__ == "__main__":
    main()
