# OmniTrack AI — Tier A Benchmark Results

Generated: 2026-09-18T09:24:41.245748+00:00
Host: win32 | Python 3.13.9

| Criterion | Target | Measured | Result |
|---|---|---|---|
| Search latency | < 100ms | 2.49ms | **PASS** |
| Integrity verification | 100% detection | 100.0% | **PASS** |
| Throughput | 20+ FPS | 19.65 FPS | **FAIL** |
| Video synopsis | >= 10x | 10.0x | **PASS** |
| Adversarial robustness | documented | 0.0 | **PASS** |

## Detail

### Search latency — PASS
- **unit**: ms
- **runs**: 100
- **top_k**: 10
- **gallery_size**: 94
- **mean**: 1.83
- **p50**: 1.79
- **p95**: 2.49
- **p99**: 3.22
- **min**: 1.52
- **max**: 3.22
- **note**: p95 is the reported figure; mean alone hides tail latency

### Integrity verification — PASS
- **unit**: %
- **chain_length**: 25
- **mutations_tried**: 6
- **mutations_detected**: 6
- **false_positives**: 0
- **control_passed**: True

| Mutation | Layer | Expected | Verifier said | Correct |
|---|---|---|---|---|
| control (no tampering) | control | valid | valid | yes |
| edit description | content | broken | broken | yes |
| re-attribute to another user | content | broken | broken | yes |
| edit event_type | content | broken | broken | yes |
| backdate timestamp | content | broken | broken | yes |
| delete a middle entry | linkage | broken | broken | yes |
| repoint previous_hash | linkage | broken | broken | yes |

- **note**: ground truth is synthetic: we choose the mutations, so we know the answer

### Throughput — FAIL
- **unit**: FPS
- **frames**: 120
- **model**: yolov8n.pt
- **resolution**: 1280x720
- **clip**: emotion test.mp4
- **device**: cpu
- **torch**: 2.14.0+cpu
- **mean_ms_per_frame**: 50.9
- **p95_ms_per_frame**: 71.1
- **scope**: single camera, detector only (decode and tracking excluded)

### Video synopsis — PASS
- **unit**: x
- **clip**: emotion test.mp4
- **original_duration_s**: 10.0
- **synopsis_duration_s**: 1.0
- **tubes_extracted**: 359
- **tubes_placed**: 359
- **event_retention**: 1.0
- **caveat**: WEAK PASS. synopsis_length is derived from compression_target (synopsis.py:338), so the ratio is set by configuration, not measured — this criterion cannot fail by construction. The meaningful figure is event_retention: the fraction of extracted activity tubes that survived onto the condensed timeline. Whether the SURVIVING tubes are the right ones needs ground truth and is Tier C (see docs/EVALUATION.md).

### Adversarial robustness — PASS
- **unit**: 
- **attacks**: FGSM, PGD (white-box, via ART)
- **art_version**: 1.20.1
- **model**: yolov8n.pt
- **sample_size**: 8
- **sample_source**: video:emotion test.mp4
- **eps**: 0.03
- **pgd_steps**: 10
- **persons_clean**: 8
- **persons_after_fgsm**: 6
- **persons_after_pgd**: 0
- **retention_fgsm**: 0.75
- **retention_pgd**: 0.0
- **finding**: at L-inf eps=0.03, PGD retained 0% of clean detections and FGSM 75%. Reported value is PGD retention (the stronger attack); lower means less robust.
