"""
Generic model fetch utility.

Examples:
  .venv\\Scripts\\python scripts/download_default_weights.py
  .venv\\Scripts\\python scripts/download_default_weights.py yolo26m.pt sam2_b.pt
  .venv\\Scripts\\python scripts/download_default_weights.py https://host/model.pt
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def _default_seeds(profile: str) -> list[str]:
    if profile == "workstation":
        return ["yolo11n.pt", "yolo26n.pt", "yolo26m.pt"]
    return ["yolo11n.pt", "yolo26n.pt"]


def main() -> None:
    backend = Path(__file__).resolve().parent.parent
    out = backend / "model_weight"
    out.mkdir(parents=True, exist_ok=True)
    parser = argparse.ArgumentParser()
    parser.add_argument("sources", nargs="*", help="Model asset names or URLs")
    parser.add_argument("--profile", default=os.getenv("RUNTIME_PROFILE", "laptop"), choices=["laptop", "workstation"])
    args = parser.parse_args()

    sources = args.sources or _default_seeds(args.profile)
    os.chdir(out)
    from ultralytics.utils.downloads import attempt_download_asset
    import urllib.request

    for src in sources:
        name = Path(src).name
        target = out / name
        if target.is_file():
            print(f"Already present: {target}")
            continue
        if src.startswith(("http://", "https://")):
            urllib.request.urlretrieve(src, str(target))
            print(f"Downloaded URL: {target}")
        else:
            attempt_download_asset(src)
            print(f"Fetched asset: {src}")


if __name__ == "__main__":
    main()
