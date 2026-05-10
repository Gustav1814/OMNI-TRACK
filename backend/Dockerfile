# OmniTrack AI — Backend (Production-grade multi-stage build)
# Python 3.11, non-root user, minimal footprint

FROM python:3.11-slim AS builder

WORKDIR /build

# So pip --user console_scripts land on PATH during install (silences "not on PATH" warnings)
ENV PATH=/root/.local/bin:$PATH

# Build dependencies (some packages need compile)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-core.txt requirements-docker.txt .
# CPU-only PyTorch first (avoids ~900MB+ CUDA/cuDNN wheels in typical Linux containers).
RUN pip install --no-cache-dir --user \
    torch torchvision \
    --index-url https://download.pytorch.org/whl/cpu
# requirements-docker.txt omits DeepFace/TensorFlow (large); emotion falls back to mock.
RUN pip install --no-cache-dir --user -r requirements-docker.txt

# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

WORKDIR /app

# Runtime deps (libxcb*: opencv-python from ultralytics still links X11 stack on manylinux wheels)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    libglib2.0-0 \
    libgomp1 \
    libsm6 \
    libxcb1 \
    libxext6 \
    libxrender1 \
    && rm -rf /var/lib/apt/lists/*

# Real home so user-site-packages resolve correctly for the non-root user
RUN groupadd -r omnitrack && useradd -r -g omnitrack -m -d /home/omnitrack omnitrack

# Copy deps into omnitrack-owned tree (/root is not readable by non-root)
COPY --from=builder --chown=omnitrack:omnitrack /root/.local /home/omnitrack/.local

ENV PATH=/home/omnitrack/.local/bin:$PATH

# Writable dirs (weights come from bind-mount in docker-compose; *.pt is dockerignored)
RUN mkdir -p /app/model_weight /app/storage/footage /app/storage/logs /app/exports \
    && chown -R omnitrack:omnitrack /app/model_weight /app/storage /app/exports

COPY --chown=omnitrack:omnitrack app/ ./app/
COPY --chown=omnitrack:omnitrack alembic.ini ./
COPY --chown=omnitrack:omnitrack alembic/ ./alembic/

USER omnitrack

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

# Health check (python -c to avoid relying on curl in slim image)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
