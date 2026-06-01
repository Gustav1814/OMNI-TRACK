#!/bin/sh
set -e

echo "OmniTrack — applying database migrations..."
alembic upgrade head

echo "OmniTrack — starting API..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
