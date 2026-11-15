"""Shared pytest configuration for the OmniTrack backend.

Conventions:
- ``@pytest.mark.slow``  - loads a model / decodes video; excluded in CI.
- ``@pytest.mark.gpu``   - needs CUDA; skipped automatically when absent.
- ``@pytest.mark.db``    - needs PostgreSQL; skipped automatically when the
  host from DATABASE_URL is unreachable.

Heavy imports (torch, ultralytics) are deliberately NOT done here so the fast
suite starts in well under a second.
"""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# Keep CPU-bound libraries polite on shared/dev machines and CI runners.
for _var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_var, "4")
# Never let a test silently fall back to fabricated AI output.
os.environ.setdefault("ALLOW_MOCK_AI", "false")


def _cuda_available() -> bool:
    try:
        import torch  # noqa: PLC0415 - only imported when a gpu test is collected
    except Exception:
        return False
    try:
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _db_reachable() -> bool:
    url = os.environ.get("DATABASE_URL")
    if not url:
        try:
            from app.config import settings  # noqa: PLC0415

            url = settings.DATABASE_URL
        except Exception:
            return False
    parsed = urlparse(url.replace("+asyncpg", ""))
    host, port = parsed.hostname or "localhost", parsed.port or 5432
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    gpu_items = [i for i in items if "gpu" in i.keywords]
    if gpu_items and not _cuda_available():
        skip = pytest.mark.skip(reason="CUDA device not available")
        for item in gpu_items:
            item.add_marker(skip)
    db_items = [i for i in items if "db" in i.keywords]
    if db_items and not _db_reachable():
        skip = pytest.mark.skip(reason="PostgreSQL not reachable")
        for item in db_items:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def backend_root() -> Path:
    return BACKEND_ROOT


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return BACKEND_ROOT.parent
