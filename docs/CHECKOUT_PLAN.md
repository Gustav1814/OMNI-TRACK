# Queue Insights — implementation plan

## Why this is needed

The counting engine (`app/ai/checkout_analytics.py`) is written and already runs
on every pipeline tick. It produces nothing because **`self.lanes` is always
empty** — `add_lane()` is never called from anywhere in the codebase, and there
is no way for an operator to say where a till is. So `/api/checkout/metrics`
returns `[]` and `/checkout` shows zeros, permanently.

Nothing needs training or a new model. A queue is people standing inside a
marked area — the pipeline already detects and tracks every person. The missing
piece is the mark.

---

## What gets measured, and what it is honestly called

One box per lane, covering the queue and the till. From that:

| Metric | Definition | Notes |
|---|---|---|
| **Queue length** | people inside the box right now | The proposal's success criterion (±1 person) |
| **Time in lane** | from entering the box to leaving it | Wait **and** service combined |
| **Throughput** | customers leaving the box per hour | Extrapolated from recent exits |
| **Wait estimate** | avg time in lane × queue length | A projection, not a measurement |

The engine currently calls the second one `avg_service_time`. **It is not
service time** — a box drawn around a queue cannot tell waiting from being
served. Rename it `time_in_lane` in the API and the UI. Calling a wait time
"service time" is the kind of thing that gets picked apart in a viva.

---

## Phase 1 — Register a checkout job

Reuses the region drawing already built; no new UI component.

**`frontend/src/components/jobs/jobsApi.js`** — fourth entry in `ACTIVITY_TYPES`:

```js
{
    kpi_name: "checkout_queue",
    label: "Checkout queue",
    allowed_region_types: ["bounding_box"],
    region_descriptions: {
        bounding_box: "One box per till, covering the queue and the serving point.",
    },
}
```

Each drawn box becomes a lane; the region name the user types ("Till 1") becomes
the lane name. `JobConfigRequest` already carries `regions` + `activity_type`, so
nothing changes in the request shape.

**`backend/app/services/pipeline.py`** — in `set_camera_job`, when the activity
is `checkout_queue`, convert the regions into lanes:

- region `coordinates {x_min, y_min, x_max, y_max}` → `CheckoutLane.bbox [x1,y1,x2,y2]`
- `lane_id` = `f"{camera_id}:{region.name}"`, `name` = region name
- `clear_camera_job` must drop that camera's lanes, or a re-registered job
  doubles them. `CheckoutAnalytics` needs a `clear_lanes(camera_id)`.

**Coordinate scaling — do not skip this.** Regions are stored in the pixel
dimensions the browser drew them in (`frame_width`/`frame_height`), and the
pipeline may decode smaller frames. `_region_scale(camera_id, w, h)` already
computes the factor for the other activities. Add an optional `scale=(sx, sy)`
argument to `CheckoutAnalytics.update()` and apply it in `_get_persons_in_lane`.
Rescaling once at registration is not possible — the decoded frame size is not
known until the first frame arrives.

**One bug to fix while here.** `pipeline.py:1617` passes `dets` (every class) to
`checkout.update()`. A chair inside the box currently counts as a customer. Pass
`person_detections[cam_id]` instead.

---

## Phase 2 — Persist it

Everything the engine holds is in memory: the page empties on restart and
"served today" resets. Same fix as Footfall — write to Postgres, read from
Postgres.

**New table `checkout_services`** — one row per completed visit:

```
id, job_id, camera_id, lane_id, lane_name,
track_id, global_id,
enter_at_us, exit_at_us, time_in_lane_s,
created_at
```

**New table `checkout_samples`** — periodic queue depth, for the over-time chart:

```
id, job_id, camera_id, lane_id, queue_length, wait_estimate_s, at_us, created_at
```

Written from `JobPersistence.flush()`, which already runs every 5s. Two things
to get right:

- `CheckoutAnalytics.completed_services` grows forever and is never drained.
  Add `drain_completed()` returning and clearing the list, so a service is
  written once rather than re-inserted on every flush.
- Follow the `job_alerts` precedent for new columns: `ADD COLUMN IF NOT EXISTS`
  at startup, because `create_all` never alters an existing table.

**Do not** repeat the `line_passing_counts` mistake — those rows are cumulative
and rewritten every flush, which is why a one-hour job would write ~580k rows.
`checkout_services` is append-once-per-event; `checkout_samples` is one row per
lane per interval. Both grow linearly.

---

## Phase 3 — API

Mirror the Footfall router, which is the pattern that worked.

**`GET /api/checkout/facets`** — cameras, lanes, jobs, data time range.

**`GET /api/checkout/overview?start&end&camera_id&lane_id&bucket`**

```
totals    customers_served, avg_time_in_lane_s, median_time_in_lane_s,
          peak_queue, busiest_lane, lanes_active
lanes     per lane: served, avg/median/max time in lane, peak queue, throughput/hr
timeline  per bucket: avg queue length, customers served
```

Median matters more than mean here: one abandoned trolley sitting in frame for
twenty minutes drags the average badly.

**`GET /api/checkout/live`** — current queue per lane, read from the pipeline
snapshot. This is the one genuinely live view; keep it separate from history so
an empty pipeline does not blank the historical page.

Delete the old `/metrics` and `/summary`, or reduce them to aliases.

---

## Phase 4 — The page

Rebuild `frontend/src/pages/CheckoutPage.jsx` in the Footfall/Alerts style
(`.ff-*` classes are reusable; add `.chk-*` only where the shape differs).

**Header:** filters — camera, lane, from/to, group by.

**Headline stats:**
- Customers served
- Median time in lane
- Peak queue
- Busiest lane

**Live strip** (only when a job is running): one tile per lane showing current
queue length and wait estimate, colour-graded by depth. Hidden entirely when
nothing is running rather than showing zeros.

**Queue depth over time** — area chart per bucket, one series per lane.

**Per-lane table** — served, median, average, longest, peak queue, throughput/hr.

**Distribution** — histogram of time in lane. Shows whether a lane is
consistently slow or occasionally stuck, which an average hides.

**Honest empty states**, as on Footfall: "No checkout job has run yet — register
one with the Checkout queue activity and draw a box over each till."

---

## Phase 5 — Alerts (optional, small)

`QUEUE_ALERT_LENGTH` (default off). When a lane exceeds it for longer than a
debounce window, raise a `job_alerts` row with `alert_type='queue_length'`. It
appears on the Alerts page for free, with evidence crops resolved by the
existing camera+track+time join.

Debounce matters — queue length oscillates as people shuffle, and one alert per
tick would be unusable.

---

## Testing: what footage to look for

- **Fixed camera**, angled or overhead, showing the till and the space in front
- **Two or more lanes in frame** if possible — one lane cannot demonstrate
  "busiest lane" or load imbalance
- **Several minutes**, with people actually joining and leaving. A static queue
  proves nothing
- **People clearly separated**; a dense crowd will merge detections and undercount
- Avoid heavy occlusion by counters or displays

Retail self-checkout and supermarket till footage is common in open CCTV
datasets. Airport security lines work too and are often easier to find.

---

## Validating it (the proposal's criterion)

The proposal asks for **queue length ±1 person, verified by manual counting**.
The method, matching the line-passing validation that already worked:

1. Pick ~20 moments spread across the clip
2. Pause and count people in the lane box by eye
3. Compare against `checkout_samples.queue_length` at that timestamp
4. Report mean absolute error and the proportion within ±1

That is a real measurement and takes under an hour. It is also the only Tier C
criterion where the proposal itself prescribes the method.

---

## Order of work

| Phase | Outcome | Rough size |
|---|---|---|
| 1 | A checkout job can be registered and lanes count live | Small |
| 2 | Numbers survive a restart | Medium |
| 3 | Queryable history | Small |
| 4 | The page | Medium |
| 5 | Queue alerts | Small |
| — | Manual validation | ~1 hour, needs footage |

Phases 1–2 are the ones that matter; 3–4 are the same shape as Footfall and
should go quickly. Phase 1 alone makes the live view work, so it is worth
checking against your footage before building the rest.
