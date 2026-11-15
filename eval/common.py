"""Shared helpers for the OmniTrack evaluation scripts (eval/*.py).

Every script:
  * takes the shared ML lock (one heavy job at a time on a shared machine),
  * caps CPU threads and streams data so peak RSS stays small,
  * reads/writes a *frozen* dataset manifest under eval/manifests/ (lists items,
    never images) so the exact split can be reproduced,
  * writes eval/results/<id>.json with the measured numbers plus hardware,
    model (+ sha256), imgsz, git commit, dataset manifest hash and timing.

A missed target is written with its real number; nothing here invents values.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Iterator

try:  # POSIX; the demo PC is Windows, where msvcrt locking and psutil are used instead
    import fcntl
    import resource
except ImportError:
    fcntl = resource = None

REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO / "backend"
EVAL_DIR = REPO / "eval"
RESULTS_DIR = EVAL_DIR / "results"
MANIFESTS_DIR = EVAL_DIR / "manifests"
DATASETS_DIR = BACKEND / "storage" / "datasets"
CAVIAR_DIR = BACKEND / "storage" / "demo_videos" / "caviar"
WEIGHTS_DIR = BACKEND / "model_weight"
CRITERIA_FILE = EVAL_DIR / "criteria.json"

RESULT_SCHEMA = 1
DEFAULT_LOCK = os.environ.get("OMNITRACK_ML_LOCK", str(Path(tempfile.gettempdir()) / "omnitrack-ml.lock"))

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


# --------------------------------------------------------------------------- runtime
def cap_threads(n: int) -> None:
    """Cap BLAS/OpenMP/torch/OpenCV threads. Call before heavy imports."""
    n = max(1, int(n))
    for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[var] = str(n)
    try:
        import cv2  # noqa: PLC0415

        cv2.setNumThreads(n)
    except Exception:
        pass
    if "torch" in sys.modules:
        sys.modules["torch"].set_num_threads(n)


def torch_threads(n: int) -> None:
    import torch  # noqa: PLC0415

    torch.set_num_threads(max(1, int(n)))


def resolve_device(device: str = "auto") -> str:
    """'auto' -> 'cuda:0' when a CUDA device is visible, else 'cpu'. Never hard-coded."""
    if device and device != "auto":
        return device
    try:
        import torch  # noqa: PLC0415

        return "cuda:0" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


@contextlib.contextmanager
def ml_lock(path: str | None = None) -> Iterator[None]:
    """Exclusive file lock shared by every model-loading job on this machine.

    Set OMNITRACK_ML_LOCK to the lock path; OMNITRACK_ML_LOCK_HELD=1 skips it when
    a parent process already holds the lock (avoids self-deadlock under `flock`).
    """
    if os.environ.get("OMNITRACK_ML_LOCK_HELD") == "1":
        yield
        return
    lock_path = Path(path or DEFAULT_LOCK)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as fh:
        if fcntl is None:  # Windows: lock byte 0, poll until free
            import msvcrt  # noqa: PLC0415

            fh.seek(0)
            waited = False
            while True:
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if not waited:
                        log(f"waiting for ML lock {lock_path} ...")
                        waited = True
                    time.sleep(1.0)
            try:
                yield
            finally:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            return
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log(f"waiting for ML lock {lock_path} ...")
            fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def peak_rss_mb() -> float | None:
    if resource is not None:
        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)
    try:  # Windows: peak working set
        import psutil  # noqa: PLC0415

        mi = psutil.Process().memory_info()
        return round(getattr(mi, "peak_wset", mi.rss) / 2**20, 1)
    except Exception:
        return None


# --------------------------------------------------------------------------- provenance
def sha256_file(path: Path | str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def sha256_json(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def git_info() -> dict[str, Any]:
    def _git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, timeout=10).stdout.strip()

    try:
        return {
            "commit": _git("rev-parse", "HEAD") or None,
            "branch": _git("rev-parse", "--abbrev-ref", "HEAD") or None,
            "dirty": bool(_git("status", "--porcelain", "--untracked-files=no")),
        }
    except Exception:
        return {"commit": None, "branch": None, "dirty": None}


def _cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or platform.machine()


def hardware_info(device: str | None = None) -> dict[str, Any]:
    info: dict[str, Any] = {
        "cpu": _cpu_model(),
        "logical_cores": os.cpu_count(),
        "threads_used": int(os.environ.get("OMP_NUM_THREADS", "0") or 0) or None,
        "os": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
        "device": device,
        "gpu": None,
    }
    try:
        import psutil  # noqa: PLC0415

        info["ram_gb"] = round(psutil.virtual_memory().total / 1e9, 1)
    except Exception:
        pass
    if "torch" in sys.modules:
        torch = sys.modules["torch"]
        info["torch"] = torch.__version__
        try:
            if torch.cuda.is_available():
                info["gpu"] = torch.cuda.get_device_name(0)
        except Exception:
            pass
    return info


def model_info(path: Path | str | None, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {k: v for k, v in extra.items() if v is not None}
    if path:
        p = Path(path)
        out["name"] = p.name
        if p.is_file():
            out["sha256"] = sha256_file(p)
    return out


# --------------------------------------------------------------------------- manifests
def manifest_path(name: str) -> Path:
    return MANIFESTS_DIR / f"{name}.json"


def load_or_freeze_manifest(
    name: str, builder: Callable[[], dict[str, Any]], refreeze: bool = False
) -> dict[str, Any]:
    """Return the frozen manifest `name`; build and write it only the first time.

    builder() returns {"dataset": str, "source": str, "split": str, "items": [...], ...}.
    The manifest stores a sha256 over `items` so a result can prove which split it used.
    """
    path = manifest_path(name)
    if path.is_file() and not refreeze:
        manifest = json.loads(path.read_text())
        if manifest.get("items_sha256") != sha256_json(manifest.get("items", [])):
            raise SystemExit(f"Manifest {path} was edited by hand (items_sha256 mismatch). Use --refreeze.")
        return manifest
    manifest = builder()
    manifest.setdefault("frozen_at", _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"))
    manifest["n_items"] = len(manifest.get("items", []))
    manifest["items_sha256"] = sha256_json(manifest.get("items", []))
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=1) + "\n")
    log(f"froze manifest {path.relative_to(REPO) if path.is_relative_to(REPO) else path} ({manifest['n_items']} items)")
    return manifest


def manifest_ref(name: str, manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": manifest.get("dataset"),
        "split": manifest.get("split"),
        "source": manifest.get("source"),
        "license": manifest.get("license"),
        "manifest": str(manifest_path(name).relative_to(REPO)),
        "manifest_sha256": manifest.get("items_sha256"),
        "n_items": manifest.get("n_items"),
    }


# --------------------------------------------------------------------------- criteria / results
def criterion(result_id: str) -> dict[str, Any]:
    data = json.loads(CRITERIA_FILE.read_text())
    for c in data["criteria"]:
        if c["id"] == result_id:
            return c
    raise KeyError(result_id)


def passes(value: float | None, target: float | None, op: str | None) -> bool | None:
    if value is None or target is None or not op:
        return None
    return {
        ">": value > target,
        ">=": value >= target,
        "<": value < target,
        "<=": value <= target,
    }[op]


def write_result(
    result_id: str,
    *,
    value: float | None,
    metrics: dict[str, Any],
    dataset: dict[str, Any],
    model: dict[str, Any] | None,
    device: str | None,
    started: float,
    notes: list[str] | None = None,
    status: str = "measured",
    secondary_value: float | None = None,
    extra: dict[str, Any] | None = None,
    out_dir: Path | None = None,
) -> Path:
    """Write eval/results/<id>.json. `value` is the primary metric named in criteria.json."""
    crit = criterion(result_id)
    if value is not None and value != value:  # NaN: nothing was measured
        raise SystemExit(f"{result_id}: primary metric is NaN - refusing to write a result")
    primary = {
        "metric": crit["metric"],
        "value": None if value is None else round(float(value), 4),
        "target": crit.get("target"),
        "op": crit.get("op"),
        "unit": crit.get("unit"),
        "passed": passes(value, crit.get("target"), crit.get("op")),
    }
    result: dict[str, Any] = {
        "schema": RESULT_SCHEMA,
        "id": result_id,
        "title": crit["title"],
        "criterion": crit["criterion"],
        "status": status,
        "primary": primary,
        "metrics": metrics,
        "dataset": dataset,
        "model": model or {},
        "hardware": hardware_info(device),
        "git": git_info(),
        "script": crit.get("script"),
        "command": " ".join([Path(sys.argv[0]).name, *sys.argv[1:]]),
        "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "duration_s": round(time.time() - started, 1),
        "peak_rss_mb": peak_rss_mb(),
        "notes": notes or [],
    }
    sec = crit.get("secondary")
    if sec:
        result["secondary"] = {
            "metric": sec["metric"],
            "value": None if secondary_value is None else round(float(secondary_value), 4),
            "target": sec.get("target"),
            "op": sec.get("op"),
            "passed": passes(secondary_value, sec.get("target"), sec.get("op")),
        }
        if result["primary"]["passed"] is not None and result["secondary"]["passed"] is not None:
            result["passed"] = bool(result["primary"]["passed"] and result["secondary"]["passed"])
    if "passed" not in result:
        result["passed"] = primary["passed"]
    if extra:
        result.update(extra)
    out = (out_dir or RESULTS_DIR) / f"{result_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(result, indent=2) + "\n")
    tmp.replace(out)
    log(f"wrote {out.relative_to(REPO) if out.is_relative_to(REPO) else out}: "
        f"{primary['metric']}={primary['value']} target {primary['op']} {primary['target']} -> passed={result['passed']}")
    return out


def base_parser(description: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--device", default="auto", help="auto | cpu | cuda:0 (auto picks CUDA when present)")
    p.add_argument("--threads", type=int, default=4, help="CPU thread cap (default 4)")
    p.add_argument("--refreeze", action="store_true", help="Rebuild the frozen dataset manifest")
    p.add_argument("--out-dir", type=Path, default=None, help="Write the result JSON here instead of eval/results/")
    return p


# --------------------------------------------------------------------------- downloads
def download(url: str, dest: Path, sha256: str | None = None, timeout: int = 120) -> Path:
    """Stream `url` to `dest` (atomic), verifying sha256 when given. Skips if already valid."""
    import urllib.request  # noqa: PLC0415

    if dest.is_file() and (sha256 is None or sha256_file(dest) == sha256):
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "omnitrack-eval/1.0"})
    log(f"downloading {url}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp, part.open("wb") as fh:
            while chunk := resp.read(1 << 20):
                fh.write(chunk)
        if sha256 and sha256_file(part) != sha256:
            raise SystemExit(f"sha256 mismatch for {url}; refusing to use it")
        part.replace(dest)
    finally:
        part.unlink(missing_ok=True)
    return dest


# --------------------------------------------------------------------------- metrics
def box_iou(a, b):
    """IoU matrix between xyxy arrays a (N,4) and b (M,4)."""
    import numpy as np  # noqa: PLC0415

    a = np.asarray(a, dtype=np.float64).reshape(-1, 4)
    b = np.asarray(b, dtype=np.float64).reshape(-1, 4)
    if not len(a) or not len(b):
        return np.zeros((len(a), len(b)))
    tl = np.maximum(a[:, None, :2], b[None, :, :2])
    br = np.minimum(a[:, None, 2:], b[None, :, 2:])
    inter = np.clip(br - tl, 0, None).prod(-1)
    area_a = (a[:, 2:] - a[:, :2]).clip(0).prod(-1)
    area_b = (b[:, 2:] - b[:, :2]).clip(0).prod(-1)
    return inter / np.maximum(area_a[:, None] + area_b[None, :] - inter, 1e-9)


def average_precision(scores, is_tp, n_gt: int) -> dict[str, float]:
    """COCO-style 101-point interpolated AP from per-detection scores/TP flags."""
    import numpy as np  # noqa: PLC0415

    if n_gt == 0:
        return {"ap": float("nan"), "max_recall": float("nan")}
    scores = np.asarray(scores, dtype=np.float64)
    is_tp = np.asarray(is_tp, dtype=bool)
    order = np.argsort(-scores, kind="stable")
    tp = np.cumsum(is_tp[order])
    fp = np.cumsum(~is_tp[order])
    recall = tp / n_gt
    precision = tp / np.maximum(tp + fp, 1e-9)
    # monotone precision envelope
    env = np.maximum.accumulate(precision[::-1])[::-1] if len(precision) else precision
    ap = 0.0
    for r in np.linspace(0, 1, 101):
        idx = np.searchsorted(recall, r, side="left")
        ap += env[idx] if idx < len(env) else 0.0
    return {
        "ap": float(ap / 101),
        "max_recall": float(recall[-1]) if len(recall) else 0.0,
    }
