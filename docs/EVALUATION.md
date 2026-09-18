# OmniTrack AI — Evaluation & Proposal Criteria

How the project is measured against the FYP proposal's evaluation table, what is
measured today, and exactly what remains.

---

## 1. Scoreboard

The proposal defines eleven success criteria. Five are now measured with real
numbers; six still need data that has to be obtained or annotated.

| # | Criterion | Target | Status | Measured |
|---|---|---|---|---|
| 1 | Search latency | < 100ms | ✅ **PASS** | 2.49ms p95 |
| 2 | Integrity verification | 100% tamper detection | ✅ **PASS** | 100% (6/6 mutations, 0 false positives) |
| 3 | Throughput | 20+ FPS | ❌ **FAIL** | 19.65 FPS (CPU-only) |
| 4 | Video synopsis | ≥ 10× compression | ⚠️ **WEAK PASS** | 10.0× — see §5.1 |
| 5 | Adversarial robustness | documented resilience | ✅ **DOCUMENTED** | PGD retention 0.00, FGSM 0.75 |
| 6 | Detection accuracy | > 85% mAP | ⬜ Tier B | — |
| 7 | Re-ID accuracy | > 70% Rank-1 | ⬜ Tier B | — |
| 8 | Emotion recognition | > 70% accuracy | ⬜ Tier B | — |
| 9 | Fire/smoke detection | > 90% precision, < 5% FPR | ⬜ Tier C | — |
| 10 | Crowd classification | > 85% accuracy | ⬜ Tier C | — |
| 11 | Checkout metrics | queue length ± 1 person | ⬜ Tier C | — |

Regenerate rows 1–5 at any time:

```bash
cd backend && ../.venv/Scripts/python.exe scripts/benchmark.py --all
```

Results land in `backend/benchmarks/results/` — a timestamped JSON per run plus
`LATEST.md`. Exit code is 0 when everything that ran met its target.

### Why the criteria split into tiers

The dividing question is **where the ground truth comes from**, because accuracy
without ground truth is not a measurement — comparing a model against its own
confidence scores tells you how sure it was, not whether it was right.

- **Tier A (rows 1–5)** — no ground truth needed. Each measures the system's own
  behaviour: how fast a query returns, how many frames per second, whether the
  verifier catches mutations we ourselves introduced, how much of the detector's
  own output survives an attack.
- **Tier B (rows 6–8)** — ground truth ships with a public dataset. Download,
  run, record. No annotation, but see the caveat in §4.1.
- **Tier C (rows 9–11)** — ground truth must be produced by hand, because the
  models are custom or the definitions are project-specific.

---

## 2. What was implemented

### 2.1 Audit hash chain — two real defects fixed

The audit log is a SHA-256 chain: each row's `current_hash` covers its own fields
plus the previous row's hash, so altering history is supposed to be detectable.
`GET /api/audit/verify` was reporting `valid: false, broken_at: 23`. Two separate
bugs were behind that.

**Defect 1 — concurrent appends forked the chain.**

Appending was a read-then-write with no lock: read the tip's hash, then insert a
row pointing at it. Two requests in flight simultaneously both read the same tip
and both chained onto it. Found twice in 1948 production entries:

```
 22 | camera_removed | prev: a2b1e44a6a | 10:47:40.876136
 23 | camera_removed | prev: a2b1e44a6a | 10:47:40.882165   <- same predecessor, 6ms later
```

Fixed in `AuditService.log_event` with a transaction-scoped advisory lock
(`pg_advisory_xact_lock`) taken before reading the tip, which makes read-and-append
atomic against other transactions. Released automatically on commit or rollback,
and re-entrant, so several `log_event` calls inside one transaction still work.

*Verified:* 40 concurrent login requests (10 admitted, 30 rate-limited) produced
10 chained entries and **zero forks**, where the old code forked under far less
pressure.

**Defect 2 — the chain could not detect edits at all.**

`verify_integrity` only checked linkage — that each row's `previous_hash` matched
the preceding row's `current_hash`. It never recomputed the hash. Editing a
`description` in place left the linkage perfectly intact and went undetected, so
the "tamper-evident" property did not actually hold.

The reason it was written that way: `log_event` hashed
`datetime.now(timezone.utc)` while the `timestamp` column filled itself from
`server_default=func.now()`. Those are different values, so the hashed timestamp
was never stored and recomputation was impossible.

Both halves are fixed — `log_event` now persists the timestamp it hashes, and
`verify_integrity` performs two checks per entry:

1. **linkage** — catches inserted, deleted or reordered rows
2. **content** — recomputes the hash from stored fields; catches in-place edits

`AuditChainStatus` gained `break_reason` (`"linkage"` or `"content"`), `checked`
and `message`, so the endpoint says *why* a chain is broken rather than only
*where*.

**Consequence for existing data.** Entries written before this fix hashed a
timestamp that was never stored, so they can never be recomputed — every one of
the 1948 legacy rows failed content verification. A hash chain also cannot be
repaired: recomputing hashes to "fix" a break is exactly the rewrite the design
exists to make detectable. The chain was therefore reset — all 1948 rows copied
to `audit_logs_archive` (nothing deleted) and a fresh genesis started.

Maintenance CLI, `backend/scripts/audit_chain.py`:

```bash
../.venv/Scripts/python.exe scripts/audit_chain.py --verify   # linkage + content
../.venv/Scripts/python.exe scripts/audit_chain.py --forks    # list concurrent-append races
../.venv/Scripts/python.exe scripts/audit_chain.py --reset    # archive chain, start fresh
../.venv/Scripts/python.exe scripts/audit_chain.py --harden   # UNIQUE index on previous_hash
```

`--harden` has been applied: `previous_hash` now carries a UNIQUE index, so a
forked append fails with an `IntegrityError` instead of silently corrupting the
chain. Defence in depth behind the advisory lock.

### 2.2 Adversarial robustness — ART installed and made meaningful

`adversarial-robustness-toolbox` 1.20.1 is installed in `.venv` and pinned in
`requirements.txt`. The core package was used rather than the `[torch]` extra,
which would have re-resolved torch; the existing torch 2.14.0+cpu is untouched.

Installing it exposed a methodological problem in the existing code. FGSM and PGD
are white-box attacks — they need the gradient of the model's loss with respect to
input pixels. The previous implementation built a small **randomly-initialised**
surrogate CNN, attacked that, and measured YOLO on the result. An untrained
surrogate's gradients are unrelated to YOLO's, so the "adversarial" images were
little more than structured noise and any robustness figure would have been
meaningless.

`app/security/adversarial_eval.py` now wraps the real Ultralytics network in an
ART `PyTorchClassifier`. The wrapper reduces the detect head's
`[N, 4 + num_classes, anchors]` output to two logits — strongest person score and
strongest non-person score across all anchors — turning "is a person visible" into
a 2-class problem ART can attack. Gradients were confirmed to reach the input
tensor, so this is a genuine white-box attack on the weights being defended.

**Result** (8 frames, ε = 0.03 L-inf ≈ 8/255, PGD 10 steps, yolov8n):

| | persons detected | retention | mean confidence |
|---|---|---|---|
| clean | 8 / 8 | — | 0.79 |
| FGSM | 6 / 8 | 0.75 | 0.54 |
| PGD | **0 / 8** | **0.00** | — |

PGD at a standard budget **completely blinds the detector**. That is a strong
negative finding and it is the honest answer to the proposal's "documented
resilience" criterion — the system is *not* robust to white-box gradient attack,
which is the expected result for an undefended detector and exactly what the
criterion asks to be documented.

Adversarial patch is documented as future work: ART's `AdversarialPatch` needs an
optimisation loop over many images rather than the single forward/backward pass
FGSM and PGD use.

Endpoints: `GET /api/security/robustness` (status, last result),
`POST /api/security/robustness/run` (execute; accepts `sample_size`, `eps_fgsm`,
`eps_pgd`, `pgd_steps`, `image_dir`, `video_path`). CPU-bound and slow — roughly
30s for 8 images at 10 PGD steps.

### 2.3 Tier A benchmark harness

`backend/scripts/benchmark.py` measures the five criteria that need no labelled
data, writes timestamped JSON plus `LATEST.md`, and exits non-zero if anything
that ran missed its target. Benchmarks that *cannot* run (no embeddings, no clip,
ART absent) report `SKIP` rather than `FAIL`.

**Search latency** times the real `EmbeddingService.search_similar` path — not a
hand-written query — so the figure reflects what the API serves. 100 runs after 5
warm-up queries, probing with a vector drawn from the gallery. Reports p95 rather
than mean, since mean hides tail latency.

**Throughput** times `PersonDetector.detect` over real decoded frames, with decode
excluded, after a 5-frame warm-up.

**Tamper detection** is the methodologically interesting one. Ground truth is
*synthesised*: seed a known-good chain, apply mutations we chose, check the
verifier flags exactly those. Six mutations across both defence layers:

| Mutation | Layer | Detected |
|---|---|---|
| control (no tampering) | — | valid ✓ (no false positive) |
| edit description | content | ✓ |
| re-attribute to another user | content | ✓ |
| edit event_type | content | ✓ |
| backdate timestamp | content | ✓ |
| delete a middle entry | linkage | ✓ |
| repoint previous_hash | linkage | ✓ |

The **control row matters**. A verifier hard-wired to return "broken" would score
100% detection, so the harness also confirms an untampered chain verifies clean
and reports a false-positive rate. Detection rate alone is not evidence.

Everything runs inside a transaction that is rolled back — the live audit log is
never touched. A side finding: `audit_logs.user_id` has a foreign key to `users`,
so the database already refuses a forged user id pointing at nobody; the realistic
attack is re-attribution to a *real* user, which is what the harness tests.

### 2.4 Export endpoints

The export *service* (`app/services/export.py`) was already complete. What was
missing was the wiring. The two endpoints that existed lived in `main.py` and
returned a hardcoded list with a `# TODO: Pull real data from DB` comment — so
`/api/export/detections` returned **one invented row dated February 2026** while
the database held 123,431 real detections.

Replaced with `app/routers/export.py`. Nine endpoints, all querying the database,
all returning real file downloads (`Content-Disposition: attachment`, plus an
`X-Row-Count` header for scripted callers). All require authentication.

| Endpoint | Source table | Rows available |
|---|---|---|
| `/api/export/detections` | `detections` | 123,431 |
| `/api/export/line-counts` | `line_passing_counts` | 25,039 |
| `/api/export/roi-dwell` | `roi_dwell` | 3,505 |
| `/api/export/traffic` | `foot_traffic` | 125 |
| `/api/export/vibe` | `store_vibe_scores` | 105 |
| `/api/export/alerts` | `job_alerts` | 9 |
| `/api/export/journeys` | `customer_journeys` | 8 |
| `/api/export/demographics` | `demographic_snapshots` | 0 (empty until faces processed) |
| `/api/export/audit` | `audit_logs` | live chain |
| `/api/export/full` | bundle | JSON summary |

Common query parameters: `format` (csv/json), `start`, `end`, `camera_id`,
`limit` (default 10,000, hard cap 100,000).

Four new formatters were added to `ExportService` for the KPI tables that had
none: `line_passing_report`, `roi_dwell_report`, `alerts_report`,
`journeys_report`.

Two details worth knowing:

- **Line counts and ROI dwell are cumulative per track**, so the tables hold many
  rows per track as counters climb. `latest_per_track=true` (the default) applies
  `DISTINCT ON` to keep each track's final row — the correct shape for totals, and
  the same shape the live dashboard reads. Summing across *all* rows would badly
  double-count. Set it `false` to export full history when debugging a count.
- **Audit export omits encrypted metadata** — it is AES-encrypted at rest
  precisely so it does not leave the system in a spreadsheet. The chain is
  verified once per export and the verdict stamped on every row, so a reader can
  tell whether the file came from an intact chain.

There is deliberately **no checkout export**: no checkout table exists, the
metrics are computed live. Adding an endpoint that returns nothing would be the
same mistake as the sample-data stubs.

---

## 3. Running things

```bash
# Tier A benchmarks — all five
cd backend && ../.venv/Scripts/python.exe scripts/benchmark.py --all

# individually
../.venv/Scripts/python.exe scripts/benchmark.py --latency
../.venv/Scripts/python.exe scripts/benchmark.py --tamper
../.venv/Scripts/python.exe scripts/benchmark.py --throughput
../.venv/Scripts/python.exe scripts/benchmark.py --synopsis
../.venv/Scripts/python.exe scripts/benchmark.py --adversarial

# use a specific clip for throughput/synopsis/adversarial
../.venv/Scripts/python.exe scripts/benchmark.py --all --video "D:/path/to/clip.mp4"

# audit chain
../.venv/Scripts/python.exe scripts/audit_chain.py --verify

# adversarial eval standalone (full JSON)
../.venv/Scripts/python.exe -m app.security.adversarial_eval
```

Requires Postgres running (`docker compose up -d postgres redis`). The adversarial
and synopsis benchmarks need no database.

---

## 4. Covering the remaining six criteria

### 4.1 Tier B — ground truth ships with the dataset

No annotation. Download, run the standard evaluation, record the number.

**Detection accuracy (> 85% mAP)** — COCO val2017, ~1GB.

```python
from ultralytics import YOLO
YOLO("yolov8n.pt").val(data="coco.yaml")   # downloads val2017 on first run
```

Record `map50-95` and `map50`. Note yolov8n is the *nano* model; published
`map50-95` is ~37%, well under 85%. The 85% target is only reachable against
`map50` on a narrow class subset, or with a larger model. **Decide which number
the proposal means and state it explicitly** — this is the criterion most likely
to be challenged in a viva.

**Re-ID accuracy (> 70% Rank-1)** — Market-1501, ~1.5GB, requires manual download
(no automatic mirror). torchreid has the evaluation harness built in:

```python
import torchreid
datamanager = torchreid.data.ImageDataManager(root="reid-data", sources="market1501")
# build osnet_x0_25 to match app/ai/reid_backend.py, load weights, then:
engine.run(test_only=True)
```

Match the model to the one the pipeline actually uses (`osnet_x0_25`) or the
number will not describe this system.

**Emotion recognition (> 70%)** — FER-2013, available on Kaggle as a CSV of
48×48 grayscale faces. Feed the test split through `app/ai/emotion.py` and build
a confusion matrix. Two things to watch: DeepFace expects larger colour images, so
upscaling is needed; and `app/ai/emotion.py` deliberately rejects low-confidence
faces, which will show up as abstentions rather than errors — report coverage
alongside accuracy.

**The caveat that applies to all three.** These validate the *pretrained weights*
and largely reproduce published figures. They say nothing about performance on
retail CCTV, which is the actual claim of the project. Legitimate and standard —
but if asked "does that hold in a store?", the honest answer is that nobody knows.

### 4.2 Tier C — you must annotate

**Fire/smoke (> 90% precision, < 5% FPR)** — `fire-smoke.pt` is custom, so no
public benchmark corresponds to it. Frame-level binary labelling:

1. Sample ~150 frames from `fire_room.mp4` (see `videos for testing.txt`) and
   ~150 from fire-free retail clips — the negatives are what make FPR measurable.
2. Label each frame fire / no-fire. A keyboard-driven review script makes this
   about an hour.
3. Run `FireDetector` over the set, compute precision, recall and false-positive
   rate at the operating threshold.

Highest-value Tier C item: the clips exist, the target is specific, and one
properly annotated custom benchmark is worth more at a defence than three
reproduced public ones.

**Crowd classification (> 85%)** — "crowded vs uncrowded" is a project-specific
definition, so ground truth means a human judging zone occupancy per interval.
Write down the definition first (e.g. "≥ N people in zone for ≥ T seconds"),
otherwise annotators disagree with themselves.

**Checkout metrics (± 1 person)** — the proposal already prescribes the method:
*"manual counting on sample checkout footage"*. Count queue length by hand at
fixed intervals, compare against the estimator, report mean absolute error. Needs
footage that actually shows a checkout queue.

**The template already exists.** The line-passing validation — hand-counting the
clip as 15 in / 1 out, running the system, comparing — is exactly this
methodology, and it caught a real frame-dropping bug. Tier C is that, repeated.

---

## 5. Honest limitations

### 5.1 The synopsis criterion cannot fail

`compression_ratio` is reported as 10.0× against a ≥ 10× target, but
`synopsis.py:338` computes:

```python
synopsis_length = max(int(self.frame_count / self.compression_target), ...)
```

The output length is *derived from the target*, so the ratio is set by
configuration rather than measured. 240 frames → 24 frames is 10× by
construction. **This criterion cannot fail**, and presenting it as a pass without
that context would be misleading.

The engine was therefore extended to report `tubes_placed` and `event_retention`
— the fraction of extracted activity tubes that survived onto the condensed
timeline. Current measurement: **359 extracted, 359 placed, retention 1.00**, so
nothing was dropped. That is a genuine measurement.

Whether the surviving tubes are the *right* ones — whether a reviewer watching the
synopsis would see every event that mattered — still needs ground truth and
belongs in Tier C.

### 5.2 Throughput fails on CPU

19.65 FPS against a 20+ FPS target, with torch 2.14.0+cpu and no CUDA device. The
gap is the hardware, not the code — the same model on a modest GPU clears the
target several times over. Options, in order of honesty:

1. Re-run on a CUDA machine and report both figures with the device stated.
2. Report the CPU figure as-is and note the deployment assumption.
3. Use a smaller input size (`imgsz=480`) and report the accuracy/speed trade-off.

Do **not** quietly change what "throughput" means to make it pass. A near-miss with
the device named is a stronger result than an unexplained pass.

### 5.3 Scope of the throughput figure

Single camera, detector only — decode and tracking excluded. The proposal says
"20+ FPS, multi-camera via FastAPI async", so a complete answer also needs the
figure under concurrent camera load. Not yet measured.

### 5.4 The adversarial result is a worst case

White-box attack with full gradient access, which assumes the attacker has the
model weights. A realistic CCTV attacker usually does not. The figure is the
correct one for the proposal's criterion and the standard threat model in the
literature, but it is the *ceiling* of attack effectiveness, not the expected
real-world risk. Black-box or transfer attacks would score considerably better
for the defender, and would be the natural follow-up.
