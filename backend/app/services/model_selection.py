"""
Model selection for the person stream.

There is exactly one person detector per deployment, chosen from evaluation
evidence by settings (``PERSON_MODEL`` or ``PERSON_MODEL_CPU`` / ``PERSON_MODEL_GPU``
for the resolved device; see ``Settings.person_model_for``). Fire, product, face
and emotion models run as separate specialist streams with their own cadence and
never feed the tracker or people counts, so there is nothing to pick per feed and
no ensemble.

The API still accepts ``model`` / ``models`` / ``model_mode`` for compatibility:

* ``auto`` / ``manual`` / ``single`` with no or one weight -> the configured person model.
  A different weight name is accepted only if it exists in ``model_weight/``; the
  pipeline logs that per-feed weights are ignored.
* ensemble-style modes or several weights -> ``ValueError`` (HTTP 400).
"""

from pathlib import Path
from typing import List, Optional, Tuple

from app.config import settings
from app.security.input_safety import ALLOWED_MODEL_SUFFIXES, validate_model_name


MODEL_EXTENSIONS = set(ALLOWED_MODEL_SUFFIXES)  # what validate_model_name will accept
SINGLE_MODES = {"auto", "manual", "single", ""}
REJECTED_MESSAGE = (
    "Multi-weight detection ({what}) is not supported: every camera uses the one person "
    "detector chosen from evaluation ({model}); fire/product/emotion run as separate streams."
)


class ModelModeDisabledError(ValueError):
    """Raised for API requests that select an ensemble / multi-weight mode."""


def person_model_name(device: Optional[str] = None) -> str:
    """File name of the person detector for ``device`` (default: settings.DEVICE resolved)."""
    from app.ai.detector import resolve_device

    path, _imgsz = settings.person_model_for(resolve_device(device or settings.DEVICE))
    return Path(path).name


def parse_model_selection(model: Optional[str] = None, models: Optional[str] = None) -> List[str]:
    raw = models if models is not None else model
    if not raw:
        return []
    selected: List[str] = []
    for item in str(raw).replace(";", ",").split(","):
        name = Path(item.strip()).name
        if name and name not in selected:
            selected.append(name)
    return selected


def ensure_model_mode_allowed(
    model_mode: str = "manual",
    model: Optional[str] = None,
    models: Optional[str] = None,
) -> None:
    mode = (model_mode or "manual").strip().lower()
    if mode not in SINGLE_MODES:
        raise ModelModeDisabledError(REJECTED_MESSAGE.format(what=f"model_mode={mode!r}", model=person_model_name()))
    if len(parse_model_selection(model=model, models=models)) > 1:
        raise ModelModeDisabledError(REJECTED_MESSAGE.format(what="more than one weight", model=person_model_name()))


def select_model_names(
    model_mode: str = "manual",
    model: Optional[str] = None,
    models: Optional[str] = None,
    zone: str = "",
    source: str = "",
) -> Tuple[List[str], str]:
    """``([weight name], "single")``. ``zone``/``source`` are accepted for compatibility."""
    ensure_model_mode_allowed(model_mode, model, models)
    selected = parse_model_selection(model=model, models=models)
    return (selected or [person_model_name()]), "single"


def list_all_weight_names() -> List[str]:
    weights_dir = Path(settings.MODEL_WEIGHTS_DIR)
    if not weights_dir.is_dir():
        return []
    return sorted(
        f.name for f in weights_dir.iterdir()
        if f.is_file() and f.suffix.lower() in MODEL_EXTENSIONS and not f.name.startswith(".")
    )


def resolve_model_paths(selected: List[str]) -> List[str]:
    """Real paths of bare weight names inside ``MODEL_WEIGHTS_DIR``.

    Weights are pickles, so anything that is not a bare name of an allowed weight type
    inside that directory (traversal, symlink escape, other suffix) raises
    ``UnsafeInputError`` (a ``ValueError``) and is never loaded.
    """
    return [str(validate_model_name(name)) for name in selected or []]
