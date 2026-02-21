# OmniTrack AI — Backend (Production-grade multi-stage build)
# Python 3.11, non-root user, minimal footprint

FROM python:3.11-slim as builder

WORKDIR /build

# Build dependencies (some packages need compile)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ---------------------------------------------------------------------------
FROM python:3.11-slim as runtime

WORKDIR /app

# Runtime deps only
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

# Non-root user for security
RUN groupadd -r omnitrack && useradd -r -g omnitrack omnitrack

COPY --chown=omnitrack:omnitrack app/ ./app/
COPY --chown=omnitrack:omnitrack alembic.ini ./
COPY --chown=omnitrack:omnitrack alembic/ ./alembic/

USER omnitrack

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

# Health check (python -c to avoid relying on curl in slim image)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
