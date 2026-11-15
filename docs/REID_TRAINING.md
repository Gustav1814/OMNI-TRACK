# Training the Re-ID model on the demo PC (RTX 3060 Ti)

This guide fine-tunes OmniTrack's person Re-ID model (OSNet-AIN x1.0, starting from the
MSMT17 weights the app ships with) on Market-1501 and CAVIAR shopping-centre crops.
The whole job takes about 15–25 minutes on an RTX 3060 Ti. On a CPU-only machine it takes
hours, so run it on the GPU PC.

The script is `backend/scripts/train_reid.py`. It picks CUDA automatically when it is available.

---

## 1. Check the GPU build of PyTorch

PyTorch from PyPI on Windows is CPU-only. Run this from `backend\`:

```powershell
.venv\Scripts\python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')"
```

It must print `True` and `NVIDIA GeForce RTX 3060 Ti`. If it prints `False`, install the CUDA wheels:

```powershell
.venv\Scripts\pip install --force-reinstall torch==2.15.0 torchvision==0.29.2 --index-url https://download.pytorch.org/whl/cu132
```

## 2. Copy the training data

Datasets live in `backend/storage/`, which is not in git, so copy them from the dev machine.
Keep the same paths under `backend\storage\datasets\` on the PC:

| Folder | Size | What it is |
|---|---|---|
| `market1501/Market-1501-v15.09.15/` | ~143 MB | Needs `bounding_box_train`, `bounding_box_test` and `query` |
| `caviar_reid_crops/` | ~98 MB | Pre-cut CAVIAR person crops plus `index.json` |

Also make sure the starting weights are present:

```powershell
.venv\Scripts\python scripts\download_default_weights.py osnet_ain_x1_0_msmt17.pth
```

> If `caviar_reid_crops\index.json` is missing, the script rebuilds the crops from the CAVIAR
> videos in `backend\storage\demo_videos\caviar\` (~580 MB). Copying the crop folder is faster.

## 3. Train

From `backend\`:

```powershell
.venv\Scripts\python scripts\train_reid.py --epochs 30 --p 16 --k 4
```

What happens:

1. **Before score:** the stock MSMT17 model is scored on Market-1501 and on the held-out CAVIAR clips.
2. **Training:** 30 epochs. Each batch is 16 people × 4 images. One line is printed every 20 steps, and one per epoch.
3. **After score:** the trained model is scored the same way.

Outputs:

| File | Content |
|---|---|
| `backend\model_weight\osnet_ain_x1_0_omnitrack.pth` | The trained weights |
| `eval\results\reid_finetune.json` | Before/after scores and the training settings |

A partial checkpoint is saved after each epoch as `osnet_ain_x1_0_omnitrack.pth.part`. If the run
is interrupted, the final file is not written, so the app can never pick up a half-trained model.

Useful flags:

| Flag | Use |
|---|---|
| `--before none` | Skip the before score (saves a few minutes; you lose the comparison) |
| `--max-steps 20` | Quick smoke test: stops after 20 steps |
| `--eval-only model_weight\osnet_ain_x1_0_omnitrack.pth` | Re-score an existing file without training |
| `--no-caviar` | Train on Market-1501 only |
| `--epochs 15` | Shorter run if you are short on time |

If you get `CUDA out of memory`, close other GPU apps or use `--p 12 --k 4`.

## 4. Decide whether to use it

Open `eval\results\reid_finetune.json` and compare `before.caviar_holdout` with `after.caviar_holdout`.
The CAVIAR hold-out sequences are the cross-camera demo clips and were never trained on, so they are
the honest test.

| Field | Meaning | Better if |
|---|---|---|
| `auc` | How well same-person pairs separate from different-person pairs | higher |
| `tar_at_1pct_far` | Share of same-person pairs matched when only 1% of different-person pairs are falsely matched | higher |
| `thr_at_1pct_far` | The similarity score that gives that 1% false-match rate | used for tuning below |

Use the new model only if **both** `auc` and `tar_at_1pct_far` go up on CAVIAR.

Market-1501 `rank1` and `mAP` will rise a lot, because training uses Market's own training split.
That shows training worked, but it is not proof the model works better in the store.

## 5. Switch the app to the new model

In `backend\.env`:

```ini
REID_WEIGHTS_PATH=osnet_ain_x1_0_omnitrack.pth
```

Then retune the match threshold, because a fine-tuned model produces different similarity scores.
Take the change in `thr_at_1pct_far` from before to after, and move the two thresholds by the same amount.
For example, if it went from 0.62 to 0.55 (a drop of 0.07), set:

```ini
REID_T_MATCH=0.68
REID_SIMILARITY_THRESHOLD=0.68
```

(The default for both is 0.75.)

Restart the backend. Identities saved with the old model are not comparable with the new one, so clear
the Re-ID gallery before the demo by starting from a fresh database or wiping the embeddings.

To go back, set `REID_WEIGHTS_PATH=osnet_ain_x1_0_msmt17.pth` and restore the old thresholds.

## 6. Keep a copy

`osnet_ain_x1_0_omnitrack.pth` is a locally trained file with no public download.
`download_default_weights.py` will not fetch it. Back it up yourself, or publish it as a release
asset next to `fire-smoke.pt` (see `CUSTOM` in that script) along with its sha256:

```powershell
certutil -hashfile model_weight\osnet_ain_x1_0_omnitrack.pth SHA256
```

## Calibrate thresholds on the PC

The dev laptop is CPU-only and too slow for these runs, so they were cut short there. Run them on the
GPU PC, from `backend\`, with `DEVICE=auto` (or `cuda`) in `.env`. They need the CAVIAR clips under
`backend\storage\demo_videos\caviar\` (copy them as in step 2). None of them train anything.

### Golden clip (detector + tracker regression)

```powershell
.venv\Scripts\python -m pytest -m slow tests\test_core_golden_clip.py -s
```

It uses the model and image size picked for the device (on CUDA, `PERSON_MODEL_GPU` at
`PERSON_IMGSZ_GPU`). Laptop result for yolo11s@640 on CPU: 9 GT people, 9 confirmed tracks,
1 ID switch, recall 0.756. The bounds are ID switches ≤ 3, confirmed tracks ≤ 12, recall ≥ 0.68.
The bounds were set on CPU, so if the GPU model fails them, look at the printed numbers before
changing anything.

### Detector accuracy and speed per backend

```powershell
.venv\Scripts\python scripts\eval_core.py detector --model yolo11m.pt --imgsz 960 --export none
.venv\Scripts\python scripts\eval_core.py detector --model yolo11m.pt --imgsz 960 --export engine
.venv\Scripts\python scripts\eval_core.py detector --model yolo11s.pt --imgsz 640 --export none
```

`engine` needs TensorRT (`pip install tensorrt`). Without it the detector falls back to PyTorch, and
`backend:` in the output says which backend actually ran. Keep `DETECTOR_EXPORT=auto` if the engine
gets the same AP50 (±0.005) at lower latency. Otherwise set `DETECTOR_EXPORT=none`. For reference,
the laptop CPU measured yolo11s@640: AP50 0.665 for both backends, 192 ms (PyTorch) vs 91 ms (ONNX).

### Re-ID thresholds

```powershell
.venv\Scripts\python scripts\eval_core.py reid
```

This embeds every ground-truth person crop in CAVIAR (about 13,000 crops) with the configured model.
It prints the pairwise thresholds at 1 % and 0.1 % FAR, then an open-set table with one row per
threshold. For each threshold, the table shows what happens to a returning person (correct, wrong ID,
or split into a new ID) and how often a new person gets merged into an old ID.

The laptop finished only an 8-clip subset (52 people, 632 tracklets) with `osnet_ain_x1_0`:

| T    | correct | wrong ID | split | new person merged |
|------|---------|----------|-------|-------------------|
| 0.55 | 0.903   | 0.097    | 0.000 | 0.951             |
| 0.70 | 0.900   | 0.087    | 0.013 | 0.290             |
| 0.75 | 0.877   | 0.074    | 0.049 | 0.177             |
| 0.80 | 0.817   | 0.049    | 0.134 | 0.130             |
| 0.85 | 0.651   | 0.036    | 0.313 | 0.060             |

The current settings come from this table. `REID_T_MATCH=0.75` merges far fewer new people than 0.55
and keeps splits around 5 %. `REID_T_UPDATE=0.85` adds views to the gallery only when a wrong ID is
unlikely. The full-set run decides whether these settings hold. Set them in `backend\.env`:

```
REID_T_MATCH=0.75
REID_T_UPDATE=0.85
REID_SIMILARITY_THRESHOLD=0.75
```

`REID_SIMILARITY_THRESHOLD` is the search-page cutoff, so keep it equal to `REID_T_MATCH`.
Restart the backend after changing them.

To compare architectures (the laptop timed out on these), download the MSMT17 weights for
`osnet_x1_0` and `osnet_ibn_x1_0` from https://huggingface.co/kaiyangzhou/osnet into `model_weight\`,
then run:

```powershell
.venv\Scripts\python scripts\eval_core.py reid --arch osnet_x1_0 --weights <osnet_x1_0 msmt17 file>.pth
.venv\Scripts\python scripts\eval_core.py reid --arch osnet_ibn_x1_0 --weights <osnet_ibn_x1_0 msmt17 file>.pth
```

Switch `REID_MODEL` and `REID_WEIGHTS_PATH` only if another model clearly beats AIN on correct
matches and new-person merges at the same T. After a fine-tune (step 3), run the same command with
`--weights osnet_ain_x1_0_omnitrack.pth` and recalibrate.

### Live FPS

FPS with the current worker, the ONNX/TensorRT detector and non-blocking Re-ID was never measured on the
laptop. Run it on the PC:

1. Start the backend.
2. Add two cameras in the UI, or call `POST /api/pipeline/cameras/add` with `stream_type=file`,
   `loop=true` and a CAVIAR `.mpg` as `source`.
3. Let them run for 2 minutes.
4. Read `GET /api/pipeline/health`.

That endpoint reports FPS per camera, the stage latencies (detect, queue, embed) and the worker's
queue depth. For comparison, the laptop with the old worker and the PyTorch detector reached about
5–6 fps across two cameras.
