"""
Model selection helpers shared by detection and pipeline camera endpoints.

The important invariant is that "single" resolves to exactly one weight and
"ensemble" is the only mode allowed to resolve to multiple weights.
"""

import os
from pathlib import Path
from typing import List, Optional, Tuple

from app.config import settings


MODEL_EXTENSIONS = {".pt", ".onnx", ".engine", ".tflite"}


def parse_model_selection(model: Optional[str] = None, models: Optional[str] = None) -> List[str]:
    raw = models if models is not None else model
    if not raw:
        return []
    selected: List[str] = []
    for item in str(raw).replace(";", ",").split(","):
        name = os.path.basename(item.strip())
        if name and name not in selected:
            selected.append(name)
    return selected


def default_weight_names() -> List[str]:
    return [settings.DEFAULT_YOLO_MODEL]


def list_all_weight_names() -> List[str]:
    weights_dir = Path(settings.MODEL_WEIGHTS_DIR)
    if not weights_dir.is_dir():
        return []
    names = [
        f.name
        for f in weights_dir.iterdir()
        if f.is_file() and f.suffix.lower() in MODEL_EXTENSIONS and not f.name.startswith(".")
    ]
    return sorted(dict.fromkeys(names))


def select_smart_weight_names(zone: str = "", source: str = "", prefer_ensemble: bool = False) -> List[str]:
    """
    Select a small compatible person-detection set.

    This intentionally avoids "run every .pt file" because folders often contain
    face, fire, product, pose, or segmentation weights that are not equivalent
    person detectors.
    """
    names = list_all_weight_names()
    if not names:
        return []

    lower_by_name = {name: name.lower() for name in names}

    def first_matching(*needles: str) -> Optional[str]:
        for needle in needles:
            for name, lower in lower_by_name.items():
                if needle in lower:
                    return name
        return None

    def general_candidates() -> List[str]:
        ordered: List[str] = []
        for needle in ("yolo11", "yolov8", "yolo26", "general"):
            hit = first_matching(needle)
            if hit and hit not in ordered:
                ordered.append(hit)
        excluded = ("face", "fire", "smoke", "product", "pose", "sam", "seg", "pe_")
        for name in names:
            lower = lower_by_name[name]
            if name not in ordered and not any(token in lower for token in excluded):
                ordered.append(name)
        return ordered

    zone_text = f"{zone} {source}".lower()
    selected: List[str] = []
    general = general_candidates()

    def add(name: Optional[str]) -> None:
        if name and name not in selected:
            selected.append(name)

    if any(k in zone_text for k in ("fire", "smoke", "kitchen", "storage", "safety")):
        add(first_matching("fire", "smoke"))
        add(general[0] if general else None)
    elif any(k in zone_text for k in ("shelf", "product", "aisle", "inventory", "stock")):
        add(first_matching("product"))
        add(general[0] if general else None)
    else:
        add(general[0] if general else None)
        if prefer_ensemble and len(general) > 1:
            add(general[1])

    return selected[:2] or names[:1]


def select_model_names(
    model_mode: str = "manual",
    model: Optional[str] = None,
    models: Optional[str] = None,
    zone: str = "",
    source: str = "",
) -> Tuple[List[str], str]:
    mode = (model_mode or "manual").strip().lower()

    if mode in {"auto_ensemble", "smart", "smart_ensemble"}:
        selected = select_smart_weight_names(zone=zone, source=source, prefer_ensemble=True)
    elif mode in {"auto_all", "all"}:
        selected = list_all_weight_names()
        if not selected:
            raise ValueError(f"No model weights found in {settings.MODEL_WEIGHTS_DIR}")
    elif mode == "auto":
        selected = default_weight_names()
    elif mode == "single":
        selected = parse_model_selection(model=model, models=models)
        if len(selected) != 1:
            raise ValueError("Single model mode needs exactly one selected weight")
    elif mode == "ensemble":
        selected = parse_model_selection(model=model, models=models)
        if len(selected) < 2:
            raise ValueError("Multi-model ensemble needs at least two selected weights")
    else:
        selected = parse_model_selection(model=model, models=models)
        if not selected:
            selected = default_weight_names()

    if not selected:
        selected = default_weight_names()

    effective_mode = "ensemble" if len(selected) > 1 else "single"
    return selected, effective_mode


def resolve_model_paths(selected: List[str]) -> List[str]:
    if not selected:
        return []
    paths: List[str] = []
    for name in selected:
        model_file = Path(settings.MODEL_WEIGHTS_DIR) / name
        if model_file.exists():
            paths.append(str(model_file.resolve()))
            continue
        cwd_candidate = Path(name)
        if cwd_candidate.is_file():
            paths.append(str(cwd_candidate.resolve()))
            continue
        if name == settings.DEFAULT_YOLO_MODEL:
            paths.append(name)
            continue
        raise FileNotFoundError(f"Model {name} not found in {settings.MODEL_WEIGHTS_DIR}")
    return paths
