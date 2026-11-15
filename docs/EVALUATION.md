# Evaluation

How every success criterion from the FYP-I proposal is measured, on which
data, and what was measured. The same numbers appear on the **Evaluation**
page of the dashboard (`/evaluation`), which reads them through
`GET /api/evaluation/summary`.

Rules the scripts follow:

- A criterion without a result shows **Not measured**. No script writes a
  placeholder or estimated number, and a run that fails or is skipped writes
  nothing.
- A missed target is recorded as measured and marked **Fail**.
- Each result JSON records the model file and its sha256, imgsz and
  thresholds, the hardware, the git commit (with a dirty flag), the frozen
  dataset manifest and its hash, the run time and the peak memory.

## Results

**Every number in this table is CPU-only.** It was measured on the development laptop
(Intel i5-1245U, 4 threads, no GPU) on 14 Nov 2026. These are not the demo configuration:
the demo PC (RTX 3060 Ti) uses `yolo11m.pt@960` for people, and the final numbers come from
[Run evaluations on the PC](#run-evaluations-on-the-pc). When that run finishes, it replaces
this table. Source files: `eval/results/*.json`.

| Criterion | Target | Measured (CPU, laptop) | Result | Dataset and notes |
| --- | --- | --- | --- | --- |
| Person detection, AP@0.5 | > 0.85 | 0.8512 (`yolo11s@960`) | Pass (by 0.001) | 487 CAVIAR frames, 1,169 GT boxes. `yolo11s@640` 0.8462, `yolov8n@640` 0.8288. 316 / 146 / 59 ms per frame on CPU. The GPU config `yolo11m@960` is not measured yet. |
| Re-ID, Rank-1 on Market-1501 | > 0.70 | 0.6992 | Fail (by 0.001) | 3,368 queries, 15,913 gallery images. Rank-5 0.8403, Rank-10 0.8839, mAP 0.4328. Cross-domain: MSMT17 weights, never trained on Market-1501. 179 ms per crop on CPU. |
| Search latency, p95 (top-10 over 100k) | < 100 ms | 2.92 ms | Pass | pgvector HNSW. p50 1.99 ms, p99 3.51 ms. Recall@10 against exact search is 0.9654. |
| Crowd level accuracy | > 0.85 | 0.144 | Fail | Mall, every 4th frame (500 frames). Count MAE 16.26 with bias −16.26: the detector misses most people in this dense, high-angle, low-resolution scene. At `@640` the level accuracy is 0.018 and the MAE 20.68. |
| Emotion, FER-2013 accuracy | > 0.70 | 0.5599 | Fail | All 7,178 test faces. Strict 8-way 0.5532, macro recall 0.5232. Cross-dataset: the model was trained on AffectNet. |
| Audit tamper detection | 1.0 | 1.0 | Pass | Synthetic 1,000-entry chain; 100 trials for each of 7 attack types. |

The per-class recall and the confusion matrix for emotion are in `docs/report/FIGURES.md`.

## Run evaluations on the PC

All evaluations and the report figures are run on the demo PC (RTX 3060 Ti, Ryzen 5 5600,
16 GB). Do not run them on the dev laptop. One command, from the repo root in PowerShell:

```powershell
.\scripts\start-infra.ps1                                  # Postgres + Redis (search_latency needs pgvector)
powershell -ExecutionPolicy Bypass -File eval\run_all_pc.ps1 -Install   # first time: installs eval\requirements.txt
powershell -ExecutionPolicy Bypass -File eval\run_all_pc.ps1            # later runs
```

`eval\run_all_pc.ps1`:
- uses `backend\.venv\Scripts\python.exe`;
- checks that CUDA is visible, and warns and pauses if it is not;
- runs the steps below, cheapest first;
- logs each step to `eval\results\logs\<step>.log` and writes `eval\results\<id>.json`;
- writes a summary to `eval\results\logs\summary.txt`.

A step whose data or service is missing is skipped and named in the summary, and it writes
no result. A failed step does not stop the run. Use `-Only reid,caviar`, `-Skip throughput`
or `-Threads 6` to change what runs.

The times below are rough estimates, not measurements. The paths are under
`backend\storage\`, which is not in git: copy it from the dev box, or fetch the public sets
with `python eval\fetch_datasets.py fer2013 market1501 dfire`.

| # | Step | Script | Time (GPU) | Needs |
| --- | --- | --- | --- | --- |
| 1 | detect | `eval/detect.py` | 3–6 min | `demo_videos\caviar` (videos and GT xml); the weights `yolo11m.pt`, `yolo11s.pt` and `yolov8n.pt` |
| 2 | track | `eval/track.py` | 5–10 min | `demo_videos\caviar`; TrackEval |
| 3 | reid | `eval/reid.py` | 2–4 min | `datasets\market1501` |
| 4 | caviar | `eval/reid_caviar.py` | 2–5 min | `datasets\caviar_reid_crops`, or `demo_videos\caviar` to build it. If `osnet_ain_x1_0_omnitrack.pth` exists, it measures the shipped weights and the fine-tuned ones. |
| 5 | crowd | `eval/crowd.py` | 2–4 min | `demo_videos\mall\mall_dataset` |
| 6 | emotion | `eval/emotion.py` | 1–3 min | `datasets\fer2013` |
| 7 | demographics | `eval/demographics.py` | 2–5 min | `datasets\fairface\validation-0.25.parquet` (copy it from the dev box) |
| 8 | fire | `eval/fire.py` | 8–15 min | `datasets\dfire`, plus `demo_videos\caviar` as no-fire footage |
| 9 | synopsis | `eval/synopsis.py` | 10–20 min | `demo_videos\caviar` (about 24 min of corridor video) |
| 10 | search_latency | `eval/search_latency.py` | 5–10 min | Postgres with pgvector on port 5432, and `backend\.env` |
| 11 | throughput | `eval/throughput.py` | about 3 min | The backend running on `http://localhost:8000` with 4 file cameras (720p clips) streaming. Otherwise this step is skipped: start the cameras, then run `-Only throughput,report_figures`. |
| 12 | adversarial | `eval/adversarial.py --images 50` | 3–8 min | `datasets\coco_val2017_person` (downloads the 500-image COCO subset if it is missing); ART |
| 13 | report_figures | `eval/report_figures.py` | 2–4 min | The results above. Writes `docs\report\figures\*.png` and `docs\report\FIGURES.md` |

After the run, replace the table in [Results](#results) with the new numbers and remove
the "CPU-only" label. `report_figures.py` is the only step that draws figures. It leaves out
any metric that has no result file, and it lists those in "Not generated yet" in
`FIGURES.md`.

Re-ID fine-tuning (`backend/scripts/train_reid.py`, see `docs/REID_TRAINING.md`) is
separate and runs first. It writes `eval/results/reid_finetune.json`, which
`report_figures.py` turns into a before-and-after chart.

## How to run single scripts

```bash
cd OmniTrack
uv pip install --python backend/.venv/bin/python -r eval/requirements.txt   # TrackEval + ART, eval only

python eval/fetch_datasets.py fer2013      # ~39 MB
python eval/fetch_datasets.py market1501   # ~110 MB
python eval/fetch_datasets.py dfire        # ~200 MB (frozen 1-in-3 subset of the test split)

export OMNITRACK_ML_LOCK=/tmp/omnitrack-ml.lock   # serialises heavy jobs on a small machine
backend/.venv/bin/python eval/detect.py
backend/.venv/bin/python eval/track.py
backend/.venv/bin/python eval/reid.py
backend/.venv/bin/python eval/reid_caviar.py      # Re-ID on held-out CAVIAR store sequences
backend/.venv/bin/python eval/search_latency.py   # needs Postgres (reads settings.DATABASE_URL)
backend/.venv/bin/python eval/fire.py
backend/.venv/bin/python eval/crowd.py
backend/.venv/bin/python eval/emotion.py
backend/.venv/bin/python eval/demographics.py     # FairFace validation parquet
backend/.venv/bin/python eval/synopsis.py
backend/.venv/bin/python eval/audit_chain.py
backend/.venv/bin/python eval/adversarial.py
backend/.venv/bin/python eval/checkout.py          # needs eval/annotations/checkout/*.json
backend/.venv/bin/python eval/throughput.py --url http://localhost:8000   # backend running, 4 cameras live
backend/.venv/bin/python eval/report_figures.py   # charts + qualitative panels -> docs/report/
```

Every script writes `eval/results/<id>.json`; the targets live only in
`eval/criteria.json`. Datasets go to `backend/storage/datasets/<name>/`
(git-ignored); every downloaded file is sha256-checked against a pinned
Hugging Face revision.

Each script freezes its sample on first run into `eval/manifests/<name>.json`
(the list of items plus an `items_sha256`). Later runs reuse it and refuse a
manifest that was edited by hand; `--refreeze` builds a new one. Commit the
manifests, not the images.

Pure helpers (AP, IoU, manifest freezing, the fire k-of-n rule, Re-ID
ranking) are unit-tested in `backend/tests/test_eval_helpers.py`.

## Method per criterion

| Criterion | Script | Method |
| --- | --- | --- |
| Person detection mAP@0.5 > 85% | `eval/detect.py` | Every 150th frame of both CAVIAR views of all 26 sequences (487 frames, 1,169 GT boxes, empty frames kept). The detector runs as the pipeline does (person class, NMS IoU 0.6) at conf 0.001 for the full PR curve; COCO 101-point AP. GT boxes under 24 px tall are ignore regions (Caltech/CityPersons protocol). Each shipped configuration is measured: `yolo11m.pt@960` (GPU) and `yolo11s.pt@640` (CPU), plus `yolo11s@960` and the old `yolov8n@640` baseline. |
| Tracking (no target) | `eval/track.py` | TrackEval HOTA / CLEAR / Identity on a frozen set of CAVIAR sequences (both views). Detector plus the app's own `PersonTracker`; only confirmed tracks are scored, because those are what the app shows and counts. Same ignore rule as detection. The `phase1` preset (shipped CPU config) is compared with the pre-remediation `baseline` preset. |
| Re-ID Rank-1 > 70% | `eval/reid.py` | Market-1501 standard protocol: 3,368 queries against 15,913 gallery images, same-camera same-ID gallery images excluded, no re-ranking or flip. The shipped extractor (`osnet_ain_x1_0`, MSMT17 weights, cosine distance) was never trained on Market-1501, so this is a cross-domain number. |
| Search < 100 ms | `eval/search_latency.py` | 100,000 L2-normalised 512-d embeddings (identity centres plus noisy views) in a session-private TEMP table with the app's HNSW cosine index; 1,000 top-10 queries with the app's query shape. Latency is client-side wall clock including the round trip. Recall@10 is checked against exact search. App tables are not touched. |
| Fire precision > 90%, FPR < 5% | `eval/fire.py` | D-Fire test split (frozen 1-in-3 subset): image-level precision, recall and FPR at the shipped confidence 0.4, plus box AP@0.5 per class. CAVIAR footage (no fire) is scanned at the pipeline's fire cadence to give false alarms per hour for the instant rule and for the k-of-n confirmation rule. |
| Crowd level accuracy > 85% | `eval/crowd.py` | Mall dataset frames (every head annotated inside the ROI). The detector counts people whose head point falls in the ROI; levels come from the app's `CrowdDensityEstimator`. Reports level accuracy, count MAE / RMSE and bias. |
| Checkout queue ±1 | `eval/checkout.py` | Hand-annotated clips (`eval/annotations/checkout/*.json`: video, lane box, people in the lane per second). Detector, tracker and the app's `CheckoutAnalytics` run as in the pipeline; the result is the MAE against the annotations. |
| Emotion > 70% on FER-2013 | `eval/emotion.py` | All 7,178 FER-2013 test faces through the app's own preprocessing and the shipped HSEmotion `enet_b0_8_best_afew.onnx` (AffectNet). Primary: argmax over the 7 shared classes. Strict: 8-way, where "contempt" counts as wrong. |
| Synopsis ≥ 10× | `eval/synopsis.py` | The app's `VideoSynopsis` on one long clip: by default all CAVIAR corridor-camera sequences joined (about 24 min, the longest same-camera footage available). Pass `--video` for a recording of 30 min or more. Reports the compression ratio and the share of activity tubes kept. |
| Audit tamper detection 100% | `eval/audit_chain.py` | A 1,000-entry chain built with `app.security.audit_chain`. 100 trials each of mutate, forge with a guessed key, delete, insert, swap, truncate and downgrade-to-legacy. The clean chain must verify. |
| 20+ FPS (4 × 720p) | `eval/throughput.py` | Run on the demo machine with the backend streaming 4 cameras. Polls `GET /api/pipeline/health` and takes the median processed FPS per camera over the window; the primary number is the slowest camera. It never starts cameras itself. |
| Re-ID in the store domain (no target) | `eval/reid_caviar.py` | The ground-truth person crops of the CAVIAR sequences that `train_reid.py` holds out, so they are never trained on. Pairs are taken within each sequence. A positive pair is the same identity at least 3 s apart; a negative pair is two different identities. Reports ROC AUC, TAR at 1% FAR, and the accept rates at `REID_T_MATCH`. CAVIAR ids are per view, so FAR is slightly pessimistic. |
| Demographics (no target) | `eval/demographics.py` | FairFace validation split (0.25 padding). Reports gender accuracy, age band (exact and ±1), per-race-group accuracy and the largest gap, a 32 px low-resolution subset, and latency per face. |
| Adversarial robustness (no target) | `eval/adversarial.py` | COCO val2017 images with people (official annotations, the first 50 by id), run through `app.security.adversarial_eval.run_detector_robustness_eval`: white-box ART FGSM and PGD at L∞ 8/255 with 10 PGD steps, with and without JPEG and median-filter defences. The primary number is PGD AP@0.5 divided by clean AP@0.5. A run that did not complete is not recorded. |

## Datasets

| Dataset | Used for | Source | Licence |
| --- | --- | --- | --- |
| CAVIAR (INRIA / shopping centre, 26 two-view sequences with GT) | detection, tracking, Re-ID (hold-out crops), fire negatives, synopsis | https://homepages.inf.ed.ac.uk/rbf/CAVIARDATA1/ | Free for research with the acknowledgement "EC Funded CAVIAR project/IST 2001 37540" |
| Mall dataset (Chen et al., BMVC 2012) | crowd | https://personal.ie.cuhk.edu.hk/~ccloy/downloads_mall_dataset.html | Research use, cite the paper |
| Market-1501 (Zheng et al., ICCV 2015) | Re-ID | fetched by `fetch_datasets.py` (pinned revision) | Research only, do not redistribute |
| FER-2013 (ICML 2013 challenge) | emotion | fetched by `fetch_datasets.py` (pinned revision) | Research use |
| D-Fire (Venâncio et al., 2022) | fire | fetched by `fetch_datasets.py` (pinned revision) | CC0 per the authors' repository |
| FairFace validation, 0.25 padding (Kärkkäinen & Joo, 2021) | demographics | pinned parquet in `backend/storage/datasets/fairface/` | CC BY 4.0 |
| COCO val2017, person subset (Lin et al., 2014) | adversarial, qualitative detection figure | `LibreYOLO/coco-val2017-mini500` on Hugging Face, downloaded by `app.security.adversarial_eval` | Annotations CC BY 4.0; images under Flickr terms |
| Reference clips (Pexels) | demos, checkout once annotated | `backend/storage/demo_videos/reference/SOURCES.md` | Pexels License |

CAVIAR and Mall are kept under `backend/storage/demo_videos/`, and the
fetched datasets under `backend/storage/datasets/`. None of them are in git.

## Not yet measured

These come only from the PC run. None of them have a result file yet:

- **Tracking** (`tracking.json`): HOTA, MOTA, IDF1 and ID switches on CAVIAR.
- **Fire** (`fire.json`): D-Fire precision, recall and FPR, and false alarms per hour on CAVIAR.
- **Demographics** (`demographics.json`): FairFace accuracy and per-group gaps.
- **Synopsis** (`synopsis.json`): the compression ratio on about 24 min of CAVIAR footage.
- **Adversarial** (`adversarial.json`): FGSM and PGD retention on COCO.
- **Re-ID in the store domain** (`reid_caviar.json`): the CAVIAR hold-out AUC and TAR.
- **Re-ID fine-tuning** (`reid_finetune.json`): written by `train_reid.py` after GPU training.
- **Throughput** (`throughput.json`): needs the live app with 4 cameras.
- **GPU numbers for detection, Re-ID, crowd, emotion and search.** The laptop numbers above are CPU-only.
- **Checkout queue ±1** (`checkout.json`): needs hand-annotated checkout clips in
  `eval/annotations/checkout/`. None exist yet, so this criterion stays "Not measured".
