# OmniTrack Scalability And Plugin Base

This document is the baseline for growing OmniTrack without turning the core app into a pile of one-off integrations.

## Design Goal

Keep the API responsive, keep AI processing bounded, and let future modules attach through explicit contracts. New analytics, store systems, alerting integrations, custom models, and client-specific features should plug into stable extension points instead of editing `main.py`, `pipeline.py`, and every dashboard page.

## Current Foundation

- FastAPI is mostly stateless and can scale horizontally once pipeline work is separated or sharded.
- `ProcessingPipeline` already supports result callbacks and lifecycle hooks.
- `PersistencePipelineCallback` already decouples live processing from database writes.
- PostgreSQL plus pgvector gives a good base for identity search, audit history, and analytics queries.
- Redis is present and should become the coordination layer for queues, rate limits, hot state, and fanout.

## Plugin Contract

Trusted backend plugins are enabled with:

```env
ENABLED_PLUGINS=["my_company.loss_prevention","my_company.pos_bridge"]
```

A plugin module may expose any of these:

```python
from fastapi import APIRouter

router = APIRouter(prefix="/api/plugins/example", tags=["Plugin: Example"])

def setup(context):
    return ExamplePlugin(context)

async def on_startup(context):
    ...

async def on_shutdown(context):
    ...

async def on_pipeline_results(results, global_state):
    ...

async def on_lifecycle(event, payload):
    ...
```

Plugin hooks must be non-blocking. Heavy work should be queued, batched, or sent to a worker process. A plugin must not directly block camera processing, hold raw frames longer than needed, or write unbounded data into memory.

## Scaling Model

The target enterprise shape is:

1. API service: auth, CRUD, dashboard reads, websocket fanout, plugin routes.
2. Camera ingest workers: one process or container shard per camera group.
3. Inference workers: GPU/CPU optimized workers with bounded queues and batch inference.
4. Persistence workers: write detections, embeddings, events, and aggregates asynchronously.
5. PostgreSQL/pgvector: durable source of truth, tuned indexes, retention policies.
6. Redis: cache, rate limits, queue coordination, lightweight pub/sub.
7. Object storage or mounted volumes: footage, clips, exports, model weights.

## Low-Cost Performance Rules

- Process fewer pixels first: ROI crop, lower inference resolution, camera-side substreams.
- Process fewer frames when possible: adaptive FPS, skip frames, motion gating, event-triggered analytics.
- Batch inference by model and device; avoid loading one YOLO instance per camera unless models differ.
- Keep queues bounded and drop stale frames before the system falls behind.
- Persist aggregates at intervals; do not write every derived metric on every frame.
- Use pgvector for search, but keep hot Re-ID gallery in memory for live matching.
- Run expensive modules selectively: emotion, demographics, synopsis, and adversarial eval should be opt-in or scheduled.
- Separate API and AI workers before adding more cameras. Scaling uvicorn workers will not solve GPU saturation.

## Runtime Controls Now In Config

- `MAX_CAMERAS`: hard cap enforced by the pipeline before a new camera is registered.
- `PROCESSING_FPS`: target processing loop rate for live inference.
- `FRAME_BUFFER_SIZE`: bounded per-camera frame queue size; stale frames are dropped instead of building latency.
- `DEFAULT_SKIP_FRAMES`: default frame skipping for low-cost edge or CPU deployments.
- `MAX_CPU_WORKERS`, `OMP_NUM_THREADS`, `MKL_NUM_THREADS`, `OPENBLAS_NUM_THREADS`: cost controls to avoid thread oversubscription.
- `ENABLED_PLUGINS`: explicit plugin allow-list.

## Next Refactor Targets

- Move Re-ID API routes from generated demo data to pgvector and journey tables.
- Replace random analytics fallbacks with empty states plus persisted aggregate reads.
- Add a queue abstraction behind pipeline callbacks so slow plugins cannot slow camera ticks.
- Add model registry metadata: model type, labels, device target, cost profile, and compatible plugin.
- Add per-camera budgets: max FPS, max resolution, enabled modules, and priority.
- Add observability: per-camera latency, dropped frames, queue depth, inference time, DB write lag, plugin hook latency.
