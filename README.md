# OmniTrack AI

**Professional-grade retail analytics platform** — transform surveillance feeds into actionable business intelligence with a hardened security posture.

- **Detection & Tracking:** YOLOv8/v11 + ByteTrack for real-time person detection and multi-object tracking  
- **Global Re-ID:** Torchreid embeddings + pgvector for cross-camera identity matching  
- **Video Synopsis:** Condensed summaries of long footage (moving-object segments)  
- **Analytics:** Shelf engagement, fire/smoke detection, crowd density, checkout metrics, emotion recognition, Store Vibe  
- **Security:** SHA-256 audit chain, AES-256 encryption (PyCryptodome), JWT auth, rate limiting, security headers  
- **Adversarial robustness:** Documented resilience; optional [ART](https://github.com/Trusted-AI/adversarial-robustness-toolbox) evaluation (FGSM, PGD) via `app.security.adversarial_eval`

---

## Quick Start

### Option A: Docker (recommended)

```bash
cd FYP
docker compose up -d
```

- **Dashboard:** http://localhost:3000  
- **API:** http://localhost:8000  
- **API Docs:** http://localhost:8000/docs  

Default DB user/pass: `omnitrack` / `omnitrack_secret`. Create a user via `/api/auth/register` then log in at the dashboard.

### Option B: Local development

**Prerequisites:** **Python 3.11 or 3.12** (required for full project standard — see below), Node 18+, PostgreSQL (with [pgvector](https://github.com/pgvector/pgvector)), Redis (optional).

**If Python is from a zip:** Extract it to a folder (e.g. `C:\Python312`). Use that folder’s `python.exe` by full path to create the venv (see Backend step 2 below).

1. **Database**

   ```bash
   # PostgreSQL + pgvector (e.g. Docker)
   docker run -d --name pg -e POSTGRES_USER=omnitrack -e POSTGRES_PASSWORD=omnitrack_secret -e POSTGRES_DB=omnitrack_db -p 5432:5432 pgvector/pgvector:pg16
   ```

2. **Backend**

   ```bash
   cd backend
   copy .env.example .env   # edit if needed (Windows)

   # Use Python 3.11 or 3.12. If from a zip, set PY to the full path to python.exe, e.g.:
   # set PY=C:\Python312\python.exe
   "%PY%" -m venv .venv
   .venv\Scripts\activate   # Windows
   pip install -r requirements.txt
   uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```
   Example if Python is in `C:\Python312`: run `C:\Python312\python.exe -m venv .venv` then `.venv\Scripts\activate` and `pip install -r requirements.txt`.  
   If the project has a Python 3.12 venv named `.venv312`, use: `.\.venv312\Scripts\Activate.ps1` then `pip install -r requirements.txt` and `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`.

3. **Frontend**

   ```bash
   cd frontend
   npm install
   npm run dev
   ```

   Open http://localhost:5173 (Vite proxies `/api` and `/ws` to the backend).

---

## Project standard (dependencies)

The proposal requires **DeepFace/FER** for emotion recognition. DeepFace depends on **TensorFlow**, which does not provide wheels for **Python 3.14**. So:

- **Full standard (no compromise):** Use **Python 3.11 or 3.12** for the backend. Then:
  1. `cd backend`
  2. `python -m venv .venv` (or `py -3.12 -m venv .venv`)
  3. Activate: `.venv\Scripts\activate` (Windows) or `source .venv/bin/activate` (Linux/macOS)
  4. `pip install -r requirements.txt`

  That installs everything in the proposal stack, including **DeepFace** for emotion recognition.

- **Optional (proposal):** Adversarial robustness with ART:  
  `pip install adversarial-robustness-toolbox[torch]`  
  Then run: `python -m app.security.adversarial_eval`

- **If you must use Python 3.14:** Install from `requirements-py314.txt` instead. DeepFace will not be installed; emotion will run in fallback mode and **does not meet the full proposal standard** for that module.

**What you need to install (full standard):**

| Requirement | What to install |
|-------------|-----------------|
| Python | **3.11 or 3.12** from [python.org](https://www.python.org/downloads/) (not 3.14 for full stack) |
| Backend deps | `pip install -r requirements.txt` (includes DeepFace) |
| ART (optional) | `pip install adversarial-robustness-toolbox[torch]` |
| PostgreSQL | With **pgvector** extension (e.g. Docker: `pgvector/pgvector:pg16`) |
| Redis | Optional; in-memory fallback if not present |
| Node.js | For frontend (e.g. Node 18+) |

---

## Project layout

```
FYP/
├── backend/                 # FastAPI app
│   ├── app/
│   │   ├── ai/              # YOLO, Re-ID, emotion, fire, crowd, shelf, checkout, vibe, synopsis
│   │   ├── models/          # SQLAlchemy + pgvector
│   │   ├── routers/         # auth, cameras, detection, reid, analytics (synopsis, shelf, fire, crowd, checkout, emotion, audit, vibe, dashboard, etc.)
│   │   ├── security/        # JWT, hashing, encryption
│   │   ├── services/        # pipeline, stream_manager, cache, broadcast, export
│   │   ├── middleware/     # tracing, security headers, rate limit
│   │   ├── config.py
│   │   ├── database.py
│   │   └── main.py
│   ├── alembic/
│   ├── requirements.txt
│   ├── .env.example
│   └── Dockerfile
├── frontend/                # React + Vite
│   ├── src/
│   │   ├── pages/           # Dashboard, Detection, ReID, Synopsis, Shelf, Fire, Crowd, Checkout, Emotion, Audit, Vibe, PeakHours, Demographics, Login
│   │   ├── components/
│   │   ├── services/api.js
│   │   └── App.jsx
│   ├── package.json
│   └── Dockerfile
├── docker-compose.yml        # postgres (pgvector), redis, backend, frontend
├── nginx.conf               # optional reverse proxy (--profile with-nginx)
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
| `YOLO_MODEL` | Detection model path | `yolov8n.pt` |
| `DEVICE` | `auto` / `cpu` / `cuda` / `mps` | `auto` |

Copy `backend/.env.example` to `backend/.env` and adjust.

---

## API overview

| Area | Prefix | Examples |
|------|--------|----------|
| Auth | `/api/auth` | `POST /login`, `POST /register`, `GET /me` |
| Cameras | `/api/cameras` | CRUD cameras |
| Detection | `/api/detection` | Start/stop, status, results |
| Re-ID | `/api/reid` | Search, journey, active |
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
| System | `/api/health` | Health check |
| Pipeline | `/api/pipeline/*` | Status, start, stop, add camera, results |
| Detection | `/api/detection/*` | Start/stop (wired to pipeline), status, **real** results from YOLO + ByteTrack |
| WebSocket | `/ws/live`, `/ws/camera/{id}` | Live dashboard feed |

---

## Success criteria (FYP)

- **Search latency:** &lt; 100 ms (pgvector cosine similarity)  
- **Detection:** &gt; 85% mAP (YOLOv8/v11)  
- **Re-ID:** &gt; 70% Rank-1 (Market-1501)  
- **Video synopsis:** ≥ 10× compression  
- **Fire/smoke:** &gt; 90% precision, &lt; 5% FPR  
- **Crowd:** &gt; 85% zone classification accuracy  
- **Emotion:** &gt; 70% (FER benchmark)  
- **Integrity:** 100% tamper detection (SHA-256 chain)  
- **Throughput:** 20+ FPS multi-camera (async pipeline)  

---

## Backend standards (proposal alignment)

| Requirement | Implementation |
|-------------|----------------|
| **SHA-256 tamper-evident audit** | `app.security.hashing` + `AuditService.log_event`; auth logs LOGIN/USER_CREATE |
| **AES-256 encryption** | `app.security.encryption` (PyCryptodome); audit metadata encrypted |
| **Audit chain verification** | `GET /api/audit/verify` uses `AuditService.verify_integrity` (100% tamper detection) |
| **pgvector cosine similarity** | `Embedding.vector` uses pgvector `Vector(512)`; `EmbeddingService.search_similar` for sub-100ms retrieval |
| **Adversarial robustness (ART)** | `app.security.adversarial_eval`; optional ART for FGSM/PGD; `GET /api/security/robustness` |

To run adversarial evaluation (FGSM, PGD):  
`pip install adversarial-robustness-toolbox[torch]` then `python -m app.security.adversarial_eval`.

### Camera feeds and detection

Detection is wired to the **multi-camera pipeline**: when you start detection with a source, the backend adds that camera to the pipeline and runs YOLO + ByteTrack on it. Results are real (not mock).

- **Dashboard → Detection:** Use “Add camera & start detection”: set **Source** to `0` for default webcam, or a path like `C:/videos/test.mp4` (file), or an RTSP URL; pick **Type** (webcam/file/rtsp), then **Start detection**. The pipeline starts and the page shows live FPS and person counts per camera.
- **API:** `POST /api/detection/start/{camera_id}?source=0&stream_type=webcam` adds the feed and starts the pipeline; `GET /api/detection/results/{camera_id}` returns the latest detections from the pipeline.

---

## License & attribution

FYP project — BS Cybersecurity. See project proposal for full abstract and references.
