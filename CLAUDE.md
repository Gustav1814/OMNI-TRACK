# OmniTrack AI — notes for Claude Code

Retail store analytics from CCTV-style video. It covers person detection and tracking, global
cross-camera Re-ID, zones, queues, dwell, fire and smoke, emotion and demographics, alerts with
evidence clips, and video synopsis. This is a final-year project, and the demo is recorded on the
Windows PC described below.

## Machines

| Machine | Spec | Use |
|---|---|---|
| Dev box (Linux) | CPU only, ~4–7 GB free RAM | Coding, tests, light evals. Never train here. |
| Demo PC (Windows) | Ryzen 5 5600, 16 GB RAM, RTX 3060 Ti 8 GB | Re-ID training, GPU benchmarks, recording the demo |

`DEVICE=auto` picks CUDA when it is present. Keep jobs memory-light on the dev box.

## Ground rules (from the owner)

- **No fake data, ever.** Return no data with `"status": "no_data"` so the UI shows its empty states.
  No random fallbacks, no mock AI (`ALLOW_MOCK_AI=false`), no placeholder numbers in the UI.
- Docker runs **only** Postgres (pgvector) and Redis. Backend and frontend run from the terminal.
- **Commit and push only when asked.**
- Leave the `users` table empty after tests. Delete any temp users or rows you create.
- Accuracy matters most, especially global cross-camera Re-ID and detection accuracy.
- Never print secrets. `backend/.env` is git-ignored.

## Stack and layout

- `backend/`: FastAPI with async SQLAlchemy and Alembic, ultralytics YOLO, ByteTrack, torchreid OSNet-AIN, and pgvector.
  - `app/ai/`: models.
  - `app/services/pipeline.py`: the per-camera frame loop.
  - `app/analytics/`: the zone engine.
  - `app/routers/`: the API.
  - `app/security/`: JWT, AES-GCM and the HMAC audit chain.
- `frontend/`: React with Vite and the "Pearl" theme (light and dark).
- Alembic chain: `001 → 002 → 003 → 004_security → 004_zones → 004_reid → 005_alerts_evidence → 006_calibration_points`.
- The small weights (`yolov8n`, `yolo11n`, `yolo26n`, `general_pe_n_11_pt`, `face11n`, `fire-smoke`, `general_product_detection`) are in git.
  The rest (`yolo11s`, `yolo11m`, OSNet, emotion, FairFace) are not. `backend/scripts/download_default_weights.py` fetches them and checks every sha256.
- Datasets and demo footage live in `backend/storage/`, which is not in git, so copy them from the dev box.
  - Demo clips and their sources: `backend/storage/demo_videos/SOURCES.md`.

## Run on the demo PC (PowerShell)

```powershell
.\scripts\start-infra.ps1                     # postgres + redis in Docker
cd backend
.venv\Scripts\python scripts\generate_secrets.py   # first time only; writes backend\.env
.venv\Scripts\python scripts\download_default_weights.py
.venv\Scripts\python scripts\create_admin.py <user> <email>   # prompts for the password
.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown 3
# second terminal
cd frontend; npm ci; npm run dev               # http://localhost:5173
```

The CUDA build of torch must be installed. See the header of `backend/requirements.txt` and check with
`python -c "import torch; print(torch.cuda.is_available())"`.

Tests: `cd backend; .venv\Scripts\python -m pytest -m "not slow"` and `cd frontend; npm test`.

## Key docs

- `docs/REMEDIATION_PLAN.md`: the master plan (Phases 1–6). Ticked items are done.
- `docs/REID_TRAINING.md`: **how to fine-tune Re-ID on the GPU, step by step.**
- `docs/EVALUATION.md` and `eval/results/*.json`: measured accuracy and speed. Quote only numbers from here.
- `docs/SECURITY.md`: auth, encryption, audit chain, secret rotation.
- `README.md`: full setup, API overview, Re-ID tuning notes.

## Status (end of the dev-box session, Nov 2026)

Done on the dev box:
- Detection/tracking/Re-ID fixes.
- Calibrated Re-ID gating:
  - tracklet top-K averaging
  - crop quality filters
  - camera topology / transit windows
  - Hungarian matching
  - pgvector persistence
- Security hardening: secrets, lockout, rate limits, HMAC audit, SSRF/path checks.
- Video synopsis.
- The no-fake-data cleanup.
- Alerts centre (`/api/alerts`) and encrypted evidence snapshots/clips (`/api/evidence`), with Alerts and Captures pages.
- Wider CSV/JSON export.
- Dwell-overrun alert.
- Focus view.
- Real demographics (FairFace ONNX, minimum-sample rule). Pipeline evidence hooks and demographics were
  still being verified when this was written; check `docs/REMEDIATION_PLAN.md` ticks.
- Restricted-area zones (intrusion alerts with evidence), entry-line counts, queue wait, floor heatmap.
- Person thumbnails and "Find similar people" (`/api/reid/thumb/{gid}`).
- Floor calibration UI (Setup → Zones → Floor calibration): clicked point pairs, fit error, migration 006.
- Fewer pages: hub pages, no duplicates. Alert false-positive fixes (dwell overrun, fire overlay gate).
- Golden-clip regression test passes.
- `backend/scripts/train_reid.py`, ready but not run. It is too slow on the dev-box CPU.

## To do on the demo PC

1. **Train Re-ID on the GPU.** Follow `docs/REID_TRAINING.md`: copy `storage/datasets/market1501` and
   `storage/datasets/caviar_reid_crops`, then run `scripts/train_reid.py --epochs 30 --p 16 --k 4`.
   Switch `REID_WEIGHTS_PATH` only if the CAVIAR hold-out `auc` and `tar_at_1pct_far` both improve,
   then retune `REID_T_MATCH` as that doc explains.
2. **Run all evaluations and make the report figures on the GPU.** Run `eval/run_all_pc.ps1` from the
   repo root. It runs every eval in `eval/` (detection, tracking, Re-ID, crowd, emotion, demographics, fire,
   synopsis, search latency, throughput, adversarial), then `eval/report_figures.py`. Results go to
   `eval/results/*.json`, figures to `docs/report/figures/`, and captions and sources to
   `docs/report/FIGURES.md`. Then update `docs/EVALUATION.md`. Numbers measured on the dev box are CPU-only.
   They were left unfinished on purpose, because evals are meant to run on this PC.
3. **Database and secrets:** run `alembic upgrade head`, then `generate_secrets.py` on this PC. If the audit chain reports legacy breaks, run
   `python -m app.security.audit_rebase --apply`.
4. **End-to-end demo check** with the clips in `backend/storage/demo_videos/`:
   - CAVIAR two-view sequences for cross-camera Re-ID. The hold-out sequences are listed in `train_reid.py`.
   - Store, shelf, crowd, faces and fire clips (see `SOURCES.md`).
5. **Owner actions, not code:**
   - Rotate the secrets that were once committed to git (old JWT/AES defaults, the `omnitrack_secret` DB
     password, the credentials on the old `stage1` branch). Delete `stage1`.
   - Optional: publish the `weights-v1` GitHub release (`fire-smoke.pt`, `general_product_detection.pt`). Not needed while they are in git.
6. **Floor calibration (plan item 105):** in Setup → Zones → Floor calibration, click 5+ measured floor
   points on each overlapping demo camera (one shared origin), then measure the double-count rate with and
   without fusion and record it in `docs/EVALUATION.md`.
7. **Known gaps:**
   - No real ceiling-CCTV shop clip.
   - No indoor fire clip with visible flames.
   - This machine's OpenCV build may lack H.264, so evidence clips can fall back to `.webm`.
   - Stock OSNet confuses people on CAVIAR's small crops (same camera, different people sharing an ID).
     The GPU fine-tune (item 1) is the fix; do not hand-tune thresholds.
