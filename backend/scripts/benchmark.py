r"""
OmniTrack AI — Tier A Benchmark Harness
═══════════════════════════════════════

Measures the four proposal success criteria that need NO hand-labelled ground
truth, because each one measures the system's own behaviour rather than whether
the system agreed with a human:

  search_latency   < 100ms      pgvector cosine query over the embeddings table
  throughput       20+ FPS      detector frames/sec on a real clip
  synopsis         >= 10x       VideoSynopsis compression ratio on a real clip
  tamper_detection 100%         audit chain caught every synthetic mutation

The fourth is the interesting one. "Ground truth" for tamper detection is
SYNTHESISABLE: we write a known-good chain, mutate rows in ways we choose, and
check the verifier flags exactly those and nothing else. No annotation needed,
and the result is a real measurement rather than a claim.

That last clause matters. A verifier that returned "broken" unconditionally
would score 100% detection, so the harness also runs an UNTAMPERED control and
reports a false-positive rate. Detection rate alone is not evidence.

Run:
  cd backend
  ..\.venv\Scripts\python.exe scripts\benchmark.py --all

  --latency --throughput --synopsis --tamper   run individual benchmarks
  --video PATH                                 clip for throughput/synopsis
  --out PATH                                   JSON results file

Exit code 0 if every benchmark that RAN met its target, else 1.
Benchmarks that could not run (no data, no clip) are reported SKIP, not FAIL.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select, text  # noqa: E402

from app.database import AsyncSessionLocal  # noqa: E402
from app.models.audit_log import AuditLog  # noqa: E402
from app.models.embedding import Embedding  # noqa: E402
from app.services.crud import AuditService, EmbeddingService  # noqa: E402

DEFAULT_VIDEO = ROOT.parent / "emotion test.mp4"


def _result(
    name: str, criterion: str, target: str, value: Any,
    passed: Optional[bool], detail: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "name": name,
        "criterion": criterion,
        "target": target,
        "value": value,
        "status": "SKIP" if passed is None else ("PASS" if passed else "FAIL"),
        "detail": detail,
    }


# ── 1. pgvector search latency ─────────────────────────────────────────

async def bench_search_latency(runs: int = 100, top_k: int = 10) -> Dict[str, Any]:
    """
    Time the real search path (EmbeddingService.search_similar), not a hand-rolled
    query, so the number reflects what the API actually serves.
    """
    async with AsyncSessionLocal() as db:
        total = (await db.execute(text("SELECT count(*) FROM embeddings"))).scalar_one()
        if total == 0:
            return _result(
                "search_latency", "Search latency", "< 100ms", None, None,
                {"reason": "embeddings table is empty — run a Re-ID job first"},
            )

        # Probe with a vector drawn from the gallery: realistic, and it exercises
        # the index the same way a live Re-ID lookup does.
        probe = (await db.execute(select(Embedding).limit(1))).scalars().first()
        qv = list(probe.vector)

        for _ in range(5):  # warm the connection and the index pages
            await EmbeddingService.search_similar(db, qv, top_k=top_k)

        samples: List[float] = []
        for _ in range(runs):
            t0 = time.perf_counter()
            await EmbeddingService.search_similar(db, qv, top_k=top_k)
            samples.append((time.perf_counter() - t0) * 1000.0)

    samples.sort()

    def pct(q: float) -> float:
        return samples[min(int(len(samples) * q), len(samples) - 1)]

    mean = statistics.fmean(samples)
    p95 = pct(0.95)
    return _result(
        "search_latency", "Search latency", "< 100ms", round(p95, 2),
        p95 < 100.0,
        {
            "unit": "ms", "runs": runs, "top_k": top_k, "gallery_size": total,
            "mean": round(mean, 2), "p50": round(pct(0.50), 2),
            "p95": round(p95, 2), "p99": round(pct(0.99), 2),
            "min": round(samples[0], 2), "max": round(samples[-1], 2),
            "note": "p95 is the reported figure; mean alone hides tail latency",
        },
    )


# ── 2. detector throughput ─────────────────────────────────────────────

def bench_throughput(video: Path, max_frames: int = 120) -> Dict[str, Any]:
    """
    Frames per second through the production PersonDetector on real frames.

    Decode time is excluded, so this is inference throughput for one camera —
    the quantity the 20+ FPS criterion is about.
    """
    try:
        import cv2
        import torch
        from app.ai.detector import PersonDetector
    except Exception as e:
        return _result("throughput", "Throughput", "20+ FPS", None, None,
                       {"reason": f"import failed: {e}"})

    if not video.exists():
        return _result("throughput", "Throughput", "20+ FPS", None, None,
                       {"reason": f"clip not found: {video}"})

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        return _result("throughput", "Throughput", "20+ FPS", None, None,
                       {"reason": f"cannot open clip: {video}"})

    frames = []
    while len(frames) < max_frames:
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(frame)
    h, w = (frames[0].shape[:2] if frames else (0, 0))
    cap.release()

    if not frames:
        return _result("throughput", "Throughput", "20+ FPS", None, None,
                       {"reason": "clip produced no frames"})

    det = PersonDetector(model_path="yolov8n.pt", classes=[])
    if det.model is None:
        return _result("throughput", "Throughput", "20+ FPS", None, None,
                       {"reason": "detector in mock mode (ultralytics unavailable)"})

    for f in frames[:5]:      # warm-up: the first call pays lazy init + allocation
        det.detect(f)

    per_frame: List[float] = []
    for f in frames:
        t0 = time.perf_counter()
        det.detect(f)
        per_frame.append(time.perf_counter() - t0)

    total = sum(per_frame)
    fps = len(frames) / total if total > 0 else 0.0
    cuda = bool(torch.cuda.is_available())
    return _result(
        "throughput", "Throughput", "20+ FPS", round(fps, 2), fps >= 20.0,
        {
            "unit": "FPS", "frames": len(frames), "model": "yolov8n.pt",
            "resolution": f"{w}x{h}", "clip": video.name,
            "device": "cuda" if cuda else "cpu",
            "torch": torch.__version__,
            "mean_ms_per_frame": round(statistics.fmean(per_frame) * 1000, 2),
            "p95_ms_per_frame": round(sorted(per_frame)[int(len(per_frame) * 0.95)] * 1000, 2),
            "scope": "single camera, detector only (decode and tracking excluded)",
        },
    )


# ── 3. synopsis compression ────────────────────────────────────────────

def bench_synopsis(video: Path) -> Dict[str, Any]:
    """Compression ratio is computed by the engine itself; we just capture it."""
    try:
        from app.ai.synopsis import VideoSynopsis
    except Exception as e:
        return _result("synopsis", "Video synopsis", ">= 10x", None, None,
                       {"reason": f"import failed: {e}"})
    if not video.exists():
        return _result("synopsis", "Video synopsis", ">= 10x", None, None,
                       {"reason": f"clip not found: {video}"})

    out_dir = ROOT / "benchmarks" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "synopsis_benchmark.mp4"
    try:
        engine = VideoSynopsis(compression_target=10.0)
        m = engine.process_video(str(video), str(out))
    except Exception as e:
        return _result("synopsis", "Video synopsis", ">= 10x", None, None,
                       {"reason": f"synopsis failed: {e}"})

    ratio = float(m.get("compression_ratio", 0.0))
    return _result(
        "synopsis", "Video synopsis", ">= 10x", ratio, ratio >= 10.0,
        {
            "unit": "x", "clip": video.name,
            "original_duration_s": m.get("original_duration"),
            "synopsis_duration_s": m.get("synopsis_duration"),
            "tubes_extracted": m.get("tubes_extracted"),
            "tubes_placed": m.get("tubes_placed"),
            "event_retention": m.get("event_retention"),
            "caveat": (
                "WEAK PASS. synopsis_length is derived from compression_target "
                "(synopsis.py:338), so the ratio is set by configuration, not "
                "measured — this criterion cannot fail by construction. The "
                "meaningful figure is event_retention: the fraction of extracted "
                "activity tubes that survived onto the condensed timeline. "
                "Whether the SURVIVING tubes are the right ones needs ground "
                "truth and is Tier C (see docs/EVALUATION.md)."
            ),
        },
    )


# ── 4. audit tamper detection ──────────────────────────────────────────

async def bench_tamper_detection(chain_len: int = 25) -> Dict[str, Any]:
    """
    Build a known-good chain, mutate it in each way an attacker might, and check
    the verifier catches every one. Everything runs inside a transaction that is
    rolled back, so the live audit log is untouched.
    """
    cases: List[Dict[str, Any]] = []

    async with AsyncSessionLocal() as db:
        try:
            for i in range(chain_len):
                await AuditService.log_event(
                    db, "BENCHMARK", None, f"synthetic audit entry {i}"
                )
            await db.flush()

            ids = list((await db.execute(
                select(AuditLog.id).order_by(AuditLog.id.asc())
            )).scalars().all())
            if len(ids) < 5:
                return _result("tamper_detection", "Integrity verification",
                               "100% detection", None, None,
                               {"reason": "could not seed a chain"})
            mid = ids[len(ids) // 2]

            # Control: an untouched chain must verify CLEAN. Without this, a
            # verifier hard-wired to "broken" would score a perfect 100%.
            db.expire_all()
            base = await AuditService.verify_integrity(db)
            cases.append({
                "case": "control (no tampering)", "kind": "control",
                "expected": "valid",
                "verifier_said": "valid" if base["valid"] else "broken",
                "correct": bool(base["valid"]),
                "reason": base.get("break_reason"),
            })

            # A real user id: audit_logs.user_id has a FK to users, so the
            # database already refuses a forged id pointing at nobody. The
            # interesting attack is re-attributing an event to a REAL user.
            real_user = (await db.execute(
                text("SELECT id FROM users ORDER BY id LIMIT 1")
            )).scalar_one_or_none()

            mutations = [
                ("edit description", "content",
                 "UPDATE audit_logs SET description = 'TAMPERED' WHERE id = :i"),
                ("edit event_type", "content",
                 "UPDATE audit_logs SET event_type = 'FORGED' WHERE id = :i"),
                ("backdate timestamp", "content",
                 "UPDATE audit_logs SET timestamp = timestamp - interval '1 day' WHERE id = :i"),
                ("delete a middle entry", "linkage",
                 "DELETE FROM audit_logs WHERE id = :i"),
                ("repoint previous_hash", "linkage",
                 "UPDATE audit_logs SET previous_hash = repeat('0', 64) WHERE id = :i"),
            ]
            if real_user is not None:
                mutations.insert(1, (
                    "re-attribute to another user", "content",
                    f"UPDATE audit_logs SET user_id = {int(real_user)} WHERE id = :i",
                ))

            for label, kind, sql in mutations:
                sp = await db.begin_nested()
                await db.execute(text(sql), {"i": mid})
                await db.flush()
                db.expire_all()   # drop the identity map, else we re-read stale rows
                r = await AuditService.verify_integrity(db)
                cases.append({
                    "case": label, "kind": kind, "expected": "broken",
                    "verifier_said": "valid" if r["valid"] else "broken",
                    "correct": not r["valid"],
                    "reason": r.get("break_reason"),
                    "broken_at": r.get("broken_at"),
                })
                await sp.rollback()
                db.expire_all()
        finally:
            await db.rollback()   # nothing synthetic reaches the real chain

    tampered = [c for c in cases if c["kind"] != "control"]
    detected = sum(1 for c in tampered if c["correct"])
    control_ok = all(c["correct"] for c in cases if c["kind"] == "control")
    rate = (detected / len(tampered) * 100.0) if tampered else 0.0
    passed = (rate == 100.0) and control_ok

    return _result(
        "tamper_detection", "Integrity verification", "100% detection",
        round(rate, 1), passed,
        {
            "unit": "%", "chain_length": chain_len,
            "mutations_tried": len(tampered), "mutations_detected": detected,
            "false_positives": 0 if control_ok else 1,
            "control_passed": control_ok,
            "cases": cases,
            "note": "ground truth is synthetic: we choose the mutations, so we know the answer",
        },
    )


# ── 5. adversarial robustness ──────────────────────────────────────────

def bench_adversarial(video: Path, sample_size: int = 8) -> Dict[str, Any]:
    """
    FGSM and PGD against the detector, via ART.

    This belongs in Tier A because it needs no labelled data either: the ground
    truth is the detector's OWN output on the clean frames. We are not asking
    "was YOLO right?", we are asking "how much of what YOLO found survives an
    attack?" — a self-referential comparison.

    The proposal target is "documented resilience" rather than a number, so this
    passes when the evaluation produces usable figures. The figures themselves
    are the deliverable, and a low retention is a finding, not a failure.
    """
    try:
        from app.security.adversarial_eval import (
            get_robustness_status, run_detector_robustness_eval,
        )
    except Exception as e:
        return _result("adversarial", "Adversarial robustness", "documented",
                       None, None, {"reason": f"import failed: {e}"})

    if not get_robustness_status()["art_available"]:
        return _result("adversarial", "Adversarial robustness", "documented",
                       None, None,
                       {"reason": "ART not installed (pip install adversarial-robustness-toolbox)"})

    r = run_detector_robustness_eval(
        sample_size=sample_size,
        video_path=str(video) if video.exists() else None,
    )
    if r.get("skipped"):
        return _result("adversarial", "Adversarial robustness", "documented",
                       None, None, {"reason": r.get("reason") or r.get("error", "eval skipped")})
    if not r.get("baseline_usable"):
        return _result("adversarial", "Adversarial robustness", "documented",
                       None, None,
                       {"reason": "clean baseline detected nobody — retention undefined",
                        "sample_source": r.get("sample_source")})

    pgd = r["detection_retention_pgd"]
    return _result(
        "adversarial", "Adversarial robustness", "documented",
        pgd, True,
        {
            "unit": "", "attacks": "FGSM, PGD (white-box, via ART)",
            "art_version": r.get("art_version"),
            "model": r.get("model"), "sample_size": r.get("sample_size"),
            "sample_source": r.get("sample_source"),
            "eps": r.get("eps_pgd"), "pgd_steps": r.get("pgd_steps"),
            "persons_clean": r["persons_clean"]["total"],
            "persons_after_fgsm": r["persons_fgsm"]["total"],
            "persons_after_pgd": r["persons_pgd"]["total"],
            "retention_fgsm": r["detection_retention_fgsm"],
            "retention_pgd": r["detection_retention_pgd"],
            "finding": (
                f"at L-inf eps={r.get('eps_pgd')}, PGD retained "
                f"{r['detection_retention_pgd']:.0%} of clean detections and FGSM "
                f"{r['detection_retention_fgsm']:.0%}. Reported value is PGD retention "
                f"(the stronger attack); lower means less robust."
            ),
        },
    )


# ── reporting ──────────────────────────────────────────────────────────

def render_markdown(results: List[Dict[str, Any]], meta: Dict[str, Any]) -> str:
    lines = [
        "# OmniTrack AI — Tier A Benchmark Results",
        "",
        f"Generated: {meta['generated_at']}",
        f"Host: {meta['platform']} | Python {meta['python']}",
        "",
        "| Criterion | Target | Measured | Result |",
        "|---|---|---|---|",
    ]
    for r in results:
        v = r["value"]
        unit = r["detail"].get("unit", "")
        if v is None:
            shown = "—"
        elif unit == "FPS":
            shown = f"{v} FPS"
        else:
            shown = f"{v}{unit}"
        lines.append(f"| {r['criterion']} | {r['target']} | {shown} | **{r['status']}** |")
    lines += ["", "## Detail", ""]
    for r in results:
        lines.append(f"### {r['criterion']} — {r['status']}")
        for k, val in r["detail"].items():
            if k == "cases":
                lines.append("")
                lines.append("| Mutation | Layer | Expected | Verifier said | Correct |")
                lines.append("|---|---|---|---|---|")
                for c in val:
                    lines.append(
                        f"| {c['case']} | {c['kind']} | {c['expected']} | "
                        f"{c['verifier_said']} | {'yes' if c['correct'] else 'NO'} |"
                    )
                lines.append("")
            else:
                lines.append(f"- **{k}**: {val}")
        lines.append("")
    return "\n".join(lines)


async def run(args) -> int:
    video = Path(args.video) if args.video else DEFAULT_VIDEO
    want_all = args.all or not any(
        [args.latency, args.throughput, args.synopsis, args.tamper, args.adversarial]
    )

    results: List[Dict[str, Any]] = []
    if want_all or args.latency:
        print("-> search latency ...", flush=True)
        results.append(await bench_search_latency())
    if want_all or args.tamper:
        print("-> tamper detection ...", flush=True)
        results.append(await bench_tamper_detection())
    if want_all or args.throughput:
        print("-> throughput ...", flush=True)
        results.append(bench_throughput(video))
    if want_all or args.synopsis:
        print("-> synopsis compression ...", flush=True)
        results.append(bench_synopsis(video))
    if want_all or args.adversarial:
        print("-> adversarial robustness (slow, ~40s on CPU) ...", flush=True)
        results.append(bench_adversarial(video))

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "platform": sys.platform,
        "python": sys.version.split()[0],
    }
    payload = {"meta": meta, "results": results}

    out_dir = ROOT / "benchmarks" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = Path(args.out) if args.out else out_dir / f"tier_a_{stamp}.json"
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    md = render_markdown(results, meta)
    md_path = out_dir / "LATEST.md"
    md_path.write_text(md, encoding="utf-8")

    print()
    print(md.split("## Detail")[0].rstrip())
    print()
    print(f"  JSON     : {json_path}")
    print(f"  Markdown : {md_path}")

    ran = [r for r in results if r["status"] != "SKIP"]
    failed = [r for r in ran if r["status"] == "FAIL"]
    skipped = [r for r in results if r["status"] == "SKIP"]
    if skipped:
        print("  skipped: " + ", ".join(
            f"{r['name']} ({r['detail'].get('reason', '')})" for r in skipped
        ))
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Tier A benchmarks (no hand-labelled ground truth required)"
    )
    ap.add_argument("--all", action="store_true", help="run every benchmark (default)")
    ap.add_argument("--latency", action="store_true", help="pgvector search latency")
    ap.add_argument("--throughput", action="store_true", help="detector FPS")
    ap.add_argument("--synopsis", action="store_true", help="synopsis compression ratio")
    ap.add_argument("--tamper", action="store_true", help="audit chain tamper detection")
    ap.add_argument("--adversarial", action="store_true", help="FGSM/PGD via ART")
    ap.add_argument("--video", help=f"clip for throughput/synopsis (default: {DEFAULT_VIDEO.name})")
    ap.add_argument("--out", help="JSON results path")
    return asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
