# Audience Mix (`/demographics`)

Age and gender per visitor, from the face reads DeepFace already performs for
emotion.

---

## What this replaced

`GET /api/demographics/current` ended in a cold-start fallback returning a
hand-written age curve and a 72/66 gender split — **138 people who did not
exist**. Because nothing ever wrote `demographic_snapshots`, `total_count` was
always 0, the real branch never fired, and that fallback was the only path a
caller could reach. The page itself was honest: it rendered whatever the API
sent, and carried an empty state it could never display.

Two mock generators in `app/ai/emotion.py` are also gone. They returned random
emotions and a coin-flip gender whenever DeepFace failed to import, so a missing
dependency looked exactly like a working sentiment feed. Both paths now return
nothing when there is nothing.

---

## No new model

DeepFace was already installed and already running for emotion, and the same
call returns age and gender. `analyze_demographics()` was written and asked for
all three actions — it simply had no caller.

InsightFace (`buffalo_l`) gives tighter ages but means a second face detector
competing for CPU on the same frames, a new dependency, and a deviation from the
proposal's stated "DeepFace/FER". If age proves poor on real footage, the swap
is contained to `EmotionRecognizer.analyze_faces`.

**Scope, stated plainly:** demographics is **not in the FYP proposal**. The
document names emotion recognition; age and gender appear nowhere. This is
product value, not criterion value — worth building because it is nearly free
alongside emotion, but it does not displace the six unmeasured criteria in
`EVALUATION.md`.

---

## How it works

### 1. One DeepFace call, not two

`EmotionRecognizer.analyze_faces(frame, …, demographics=True)` asks for
`["emotion", "age", "gender"]` in the pass the pipeline was already making.
Detection and alignment dominate the cost; the extra classifier heads are
comparatively cheap, and a second pass would double the most expensive thing in
the pipeline. Throttling is unchanged: `EMOTION_EVERY_N_TICKS = 15`, per camera,
with the previous summary re-served between runs.

### 2. Faces are matched to people

DeepFace is handed the whole frame and finds faces itself, which says nothing
about *which person* a face belongs to. Each face box is matched to the person
box containing it — highest containment, at least
`DEMOGRAPHICS_FACE_CONTAINMENT` (0.6) of the face inside — using the
`person_detections` the pipeline already has.

Faces that match nobody are counted as `unattributed_faces` and carry **no age
or gender**. That is usually a job with no person class enabled. They are
surfaced rather than dropped, the same treatment Footfall gives untracked
detections.

### 3. One row per visitor, not per frame

This is the design decision that matters. DeepFace's age estimate moves several
years between consecutive frames of the same face. A row per observation turns
one shopper standing at a till for thirty seconds into fifteen "people" whose
ages disagree.

Instead, samples accumulate per `(camera, track)` and reduce to **one row**
carrying the **median age** and the **modal gender** when the visit ends:

| Stage | Where |
|---|---|
| Bank a sample | `ProcessingPipeline._absorb_faces` |
| Keep a visit alive while its person is on screen | `_touch_demographic_tracks` |
| Close a quiet visit | `_retire_demographic_visits` |
| Reduce samples to a row | `_visit_to_row` |
| Write it | `PersistencePipelineCallback._write_demographics` |

A visit closes `DEMOGRAPHICS_VISIT_TIMEOUT_S` (45s) after its **track** was last
seen, not after its face was — a shopper who turns away would otherwise be
recorded as two separate visitors. Open visits are force-closed when the camera
is removed or the pipeline stops, and the stop lifecycle event drains them.

### 4. Quality gates

`analyze_frame` dropped junk faces with three checks; `analyze_demographics` had
**none** of them. With `enforce_detection=False` a faceless frame comes back as
the whole frame with `face_confidence == 0.0` — which would have been recorded
as a confident thirty-year-old. The three are now one shared helper,
`_usable_face`:

- `face_confidence >= EMOTION_MIN_FACE_CONFIDENCE` (0.5)
- at least one eye landmark (coupled to `mtcnn`; `ssd` never emits them)
- face region < 90% of frame area

Plus a fourth, demographics only: **minimum face size**,
`DEMOGRAPHICS_MIN_FACE_PX`. An age off a 20x20 face is a number with no
information in it. Faces below it still count toward presence but contribute no
age or gender.

### 5. Age buckets defined once

`app/config.py` holds `AGE_BUCKETS`, `age_group()` and `normalize_gender()`.
Three places used to define buckets and all three disagreed, so a row written as
`26-35` could land in a chart with no such bar. The API also ships `age_groups`
to the page, so the frontend does not hard-code them either. Gender is
normalised at the write boundary: `Man → male`, `Woman → female`, anything else
`unknown`.

### 6. Emotion persists too

The row carries `dominant_emotion` and `sentiment_score` from the same DeepFace
result. It costs nothing extra and gives the system its **only** emotion
history — the Emotion page is live-only and blanks when the pipeline stops.

---

## API

```
GET /api/demographics/facets     cameras, zones, age bands, data time range
GET /api/demographics/overview   totals, age x gender, timeline, per zone, moods
GET /api/demographics/current    the original shape, kept for compatibility
```

`/overview` takes `camera_id`, `zone`, `start`, `end`, `bucket`
(`minute|hour|day`).

The cross-tab is kept **joint**. `get_demographics_breakdown` grouped age and
gender separately, which marginalises the cross-tab away and makes "women aged
26-35" unanswerable.

`totals.reportable` is `visitors >= min_sample_for_percentages` (30). Below it
the page prints counts and hides shares: "62% female" out of eight faces is
noise presented as fact.

---

## Page

`frontend/src/pages/DemographicsPage.jsx`, built on the Footfall shell
(`.ff-*`, themed for light and dark) with `.dm-*` additions.

- **Sample-size banner** — faces, visitors, and how many could not be attributed
- **Headline** — visitors analysed, median age, gender split, busiest zone
- **Age x gender pyramid** — male left, female right, one row per band
- **Age bands** — totals including visitors whose gender was not read, which the
  pyramid cannot show
- **Gender donut** — with `n` stated beside it
- **Who visits when** — stacked area per band; the commercially useful one,
  since morning and evening rarely share an audience
- **Mood on arrival** — the persisted emotion
- **By zone** — visitors, median age, gender split per camera/zone
- **Footnote** — age is an estimate with an error of several years, which is why
  bands are shown rather than exact ages

---

## Config

| Setting | Default | Meaning |
|---|---|---|
| `ENABLE_DEMOGRAPHICS` | `True` | Rides on emotion; off when emotion is off |
| `DEMOGRAPHICS_MIN_FACE_PX` | see `config.py` | Smallest face that may give an age |
| `DEMOGRAPHICS_VISIT_TIMEOUT_S` | `45.0` | Quiet period before a visit is written |
| `DEMOGRAPHICS_FACE_CONTAINMENT` | `0.6` | Face-in-person overlap needed to attribute |

---

## Limits

### `DECODE_IMGSZ` is the binding constraint — read this first

Frames are downscaled at decode time so their longest side is `DECODE_IMGSZ`,
which is **480** here. That value is not in a `.env` file — there is none, only
`.env.example` — it comes from the `laptop` runtime profile default applied in
`config.py::apply_runtime_profile_defaults`. Everything downstream — detector,
tracker and DeepFace — sees that smaller frame, not the source.

Measured on this project's own clips:

| Clip | Face in source (1280 wide) | Face at `DECODE_IMGSZ=480` |
|---|---|---|
| Close-range (`emotion test.mp4`) | 97–284 px | ~36–43 px |
| Checkout (`camera_3_…_checkout.mp4`) | 18–44 px | ~7–16 px |

The close-range clip survives the downscale; **the checkout clip does not.** At
7–16px there is nothing for an age model to read, and no threshold setting
changes that. To get demographics from a wide camera, raise `DECODE_IMGSZ` (960
or 1280) and accept the detector cost, or place the camera closer.

The pipeline logs this once per camera when every face it finds is too small,
naming the setting, so it does not have to be diagnosed twice.

### Where the size threshold came from

`DEMOGRAPHICS_MIN_FACE_PX` was set by measurement, not by taste. The same frames
were read at full size and at `DECODE_IMGSZ=480`:

| Frame | Face at 1280 | Age | Face at 480 | Age | Error |
|---|---|---|---|---|---|
| 0 | 97px | 44 | 38px | 41 | 3y |
| 15 | 102px | 45 | 36px | 42 | 3y |
| 30 | 103px | 47 | 38px | 42 | 5y |
| 45 | 98px | 39 | 38px | 41 | 2y |
| 120 | 115px | 40 | 40px | 39 | 1y |
| 150 | 113px | 46 | 43px | 44 | 2y |

Mean disagreement **2.7 years**, and gender correct in every case — *better*
agreement than the model has with itself between consecutive full-size frames,
where the spread is around 7 years. A first pass set the threshold at 45px on
the assumption that small faces were unreadable; the measurement showed that was
wrong and discarding usable data, so it is **35**, the bottom of the measured
range. Below 35 there is no evidence, so nothing is recorded.

This is also the strongest argument for the median-per-visitor design: the
model's uncertainty about *the same face* is larger than the cost of the
downscale.

**Faces must be visible.** A ceiling-mounted camera yields the tops of heads and
DeepFace will correctly return nothing. The page then shows its empty state,
which is the honest answer, not a bug.

**Age is an estimate.** Several years of error is normal, and CCTV faces are
smaller and worse lit than the benchmark faces the model was measured on. Bands,
not ages, and the page says so.

**First run downloads weights.** DeepFace fetches its Age and Gender models on
first use (hundreds of MB). A run that appears to hang is probably downloading.
Pre-warm before any demo.

**Privacy.** `track_id` ties an inferred age and gender to a tracked individual
for the length of one camera session. That is consistent with `detections`,
which already stores `track_id`, and it never becomes a durable identity — but
it is a real change in what the row means, and worth stating against the
proposal's Privacy-by-Design claim.
