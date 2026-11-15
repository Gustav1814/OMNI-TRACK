# OmniTrack AI

**Professional-grade retail analytics platform** — transform surveillance feeds into actionable business intelligence with a hardened security posture.

- **Detection & Tracking:** YOLOv8/v11 + ByteTrack for real-time person detection and multi-object tracking  
- **Global Re-ID:** Torchreid embeddings + pgvector for cross-camera identity matching  
- **Video Synopsis:** Condensed summaries of long footage (moving-object segments)  
- **Analytics:** Shelf engagement, fire/smoke detection, crowd density, checkout metrics, emotion recognition, Store Vibe  
- **Security:** SHA-256 audit chain, AES-256 encryption (PyCryptodome), JWT auth, rate limiting, security headers  
- **Adversarial robustness:** Documented resilience; optional [ART](https://github.com/Trusted-AI/adversarial-robustness-toolbox) evaluation (FGSM, PGD) via `app.security.adversarial_eval`

---

## Backend: FastAPI

The **API is built with [FastAPI](https://fastapi.tiangolo.com/)**:

- **Entry point:** `backend/app/main.py` — `app = FastAPI(...)`, lifespan, middleware, routers.
- **Routers (all under `/api/...`):** `app/routers/` holds auth, cameras, detection, reid, footage, model, zones, zone_analytics (shelf, crowd, checkout, fire, emotion, vibe, demographics, peak-hours, dashboard), alerts, evidence, export, synopsis, security (security + audit), setup, humanless, evaluation and pipeline_health. Each uses `APIRouter(prefix="/api/...")`.
- **Core routes in main.py:** `/api/health`, `/api/pipeline/*`, `/api/stream/camera/{id}/live`, `/api/export/*`, WebSocket `/ws/live`, `/ws/camera/{id}`.
- **Run:** `uvicorn app.main:app --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown 3` (serves the FastAPI app). Interactive docs: http://localhost:8000/docs .

---

## Quick Start

Docker runs only the infrastructure (PostgreSQL + pgvector, Redis). The backend and frontend run from the terminal.

**Prerequisites:** Python 3.12 (torch/torchreid are not reliable on 3.14), Node 20+, Docker, [uv](https://docs.astral.sh/uv/) (or pip).

1. **Infrastructure**

   ```bash
   docker compose up -d        # postgres (pgvector/pg16) + redis
   ```

   The backend creates the schema and the pgvector extension on first start.

2. **Secrets** (required — the backend refuses to start with missing or placeholder secrets)

   ```bash
   cd backend
   python scripts/generate_secrets.py                       # writes backend/.env (mode 0600): JWT, session, AES and audit-HMAC keys
   python scripts/generate_secrets.py --rotate-db-password  # replaces the compose default Postgres password and updates DATABASE_URL
   ```

   Values are never printed. Keep `backend/.env` out of git (it is ignored). Rotating the AES or audit key later makes stored ciphertext / the audit chain unverifiable, so the script never does it silently.

3. **Backend** (terminal 1)

   ```bash
   cd backend
   uv venv --python 3.12 .venv
   # CPU-only machine:
   uv pip install --python .venv/bin/python -r requirements.txt \
       --extra-index-url https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match
   # NVIDIA machine: install requirements.txt, then the CUDA torch build (see the header of requirements.txt)
   .venv/bin/python scripts/download_default_weights.py     # every weight, sha256-verified (none are in git)
   .venv/bin/python scripts/create_admin.py <username> <email>   # prompts for the password
   .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown 3
   ```

   API: http://localhost:8000 · Docs: http://localhost:8000/docs · Pipeline health: http://localhost:8000/api/pipeline/health.
   `DEVICE=auto` (default) uses CUDA when present, otherwise CPU. A missing weight disables only that feature and is logged; nothing is faked (`ALLOW_MOCK_AI=false`).

4. **Frontend** (terminal 2)

   ```bash
   cd frontend
   npm ci
   npm run dev
   ```

   Open http://localhost:5173 (Vite proxies `/api` and `/ws` to the backend) and log in with the admin account.

5. **Demo footage** — add cameras in *Store Setup → Cameras* pointing at files under `backend/storage/demo_videos/` (CAVIAR two-view clips, Mall, and the reference clips listed in `backend/storage/demo_videos/reference/SOURCES.md`).

6. **Tests**

   ```bash
   cd backend && .venv/bin/python -m pytest -m "not slow"   # fast suite; gpu/db tests skip automatically
   cd frontend && npm test                                   # vitest page smoke tests
   ```

   CI (`.github/workflows/ci.yml`) runs `ruff --select F`, the fast pytest suite on CPU torch, `npm run build` and vitest.

---

## Project standard (dependencies)

Use Python 3.11, 3.12, or 3.14 for the backend:

1. `cd backend`
2. `python -m venv .venv`
3. Activate: `.venv\Scripts\activate` (Windows) or `source .venv/bin/activate` (Linux/macOS)
4. `pip install -r requirements.txt`

- **Optional (proposal):** Adversarial robustness with ART:  
  `pip install adversarial-robustness-toolbox[torch]`  
  Then run: `python -m app.security.adversarial_eval`

- **Python 3.14:** You can also install from `requirements-py314.txt`; it contains the same lightweight CV stack without extra training/runtime packages.

**What you need to install (full standard):**

| Requirement | What to install |
|-------------|-----------------|
| Python | **3.11, 3.12, or 3.14** from [python.org](https://www.python.org/downloads/) |
| Backend deps | `pip install -r requirements.txt` |
| ART (optional) | `pip install adversarial-robustness-toolbox[torch]` |
| PostgreSQL | With **pgvector** extension (e.g. Docker: `pgvector/pgvector:pg16`) |
| Redis | Optional; in-memory fallback if not present |
| Node.js | For frontend (e.g. Node 18+) |

---

## Project layout

```
OMNI-TRACK/
├── backend/                 # FastAPI app
│   ├── app/
│   │   ├── ai/              # YOLO, Re-ID, emotion, fire, crowd, shelf, checkout, vibe, synopsis
│   │   ├── models/          # SQLAlchemy + pgvector
│   │   ├── analytics/       # zone engine (crowd, shelf, queue, dwell, line crossing)
│   │   ├── routers/         # one module per API area (see API overview)
│   │   ├── security/        # JWT, AES-GCM encryption, HMAC audit chain, input safety
│   │   ├── services/        # pipeline, track_coordinator, stream_manager, evidence, export, persistence
│   │   ├── middleware/      # tracing, security headers, rate limit
│   │   ├── config.py
│   │   ├── database.py
│   │   └── main.py
│   ├── alembic/             # Versioned schema migrations (Alembic)
│   ├── alembic.ini
│   ├── data/                # Local test videos (generated by scripts/smoke_test.py)
│   ├── model_weight/        # small .pt files in git; run scripts/download_default_weights.py for the rest
│   ├── scripts/             # smoke_test.py and other dev scripts
│   ├── requirements.txt
│   └── .env.example
├── frontend/                # React + Vite
│   ├── src/
│   │   ├── pages/
│   │   ├── components/
│   │   ├── services/api.js
│   │   └── App.jsx
│   └── package.json
├── docs/                    # Proposal and dev notes (see docs/README.md)
├── docker-compose.yml       # postgres (pgvector) + redis only
└── README.md
```

---

## Environment

| Variable | Description | Default |
|----------|-------------|--------|
| `DATABASE_URL` | PostgreSQL (async) URL | `postgresql+asyncpg://omnitrack:omnitrack_secret@localhost:5432/omnitrack_db` |
| `REDIS_URL` | Redis URL | `redis://localhost:6379/0` |
| `JWT_SECRET_KEY` | Secret for access/refresh tokens | *(change in production)* |
| `AES_SECRET_KEY` | 32-byte hex key for AES-256 | *(set in production)* |
| `CORS_ORIGINS` | Allowed origins (JSON array) | `["http://localhost:5173","http://localhost:3000"]` |
| `MODEL_WEIGHTS_DIR` | Folder for YOLO `.pt` files | `model_weight` |
| `YOLO_MODEL` | Detection model path | `yolov8n.pt` |
| `DB_POOL_SIZE` | SQLAlchemy connection pool size | `20` |
| `DB_RUN_MIGRATIONS_ON_STARTUP` | Run Alembic on API startup | `true` |
| `DETECTION_RETENTION_DAYS` | Auto-purge detections older than N days | `30` |
| `EMBEDDING_RETENTION_DAYS` | Auto-purge embeddings older than N days | `90` |
| `DEVICE` | `auto` / `cpu` / `cuda` / `mps` | `auto` |

Copy `backend/.env.example` to `backend/.env` and adjust.

### Database (enterprise)

- **Migrations:** `cd backend && alembic upgrade head` (also runs on API startup locally; Docker entrypoint runs migrations before uvicorn).
- **Maintenance CLI:** `python -m scripts.db_maintenance health|analyze|retention|migrate`
- **Re-ID search:** pgvector **HNSW** index on `embeddings.vector` for sub-100ms cosine similarity.
- **Retention:** Configurable auto-purge for detections, embeddings, and analytics (audit logs are never deleted).
- **Health:** `GET /api/health` includes pool stats, migration revision, and HNSW index status.

---

## API overview

| Area | Prefix | Examples |
|------|--------|----------|
| Auth | `/api/auth` | `POST /login`, `POST /register`, `GET /me` |
| Cameras | `/api/cameras` | CRUD cameras |
| Detection | `/api/detection` | Start/stop, status, results, recording start/stop |
| Re-ID | `/api/reid` | Search, journey, active, `GET /thumb/{global_id}` (best crop), topology, floor calibration (3×3 matrix or 4+ clicked point pairs) |
| Zones | `/api/zones` | Per-camera zones (crowd, shelf, queue, counter, entry line, restricted area), camera snapshot, floor heatmap |
| Alerts | `/api/alerts` | List, facets, acknowledge |
| Evidence | `/api/evidence` | Encrypted snapshots and clips per alert |
| Export | `/api/export` | CSV/JSON per dataset, full ZIP |
| Security | `/api/security` | Status, robustness evaluation |
| Setup | `/api/setup` | Store templates, store profile |
| Evaluation | `/api/evaluation` | Measured results from `eval/results` |
| Models | `/api/models` | List YOLO weights, classes |
| Dashboard | `/api/dashboard` | `GET /overview` |
| Synopsis | `/api/synopsis` | List, generate |
| Shelf | `/api/shelf` | Engagement, top-zones |
| Fire | `/api/fire` | Alerts, status |
| Crowd | `/api/crowd` | Status, history |
| Checkout | `/api/checkout` | Metrics, summary |
| Emotion | `/api/emotion` | Current, store-sentiment |
| Audit | `/api/audit` | Logs, verify chain |
| Vibe | `/api/vibe` | Current, trend |
| Demographics | `/api/demographics` | Current |
| Peak hours | `/api/peak-hours` | Today |
| Footage | `/api/footage` | List, upload, serve (CCTV clips) |
| Pipeline | `/api/pipeline/*` | Status, start, stop, add camera, results |
| Stream | `/api/stream/camera/{id}/live` | MJPEG live feed (auth: Bearer or `?token=`) |
| System | `/api/health` | Health check |
| WebSocket | `/ws/live`, `/ws/camera/{id}` | Live dashboard feed |

---

## Success criteria (FYP)

Every proposal criterion has a reproducible script in `eval/`, a frozen dataset manifest and a result file in
`eval/results/`. The **Evaluation** page in the dashboard (`/evaluation`, API `GET /api/evaluation/summary`) shows
target vs measured, pass/fail, dataset, hardware and date; a criterion that has not been measured shows
"Not measured", never a number. Method, datasets and the current numbers (including the ones that miss their
target): [docs/EVALUATION.md](docs/EVALUATION.md).

---

## Backend standards (proposal alignment)

| Requirement | Implementation |
|-------------|----------------|
| **Tamper-evident audit** | HMAC-SHA256 chain keyed by `AUDIT_HMAC_KEY` (`AuditService.log_event`); see docs/SECURITY.md |
| **AES-256 encryption** | `app.security.encryption` (AES-256-GCM, PyCryptodome); audit metadata, face crops and alert evidence |
| **Audit chain verification** | `GET /api/audit/verify` uses `AuditService.verify_integrity` (100% tamper detection) |
| **pgvector cosine similarity** | `Embedding.vector` uses pgvector `Vector(512)`; `EmbeddingService.search_similar` for sub-100ms retrieval |
| **Adversarial robustness (ART)** | `app.security.adversarial_eval`; optional ART for FGSM/PGD; `GET /api/security/robustness` |

**Adversarial robustness (ART):**

- **Install:** `pip install adversarial-robustness-toolbox[torch]`
- **Status:** `GET /api/security/robustness` — returns `art_available`, `last_eval`, and install hint.
- **Run eval:** `POST /api/security/robustness/run` (optional query: `sample_size`, `eps_fgsm`, `eps_pgd`, `pgd_steps`) or CLI: `python -m app.security.adversarial_eval`
- **FGSM / PGD:** Implemented via ART on a classifier path (documented resilience). Full YOLO detector eval would require wrapping Ultralytics to ART’s object-detection interface.
- **Adversarial patch:** Documented; ART provides a notebook for patch attacks on YOLO: `notebooks/adversarial_patch/attack_adversarial_patch_pytorch_yolo.ipynb` in the [ART repo](https://github.com/Trusted-AI/adversarial-robustness-toolbox).

### Re-ID tuning

Re-ID is body-based (OSNet-AIN, `REID_MODEL` + `REID_WEIGHTS_PATH`, MSMT17 weights by default), so it works when the face is turned away. Settings in `backend/.env`:

- **Quality gate** (`REID_MIN_CROP_HEIGHT`, `REID_MIN_ASPECT`/`REID_MAX_ASPECT`, `REID_EDGE_MARGIN_PX`, `REID_MAX_OCCLUSION_IOU`, `REID_MIN_DET_CONF`): crops that are small, cut by the frame edge, occluded or low-confidence are never embedded.
- **Tracklets** (`REID_TRACKLET_TOP_K`, `REID_TRACKLET_MIN_CROPS`, `REID_TRACKLET_MAX_WAIT_S`): a track's feature is the L2-normalised mean of its best crops. Single frames are never matched.
- **`REID_T_MATCH`** (default `0.75`): minimum cosine similarity for a tracklet to take an existing identity. Raise it if different people merge; lower it if one person gets several IDs. Calibrate it on the CAVIAR hold-out as described in `docs/REID_TRAINING.md`.
- **`REID_T_UPDATE`** (default `0.85`, above `T_MATCH`): a matched tracklet is added to the identity as a new view only above this, so one wrong match cannot poison the gallery. `REID_EMBEDDINGS_PER_ID` caps views per identity.
- **Topology** (`REID_CAMERA_TOPOLOGY` or `/api/reid/topology`): which camera can follow which, with min/max transit seconds. A match that breaks it is rejected. With no links, any pair is allowed within `REID_DEFAULT_MAX_TRANSIT_S`.
- **Floor calibration** (Setup → Zones → Floor calibration): click 4 or more measurable floor spots per camera and type their positions in metres from one shared corner. With 5 or more points the fit error is reported. Once two overlapping cameras are calibrated, a person standing within 0.75 m on both views, with agreeing appearance, keeps one global ID (fusion), so occupancy counts them once.
- **Known limit of the stock weights:** on CAVIAR's low-resolution crops (about 40×90 px), the stock OSNet-AIN sometimes gives two different people on the same camera the same global ID, or one person two. Fine-tune on the GPU (`docs/REID_TRAINING.md`) before retuning `REID_T_MATCH`; do not lower or raise thresholds by eye.

### Camera feeds and detection

Detection is wired to the **multi-camera pipeline**: when you start detection with a source, the backend adds that camera to the pipeline and runs YOLO + ByteTrack on it. Results are real (not mock).

- **Dashboard → Detection:** Use “Add camera & start detection”: set **Source** to `0` for default webcam, or a path like `C:/videos/test.mp4` (file), or an RTSP URL; pick **Type** (webcam/file/rtsp), then **Start detection**. The pipeline starts and the page shows live FPS and person counts per camera.
- **API:** `POST /api/detection/start/{camera_id}?source=0&stream_type=webcam` adds the feed and starts the pipeline; `GET /api/detection/results/{camera_id}` returns the latest detections from the pipeline.

### Local playback (FYP — no live cameras)

For demos and FYP evaluation **without live CCTV**, the system supports **local video playback** as if they were live feeds:

1. **Stored footage** — Upload clips via **Dashboard → Stored CCTV Footage** (or `POST /api/footage/upload`). On **Detection**, choose **Type: Video file** → **From stored footage** → select a clip, then **Start detection**. The backend resolves `footage:filename.mp4` to `storage/footage/filename.mp4` and runs the full pipeline (YOLO, ByteTrack, Re-ID, analytics) on that file.
2. **Custom path** — Use **Type: Video file** → **Custom path** and enter an absolute path to a `.mp4`/`.avi` file (e.g. `C:/videos/entrance.mp4`). The same pipeline runs on that file.
3. **Store CCTV prototype block** — The Detection page has a **Store CCTV prototype** section: use **Start all as store cameras** to run every stored clip as Camera 1, 2, 3, … in one go (same pipeline, shared Re-ID gallery).

Architecture is **production-ready**: the same pipeline and APIs support RTSP/live cameras when deployed; for FYP you only switch the source to local files or `footage:...`.

### Global ID and cross-camera data sharing

Code: `app/services/track_coordinator.py` (one coordinator shared by every camera).

1. **Local tracks.** Each camera runs its own ByteTrack. Only confirmed tracks (3+ hits) go further.
2. **Tracklets.** Crops that pass the quality gate are embedded (512-d, L2-normalised) and the best ones are averaged into one tracklet feature.
3. **Assignment.** Ready tracklets are matched to recently lost identities in one Hungarian assignment (cost = 1 - similarity). A pair is feasible only above `REID_T_MATCH` and inside the topology transit window, so two new tracks can never claim the same person. Unmatched tracklets get a new global ID, which then stays fixed for the life of the local track.
4. **Overlapping cameras.** When both cameras have a floor homography (Setup page), a track standing within `REID_FUSION_DISTANCE_M` of a track on the other camera, with agreeing appearance, takes its global ID. Occupancy then counts that person once.
5. **Persistence.** Identity views go to pgvector (HNSW, cosine). The in-memory gallery keeps recent identities and is warm-started from the database on boot. Assignments are published on the event bus and the Redis stream `tracks.assignments`.

---

## License & attribution

FYP project — BS Cybersecurity. See [docs/proposal](docs/proposal/) for the full abstract and references.
