"""Report figures (PNG) for the final-year report, built only from real evaluation data.

A. Metric charts: read eval/results/*.json (written by the eval scripts). A
   missing result file or metric is left off - never filled in.
B. Qualitative panels: a few seconds of inference per module on a handful of
   frozen dataset samples, chosen by a fixed rule (the first N items of the
   frozen manifest, plus the first failure if none of those fails), so
   failures are shown next to successes and nothing is cherry-picked.

Writes docs/report/figures/*.png, docs/report/figures/index.json and
docs/report/FIGURES.md (one entry per figure: caption, data source, the exact
numbers shown). Every image carries its dataset in a footer.

    python eval/report_figures.py                       # everything (DEVICE auto: CUDA if present)
    python eval/report_figures.py --only charts          # charts only, no inference
    python eval/report_figures.py --only qual --figures detection,fire
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

import numpy as np

from common import (
    BACKEND,
    CRITERIA_FILE,
    DATASETS_DIR,
    MANIFESTS_DIR,
    REPO,
    RESULTS_DIR,
    WEIGHTS_DIR,
    base_parser,
    cap_threads,
    log,
    ml_lock,
    resolve_device,
    torch_threads,
)

OUT = REPO / "docs" / "report" / "figures"
MD = REPO / "docs" / "report" / "FIGURES.md"
INDEX = OUT / "index.json"

# ---- dataviz tokens (light mode, print) -------------------------------------------------------------
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1"
CAT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]  # fixed order, never cycled
SEQ = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#2a78d6", "#256abf", "#184f95", "#0d366b"]
GOOD, CRITICAL = "#0ca30c", "#d03b3b"  # status only, always with a text label

CHARTS = ["summary", "detection", "tracking", "reid", "reid_caviar", "reid_finetune", "crowd", "emotion",
          "demographics", "fire", "throughput", "search_latency", "synopsis", "adversarial"]
QUAL = ["q_detection", "q_tracking", "q_reid", "q_crowd", "q_fire", "q_emotion", "q_demographics",
        "q_adversarial"]

ENTRIES: dict[str, dict] = {}
GAPS: dict[str, str] = {}
_plt = None


def plt():
    global _plt
    if _plt is None:
        import matplotlib  # noqa: PLC0415

        matplotlib.use("Agg")
        import matplotlib.pyplot as p  # noqa: PLC0415

        p.rcParams.update({
            "font.family": "DejaVu Sans", "font.size": 9, "text.color": INK,
            "axes.edgecolor": INK2, "axes.labelcolor": INK2, "axes.titlecolor": INK, "axes.titlesize": 10,
            "axes.titleweight": "bold", "axes.titlelocation": "left",
            "xtick.color": INK2, "ytick.color": INK2, "axes.linewidth": 0.6,
            "axes.spines.top": False, "axes.spines.right": False,
            "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6, "axes.axisbelow": True,
            "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
            "legend.frameon": False, "legend.fontsize": 8,
        })
        _plt = p
    return _plt


# ---- helpers ----------------------------------------------------------------------------------------
def load(result_id: str) -> dict | None:
    p = RESULTS_DIR / f"{result_id}.json"
    if not p.is_file():
        return None
    r = json.loads(p.read_text())
    return r if r.get("status") == "measured" else None


def device_label(r: dict) -> str:
    hw = r.get("hardware") or {}
    dev = str(hw.get("device") or "")
    if dev.startswith("cuda"):
        return f"GPU ({hw.get('gpu') or dev})"
    return f"CPU ({hw.get('cpu') or 'unknown'})" if dev else "device not recorded"


def ds_name(r: dict) -> str:
    d = r.get("dataset") or {}
    return " - ".join(x for x in (d.get("name"), d.get("split")) if x)


def footer(fig, text: str) -> None:
    fig.text(0.01, 0.006, text, fontsize=6.5, color=INK2, ha="left", va="bottom", wrap=True)


def save(fig, name: str, group: str, caption: str, source: str, numbers: list[str], dataset: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    footer(fig, f"Dataset: {dataset}")
    fig.savefig(OUT / name, dpi=200)
    plt().close(fig)
    ENTRIES[name] = {"file": f"figures/{name}", "group": group, "caption": caption, "source": source,
                     "dataset": dataset, "numbers": numbers, "generated": time.strftime("%Y-%m-%d %H:%M")}
    log(f"wrote {name}")


def pct(v) -> str:
    return "n/a" if v is None else f"{100 * v:.1f}%"


def hbars(ax, labels: list[str], series: dict[str, list], fmt=lambda v: f"{v:.3f}", xmax=None):
    """Grouped horizontal bars, value label at each bar end (ink colour), legend for >= 2 series."""
    n = len(series)
    h = 0.8 / n
    y = np.arange(len(labels))
    for k, (name, vals) in enumerate(series.items()):
        pos = y - 0.4 + h * (k + 0.5)
        v = np.array([np.nan if x is None else x for x in vals], float)
        ax.barh(pos, np.nan_to_num(v), height=h * 0.92, color=CAT[k], label=name if n > 1 else None,
                edgecolor=SURFACE, linewidth=0.8)
        for yy, vv in zip(pos, v):
            if not np.isnan(vv):
                ax.text(vv, yy, " " + fmt(vv), va="center", ha="left", fontsize=7.5, color=INK)
    ax.set_yticks(y, labels)
    ax.invert_yaxis()
    ax.grid(axis="y", visible=False)
    if xmax:
        ax.set_xlim(0, xmax)
    if n > 1:
        ax.legend(loc="lower right", bbox_to_anchor=(1, 1.0), ncol=n, fontsize=7.5)


def vbars(ax, labels: list[str], series: dict[str, list], fmt=lambda v: f"{v:.3f}", ymax=None):
    n = len(series)
    w = 0.8 / n
    x = np.arange(len(labels))
    for k, (name, vals) in enumerate(series.items()):
        pos = x - 0.4 + w * (k + 0.5)
        v = np.array([np.nan if x_ is None else x_ for x_ in vals], float)
        ax.bar(pos, np.nan_to_num(v), width=w * 0.92, color=CAT[k], label=name if n > 1 else None,
               edgecolor=SURFACE, linewidth=0.8)
        for xx, vv in zip(pos, v):
            if not np.isnan(vv):
                ax.text(xx, vv, fmt(vv), va="bottom", ha="center", fontsize=7, color=INK)
    ax.set_xticks(x, labels)
    ax.grid(axis="x", visible=False)
    if ymax:
        ax.set_ylim(0, ymax)
    if n > 1:
        ax.legend(loc="lower right", bbox_to_anchor=(1, 1.0), ncol=n, fontsize=7.5)


def refline(ax, v: float, label: str, axis: str = "x") -> None:
    if axis == "x":
        ax.axvline(v, color=INK2, lw=1, ls=(0, (4, 3)))
        ax.text(v, 1.0, f" {label}", transform=ax.get_xaxis_transform(), fontsize=7, color=INK2, va="bottom")
    else:
        ax.axhline(v, color=INK2, lw=1, ls=(0, (4, 3)))
        ax.text(1.0, v, f" {label}", transform=ax.get_yaxis_transform(), fontsize=7, color=INK2, va="center")


def heatmap(ax, counts: list[list[int]], labels: list[str], title: str) -> None:
    from matplotlib.colors import LinearSegmentedColormap  # noqa: PLC0415

    c = np.asarray(counts, float)
    norm = c / np.maximum(c.sum(1, keepdims=True), 1)
    ax.imshow(norm, cmap=LinearSegmentedColormap.from_list("seq", [SURFACE, *SEQ]), vmin=0, vmax=1)
    for i in range(c.shape[0]):
        for j in range(c.shape[1]):
            ax.text(j, i, f"{100 * norm[i, j]:.0f}%\n{int(c[i, j])}", ha="center", va="center", fontsize=6,
                    color=SURFACE if norm[i, j] > 0.55 else INK)
    ax.set_xticks(range(len(labels)), labels, rotation=35, ha="right")
    ax.set_yticks(range(len(labels)), labels)
    ax.set_xlabel("predicted")
    ax.set_ylabel("true (row-normalised)")
    ax.grid(False)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_title(title)


def vlabel(m: dict) -> str:
    return f"{str(m.get('model', '?')).removesuffix('.pt')} @{m.get('imgsz', '?')}"


# ---- A. metric charts -------------------------------------------------------------------------------
def chart_summary() -> None:
    crit = json.loads(CRITERIA_FILE.read_text())["criteria"]
    rows, numbers = [], []
    for c in crit:
        r = load(c["id"])
        pv = (r or {}).get("primary", {}).get("value")
        unit = c.get("unit")
        fmtv = (lambda v: "-" if v is None else (pct(v) if unit == "ratio" else f"{v:g} {unit}"))
        target = "-" if c.get("target") is None else f"{c['op']} {fmtv(c['target'])}"
        if r is None:
            res, dev = "not measured", "-"
        elif c.get("target") is None:
            res, dev = "reported", device_label(r).split(" (")[0]
        else:
            res, dev = ("PASS" if r["primary"].get("passed") else "FAIL"), device_label(r).split(" (")[0]
        rows.append((c["title"], c["metric"], target, fmtv(pv) if r else "-", res, dev))
        numbers.append(f"{c['title']}: {c['metric']} = {fmtv(pv) if r else 'not measured'} (target {target}; {res})")
    fig = plt().figure(figsize=(9, 0.32 * len(rows) + 1.0))
    ax = fig.add_axes([0.01, 0.06, 0.98, 0.86])
    ax.axis("off")
    cols = ["Criterion", "Metric", "Target", "Measured", "Result", "Device"]
    xs = [0.0, 0.27, 0.5, 0.63, 0.76, 0.9]
    n = len(rows) + 1
    for j, h in enumerate(cols):
        ax.text(xs[j], 1 - 0.5 / n, h, fontweight="bold", va="center", fontsize=8.5)
    for i, row in enumerate(rows, start=1):
        yy = 1 - (i + 0.5) / n
        ax.axhline(1 - i / n, color=GRID, lw=0.6)
        for j, v in enumerate(row):
            color = INK if j != 1 else INK2
            if j == 4 and v in ("PASS", "FAIL"):
                ax.add_patch(plt().Rectangle((xs[j], yy - 0.012), 0.012, 0.024, color=GOOD if v == "PASS" else CRITICAL,
                                             transform=ax.transAxes))
                ax.text(xs[j] + 0.018, yy, v, va="center", fontsize=8, fontweight="bold")
                continue
            ax.text(xs[j], yy, v, va="center", fontsize=8, color=color if v not in ("not measured", "-") else INK2)
    ax.set_title("OmniTrack evaluation against the proposal criteria", loc="left", fontweight="bold")
    save(fig, "summary_criteria.png", "summary",
         "Every success criterion with its target and the measured value from eval/results. "
         "'reported' means no numeric target; 'not measured' means no result file exists yet.",
         "eval/criteria.json + eval/results/*.json", numbers, "per row: see the matching result file")


def chart_detection() -> None:
    r = load("detection")
    if not r:
        GAPS["detection"] = "eval/results/detection.json missing"
        return
    vs = r["metrics"].get("variants") or [r["metrics"]["primary"]]
    labels = [vlabel(v) for v in vs]
    series = {"AP@0.5": [v.get("ap50") for v in vs], "Recall @conf 0.5": [v.get("recall_at_conf0.5") for v in vs]}
    series = {k: v for k, v in series.items() if any(x is not None for x in v)}
    fig, ax = plt().subplots(figsize=(7, 0.55 * len(vs) + 1.4))
    hbars(ax, labels, series, xmax=1.08)
    refline(ax, r["primary"]["target"], f"target AP {r['primary']['target']}")
    ax.set_xlabel("ratio")
    ax.set_title("Person detection on CAVIAR (AP@0.5 and recall)", pad=18)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    numbers = [f"{lab}: " + ", ".join(f"{k} {v[i]:.4f}" for k, v in series.items() if v[i] is not None)
               for i, lab in enumerate(labels)]
    save(fig, "detection_ap_recall.png", "detection",
         f"Person AP@0.5 and recall at the 0.5 confidence operating point per detector/input size; first row is "
         f"the primary configuration. Measured on {device_label(r)}.",
         "eval/results/detection.json", numbers, ds_name(r) + " | measured on " + device_label(r))


def chart_tracking() -> None:
    r = load("tracking")
    if not r:
        GAPS["tracking"] = "eval/results/tracking.json missing (run eval/track.py on the PC)"
        return
    ps = r["metrics"].get("presets") or [r["metrics"]["primary"]]
    labels = [f"{p.get('preset')} ({vlabel(p)}, conf {p.get('conf')})" for p in ps]
    series = {k: [p.get(k) for p in ps] for k in ("HOTA", "MOTA", "IDF1") if any(p.get(k) is not None for p in ps)}
    fig, ax = plt().subplots(figsize=(7, 0.7 * len(ps) + 1.4))
    hbars(ax, labels, series, xmax=1.08)
    ax.set_title("Multi-object tracking on CAVIAR (TrackEval)", pad=18)
    ax.set_xlabel("score")
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    numbers = [f"{lab}: " + ", ".join(f"{k} {p.get(k)}" for k in ("HOTA", "MOTA", "IDF1", "IDSW")) for lab, p in zip(labels, ps)]
    save(fig, "tracking_hota_mota_idf1.png", "tracking",
         f"HOTA, MOTA and IDF1 of the shipped tracker per detector preset (ID switches listed in the numbers). "
         f"Measured on {device_label(r)}.", "eval/results/tracking.json", numbers, ds_name(r) + " | measured on " + device_label(r))


def chart_reid() -> None:
    r = load("reid")
    if not r:
        GAPS["reid"] = "eval/results/reid.json missing"
        return
    m = r["metrics"]
    keys = [("Rank-1", "rank1"), ("Rank-5", "rank5"), ("Rank-10", "rank10"), ("mAP", "mAP")]
    keys = [(a, b) for a, b in keys if m.get(b) is not None]
    fig, ax = plt().subplots(figsize=(6, 3.2))
    vbars(ax, [a for a, _ in keys], {"Market-1501": [m[b] for _, b in keys]}, ymax=1.0)
    refline(ax, r["primary"]["target"], f"Rank-1 target {r['primary']['target']}", axis="y")
    ax.set_title("Re-ID on Market-1501 (cross-domain, MSMT17 weights)")
    fig.tight_layout(rect=(0, 0.05, 0.93, 1))
    save(fig, "reid_market1501.png", "reid",
         f"CMC Rank-1/5/10 and mAP of the shipped OSNet-AIN extractor on the Market-1501 test protocol; the weights "
         f"never saw Market-1501. Measured on {device_label(r)}.", "eval/results/reid.json",
         [f"{a} {m[b]:.4f}" for a, b in keys] + [f"queries {m.get('n_query')}, gallery {m.get('n_gallery')}"],
         ds_name(r) + " | measured on " + device_label(r))


def chart_reid_caviar() -> None:
    r = load("reid_caviar")
    if not r:
        GAPS["reid_caviar"] = "eval/results/reid_caviar.json missing (run eval/reid_caviar.py on the PC)"
        return
    m = r["metrics"]
    if not m.get("hist_edges"):
        GAPS["reid_caviar"] = "reid_caviar.json has no similarity histograms"
        return
    e = np.asarray(m["hist_edges"])
    same = np.asarray(m["hist_same"], float) / max(sum(m["hist_same"]), 1)
    diff = np.asarray(m["hist_diff"], float) / max(sum(m["hist_diff"]), 1)
    fig, ax = plt().subplots(figsize=(6.5, 3.4))
    ax.stairs(diff, e, color=CAT[1], lw=2, label=f"different person ({m['neg_pairs']} pairs)")
    ax.stairs(same, e, color=CAT[0], lw=2, label=f"same person, >= 3 s apart ({m['pos_pairs']} pairs)")
    refline(ax, m["t_match"], f"REID_T_MATCH {m['t_match']}")
    ax.set_xlabel("cosine similarity")
    ax.set_ylabel("share of pairs")
    ax.legend(loc="upper left", fontsize=7.5)
    ax.text(0.99, 0.97, f"AUC {m['auc']:.3f}\nTAR@1%FAR {m['tar_at_1pct_far']:.3f}\n"
            f"at T_MATCH: TAR {m['tar_at_t_match']:.3f}, FAR {m['far_at_t_match']:.3f}",
            transform=ax.transAxes, ha="right", va="top", fontsize=7.5)
    ax.set_title("Re-ID similarity on held-out CAVIAR store sequences", pad=14)
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    save(fig, "reid_caviar_similarity.png", "reid_caviar",
         "Distribution of crop-to-crop cosine similarity for same-person and different-person pairs within the "
         "held-out CAVIAR sequences, with the shipped matching threshold. CAVIAR ids are per camera view, so "
         f"cross-view pairs of one person count as 'different' (FAR is pessimistic). Measured on {device_label(r)}.",
         "eval/results/reid_caviar.json",
         [f"AUC {m['auc']}", f"TAR@1%FAR {m['tar_at_1pct_far']} (threshold {m['thr_at_1pct_far']})",
          f"TAR at {m['t_match']} = {m['tar_at_t_match']}, FAR = {m['far_at_t_match']}",
          f"median similarity same {m['same_median']}, different {m['diff_median']}"], ds_name(r) + " | measured on " + device_label(r))


def chart_reid_finetune() -> None:
    p = RESULTS_DIR / "reid_finetune.json"
    if not p.is_file():
        GAPS["reid_finetune"] = "eval/results/reid_finetune.json missing (written by backend/scripts/train_reid.py on the PC)"
        return
    r = json.loads(p.read_text())
    b, a = r.get("before") or {}, r.get("after") or {}
    keys = [("Market Rank-1", "market1501", "rank1"), ("Market mAP", "market1501", "mAP"),
            ("CAVIAR AUC", "caviar_holdout", "auc"), ("CAVIAR TAR@1%FAR", "caviar_holdout", "tar_at_1pct_far")]
    keys = [k for k in keys if (b.get(k[1]) or {}).get(k[2]) is not None and (a.get(k[1]) or {}).get(k[2]) is not None]
    if not keys:
        GAPS["reid_finetune"] = "reid_finetune.json has no before/after metrics"
        return
    fig, ax = plt().subplots(figsize=(6.5, 3.4))
    vbars(ax, [k[0] for k in keys], {"before (MSMT17)": [b[k[1]][k[2]] for k in keys],
                                     "after fine-tuning": [a[k[1]][k[2]] for k in keys]}, ymax=1.08)
    ax.set_title("Re-ID before and after fine-tuning", pad=18)
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    tr = r.get("train") or {}
    save(fig, "reid_finetune_before_after.png", "reid_finetune",
         f"Re-ID before and after fine-tuning on Market-1501 train + CAVIAR train sequences (hold-out sequences "
         f"never trained on); {tr.get('epochs')} epochs on {tr.get('device')}.", "eval/results/reid_finetune.json",
         [f"{k[0]}: {b[k[1]][k[2]]:.4f} -> {a[k[1]][k[2]]:.4f}" for k in keys],
         "Market-1501 test protocol; CAVIAR hold-out sequences")


def chart_crowd() -> None:
    r = load("crowd")
    if not r:
        GAPS["crowd"] = "eval/results/crowd.json missing"
        return
    vs = r["metrics"].get("variants") or [r["metrics"]["primary"]]
    labels = [vlabel(v) for v in vs]
    fig, (a1, a2) = plt().subplots(1, 2, figsize=(8, 0.5 * len(vs) + 1.8))
    hbars(a1, labels, {"count MAE": [v.get("count_mae") for v in vs]}, fmt=lambda v: f"{v:.2f}")
    a1.set_xlabel("people per frame (lower is better)")
    a1.set_title(f"Count error (GT mean {vs[0].get('gt_mean_count')} people)")
    hbars(a2, labels, {"level accuracy": [v.get("level_accuracy") for v in vs]}, xmax=1.0)
    refline(a2, r["primary"]["target"], f"target {r['primary']['target']}")
    a2.set_yticks([])
    a2.set_title("Crowd-level accuracy", pad=14)
    a2.set_xlabel("ratio")
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    save(fig, "crowd_count_error.png", "crowd",
         f"Person-count MAE and crowd-level accuracy on Mall (detector counts inside the dataset ROI at conf "
         f"{r['metrics'].get('count_conf')}, capacity {r['metrics'].get('capacity')}). Measured on {device_label(r)}.",
         "eval/results/crowd.json",
         [f"{lab}: MAE {v.get('count_mae')}, bias {v.get('count_bias')}, level accuracy {v.get('level_accuracy')}"
          for lab, v in zip(labels, vs)], ds_name(r) + " | measured on " + device_label(r))


def chart_emotion() -> None:
    r = load("emotion")
    if not r:
        GAPS["emotion"] = "eval/results/emotion.json missing"
        return
    m = r["metrics"]
    rec, sup = m.get("per_class_recall") or {}, m.get("class_support") or {}
    if rec:
        fig, ax = plt().subplots(figsize=(6.5, 3.4))
        cls = list(rec)
        hbars(ax, [f"{c} (n={sup.get(c, '?')})" for c in cls], {"recall": [rec[c] for c in cls]}, xmax=1.08)
        refline(ax, r["primary"]["value"], f"overall accuracy {r['primary']['value']:.3f}")
        ax.set_title("Facial emotion: per-class recall on FER-2013", pad=14)
        ax.set_xlabel(f"recall (target overall accuracy {r['primary']['target']})")
        fig.tight_layout(rect=(0, 0.05, 1, 1))
        save(fig, "emotion_per_class.png", "emotion",
             "Per-class recall of the shipped AffectNet-trained emotion model on the FER-2013 test split "
             f"(cross-dataset); the dashed line is the overall 7-class accuracy. Measured on {device_label(r)}.",
             "eval/results/emotion.json",
             [f"overall accuracy {r['primary']['value']}", f"macro recall {m.get('macro_recall')}"]
             + [f"{c}: {rec[c]} (n={sup.get(c)})" for c in cls], ds_name(r) + " | measured on " + device_label(r))
    cm = m.get("confusion_matrix")
    if cm:
        fig, ax = plt().subplots(figsize=(5.4, 5))
        heatmap(ax, cm["counts"], cm["rows_true_cols_pred"], "Emotion confusion matrix (FER-2013 test)")
        fig.tight_layout(rect=(0, 0.04, 1, 1))
        save(fig, "emotion_confusion.png", "emotion",
             "Row-normalised confusion matrix (percent of the true class, with counts) on FER-2013 test.",
             "eval/results/emotion.json",
             [f"{t}: " + ", ".join(f"{p} {c}" for p, c in zip(cm["rows_true_cols_pred"], row))
              for t, row in zip(cm["rows_true_cols_pred"], cm["counts"])], ds_name(r) + " | measured on " + device_label(r))


def chart_demographics() -> None:
    r = load("demographics")
    if not r:
        GAPS["demographics"] = "eval/results/demographics.json missing (run eval/demographics.py on the PC)"
        return
    m = r["metrics"]
    groups = m.get("per_race_group") or {}
    keys = [("gender accuracy", "gender_accuracy"), ("age band exact", "age_exact"), ("age band +/-1", "age_within_1")]
    if groups:
        g = list(groups)
        fig, ax = plt().subplots(figsize=(7, 0.6 * len(g) + 1.6))
        hbars(ax, [f"{x} (n={groups[x].get('n')})" for x in g],
              {lab: [groups[x].get(k) for x in g] for lab, k in keys}, xmax=1.12)
        ax.set_title("Age and gender accuracy per FairFace race group", pad=18)
        fig.tight_layout(rect=(0, 0.05, 1, 1))
        gaps = m.get("max_gap_between_race_groups")
        save(fig, "demographics_by_group.png", "demographics",
             "Gender and age-band accuracy of the shipped FairFace model per race group (the dataset's own labels). "
             "Accuracy differs between groups; these estimates must not be used to make decisions about individuals. "
             f"Measured on {device_label(r)}.", "eval/results/demographics.json",
             [f"overall gender {m.get('gender_accuracy')}, age exact {m.get('age_exact')}, +/-1 {m.get('age_within_1')}",
              f"max gap between groups: {gaps}"]
             + [f"{x}: " + ", ".join(f"{lab} {groups[x].get(k)}" for lab, k in keys) for x in g], ds_name(r) + " | measured on " + device_label(r))
    cm = m.get("age_confusion_matrix")
    if cm:
        fig, ax = plt().subplots(figsize=(5.6, 5.2))
        heatmap(ax, cm["counts"], cm["rows_true_cols_pred"], "Age-band confusion matrix (FairFace val)")
        fig.tight_layout(rect=(0, 0.04, 1, 1))
        save(fig, "demographics_age_confusion.png", "demographics",
             "Row-normalised age-band confusion matrix on FairFace validation; most errors fall in a neighbouring band.",
             "eval/results/demographics.json",
             [f"{t}: " + ", ".join(f"{p} {c}" for p, c in zip(cm["rows_true_cols_pred"], row))
              for t, row in zip(cm["rows_true_cols_pred"], cm["counts"])], ds_name(r) + " | measured on " + device_label(r))
    lr = m.get("cctv_like_lowres") or {}
    if lr.get("lowres") and lr.get("native_same_subset"):
        fig, ax = plt().subplots(figsize=(6, 3.2))
        vbars(ax, [lab for lab, _ in keys], {"native resolution": [lr["native_same_subset"].get(k) for _, k in keys],
                                             f"{lr.get('face_width_px')} px faces (CCTV-like)": [lr["lowres"].get(k) for _, k in keys]},
              ymax=1.1)
        ax.set_title("Demographics on low-resolution faces", pad=18)
        fig.tight_layout(rect=(0, 0.05, 1, 1))
        save(fig, "demographics_lowres.png", "demographics",
             f"Same faces at native resolution and downscaled to {lr.get('face_width_px')} px wide ({lr.get('subset')}), "
             "approximating faces seen by a store camera.", "eval/results/demographics.json",
             [f"{lab}: native {lr['native_same_subset'].get(k)}, low-res {lr['lowres'].get(k)}" for lab, k in keys],
             ds_name(r) + " | measured on " + device_label(r))


def chart_fire() -> None:
    r = load("fire")
    if not r:
        GAPS["fire"] = "eval/results/fire.json missing (run eval/fire.py on the PC)"
        return
    d = r["metrics"].get("dfire") or {}
    ops = d.get("operating_points") or []
    if not ops:
        GAPS["fire"] = "fire.json has no operating points"
        return
    fig, ax = plt().subplots(figsize=(6.5, 3.6))
    vbars(ax, [f"conf {o['conf']}" for o in ops], {"precision": [o.get("precision") for o in ops],
                                                   "recall": [o.get("recall") for o in ops],
                                                   "false-positive rate": [o.get("fpr") for o in ops]}, ymax=1.1)
    refline(ax, 0.90, "precision target 0.90", axis="y")
    refline(ax, 0.05, "FPR limit 0.05", axis="y")
    ax.set_title("Fire and smoke (image level, D-Fire test)", pad=18)
    fig.tight_layout(rect=(0, 0.05, 0.86, 1))
    cav = r["metrics"].get("caviar_negatives") or {}
    numbers = [f"conf {o['conf']}: precision {o.get('precision')}, recall {o.get('recall')}, FPR {o.get('fpr')} "
               f"(TP {o.get('tp')}, FP {o.get('fp')}, FN {o.get('fn')}, TN {o.get('tn')})" for o in ops]
    numbers += [f"box AP50 fire {d.get('ap50_fire')}, smoke {d.get('ap50_smoke')}"]
    if cav:
        numbers.append(f"CAVIAR negatives: {cav.get('false_alarms_instant_per_hour')} instant / "
                       f"{cav.get('false_alarms_k_of_n_per_hour')} k-of-n false alarms per hour")
    save(fig, "fire_precision_recall.png", "fire",
         "Image-level precision, recall and false-positive rate of the fire/smoke model at three confidence "
         f"thresholds (the shipped one first). Measured on {device_label(r)}.", "eval/results/fire.json", numbers,
         ds_name(r) + " | measured on " + device_label(r))


def chart_throughput() -> None:
    rows = []  # (label, fps, source)
    for rid, key in (("detection", "variants"), ("crowd", "variants"), ("tracking", "presets")):
        r = load(rid)
        if not r:
            continue
        dev = device_label(r).split(" (")[0]
        for v in r["metrics"].get(key) or []:
            if v.get("ms_per_frame"):
                rows.append((f"{rid}: {vlabel(v)} - {dev}", 1000 / v["ms_per_frame"], f"eval/results/{rid}.json"))
    f = load("fire")
    if f and (f["metrics"].get("dfire") or {}).get("ms_per_image"):
        rows.append((f"fire: fire-smoke @{f['model'].get('imgsz')} - {device_label(f).split(' (')[0]}",
                     1000 / f["metrics"]["dfire"]["ms_per_image"], "eval/results/fire.json"))
    t = load("throughput")
    if not rows and not t:
        GAPS["throughput"] = "no ms_per_frame in any result and no throughput.json"
        return
    n_pan = 1 + bool(t)
    fig, axes = plt().subplots(n_pan, 1, figsize=(7.5, 0.42 * len(rows) + (2.4 if t else 0) + 1.4), squeeze=False)
    numbers = []
    if rows:
        ax = axes[0][0]
        hbars(ax, [r_[0] for r_ in rows], {"FPS": [r_[1] for r_ in rows]}, fmt=lambda v: f"{v:.1f}")
        ax.set_title("Model inference speed during the evaluations (frames per second)")
        ax.set_xlabel("FPS = 1000 / ms per frame (inference only: no decoding, tracking or streaming)")
        numbers += [f"{lab}: {fps:.2f} FPS ({src})" for lab, fps, src in rows]
    if t:
        ax = axes[-1][0]
        pc = t["metrics"].get("per_camera") or {}
        hbars(ax, [f"camera {k}" for k in pc], {"median FPS": [v.get("median_fps") for v in pc.values()]},
              fmt=lambda v: f"{v:.1f}")
        refline(ax, t["primary"]["target"], f"target {t['primary']['target']} FPS per camera")
        ax.set_title(f"End-to-end pipeline FPS per camera - {device_label(t)}", pad=14)
        numbers += [f"camera {k}: median {v.get('median_fps')} FPS, min {v.get('min_fps')}" for k, v in pc.items()]
    else:
        GAPS["throughput_e2e"] = "eval/results/throughput.json missing (needs the running app on the PC)"
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    save(fig, "throughput_fps.png", "throughput",
         "Frames per second per model and device. Rows marked CPU were measured on the development laptop's CPU and "
         "are inference-only; the end-to-end 20 FPS criterion is only shown when eval/results/throughput.json exists.",
         ", ".join(sorted({s for *_, s in rows} | ({"eval/results/throughput.json"} if t else set()))), numbers,
         "CAVIAR / Mall / D-Fire eval runs (see each source)")


def chart_search_latency() -> None:
    r = load("search_latency")
    if not r:
        GAPS["search_latency"] = "eval/results/search_latency.json missing"
        return
    m = r["metrics"]
    keys = [(k, m[f"{k}_ms"]) for k in ("p50", "p95", "p99", "mean") if m.get(f"{k}_ms") is not None]
    fig, ax = plt().subplots(figsize=(6, 3.2))
    vbars(ax, [k for k, _ in keys], {"latency": [v for _, v in keys]}, fmt=lambda v: f"{v:.2f} ms")
    ax.set_ylabel("ms per top-10 query")
    ax.set_title(f"Person search over {m.get('n_vectors'):,} embeddings (pgvector HNSW)")
    ax.text(0.99, 0.97, f"target: p95 < {r['primary']['target']} ms (off scale)\nrecall@10 vs exact "
            f"{m.get('recall_at_10_vs_exact')}", transform=ax.transAxes, ha="right", va="top", fontsize=7.5)
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    save(fig, "search_latency.png", "search_latency",
         f"Top-{m.get('k')} cosine search latency over {m.get('n_vectors')} 512-d embeddings with {m.get('index')}, "
         f"ef_search {m.get('ef_search')}. Measured on {device_label(r)} (database on the same machine).",
         "eval/results/search_latency.json", [f"{k} {v} ms" for k, v in keys]
         + [f"recall@10 {m.get('recall_at_10_vs_exact')}"], ds_name(r) + " | measured on " + device_label(r))


def chart_synopsis() -> None:
    r = load("synopsis")
    if not r:
        GAPS["synopsis"] = "eval/results/synopsis.json missing (run eval/synopsis.py on the PC)"
        return
    m = r["metrics"]
    if m.get("original_duration") is None or m.get("synopsis_duration") is None:
        GAPS["synopsis"] = "synopsis.json has no durations"
        return
    fig, ax = plt().subplots(figsize=(6, 2.4))
    hbars(ax, ["original", "synopsis"], {"minutes": [m["original_duration"] / 60, m["synopsis_duration"] / 60]},
          fmt=lambda v: f"{v:.2f} min")
    ax.set_title(f"Video synopsis: {m.get('compression_ratio')}x compression, "
                 f"{m.get('tubes_preserved_pct')}% of activity tubes kept")
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    save(fig, "synopsis_compression.png", "synopsis",
         "Duration of the source recording and of its synopsis video; the criterion asks for >= 10x with every "
         "event kept.", "eval/results/synopsis.json",
         [f"original {m['original_duration']} s, synopsis {m['synopsis_duration']} s",
          f"compression {m.get('compression_ratio')}x", f"tubes kept {m.get('tubes_kept')}, preserved "
          f"{m.get('tubes_preserved')}, dropped {m.get('tubes_dropped')}"], ds_name(r) + " | measured on " + device_label(r))


def chart_adversarial() -> None:
    r = load("adversarial")
    if not r:
        GAPS["adversarial"] = "eval/results/adversarial.json missing (run eval/adversarial.py on the PC)"
        return
    m = r["metrics"]
    conds = [c for c in ("clean", "fgsm", "pgd") if (m.get(c) or {}).get("ap50") is not None]
    series = {"no defence": [m[c]["ap50"] for c in conds]}
    for name, res in (m.get("defences") or {}).items():
        series[name.replace("_", " ")] = [(res.get(c) or {}).get("ap50") for c in conds]
    series = dict(list(series.items())[:3])
    fig, ax = plt().subplots(figsize=(6.5, 3.4))
    vbars(ax, [c.upper() if c != "clean" else "clean" for c in conds], series, ymax=1.1)
    eps = (m.get("params") or {}).get("eps_255")
    ax.set_title(f"Detector under attack (L-inf eps {eps}/255)", pad=18)
    ax.set_ylabel("person AP@0.5")
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    save(fig, "adversarial_ap.png", "adversarial",
         "Person AP@0.5 of the deployed detector on clean, FGSM and PGD images (ART, white-box), without and with "
         f"input defences. Measured on {device_label(r)}.", "eval/results/adversarial.json",
         [f"{s}: " + ", ".join(f"{c} {v}" for c, v in zip(conds, vals)) for s, vals in series.items()]
         + [f"PGD retention {m.get('detection_retention_pgd')}"], ds_name(r) + " | measured on " + device_label(r))


# ---- B. qualitative panels --------------------------------------------------------------------------
def _iou(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    if not len(a) or not len(b):
        return np.zeros((len(a), len(b)))
    x1 = np.maximum(a[:, None, 0], b[None, :, 0])
    y1 = np.maximum(a[:, None, 1], b[None, :, 1])
    x2 = np.minimum(a[:, None, 2], b[None, :, 2])
    y2 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    area = lambda z: (z[:, 2] - z[:, 0]) * (z[:, 3] - z[:, 1])  # noqa: E731
    return inter / np.maximum(area(a)[:, None] + area(b)[None, :] - inter, 1e-9)


def _match(pred: np.ndarray, gt: np.ndarray, thr: float = 0.5) -> tuple[list[int], list[int], list[int]]:
    """Greedy IoU match. Returns (matched pred idx, unmatched pred idx, unmatched gt idx)."""
    iou = _iou(pred, gt)
    used_p, used_g = set(), set()
    for k in np.argsort(-iou, axis=None):
        i, j = np.unravel_index(k, iou.shape)
        if iou[i, j] < thr:
            break
        if i not in used_p and j not in used_g:
            used_p.add(int(i))
            used_g.add(int(j))
    return sorted(used_p), [i for i in range(len(pred)) if i not in used_p], [j for j in range(len(gt)) if j not in used_g]


def _box(ax, b, color, ls="-", lw=1.4, label=None):
    ax.add_patch(plt().Rectangle((b[0], b[1]), b[2] - b[0], b[3] - b[1], fill=False, edgecolor=color, lw=lw, ls=ls))
    if label:
        ax.text(b[0], b[1] - 2, label, fontsize=6, color=INK, va="bottom",
                bbox={"boxstyle": "square,pad=0.1", "fc": SURFACE, "ec": "none", "alpha": 0.85})


def _panel(ax, rgb, title: str, failure: bool) -> None:
    ax.imshow(rgb)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.grid(False)
    for s in ax.spines.values():
        s.set_visible(False)
    tag = "FAILURE" if failure else "OK"
    ax.set_title(f"{tag} - {title}", fontsize=7.5, loc="left", fontweight="bold" if failure else "normal",
                 color=INK)
    if failure:  # status colour paired with the FAILURE text label
        ax.add_patch(plt().Rectangle((0, 0), 1, 1, transform=ax.transAxes, fill=False, edgecolor=CRITICAL, lw=2.5))


def _legend(fig, items: list[tuple[str, str, str]]) -> None:
    from matplotlib.lines import Line2D  # noqa: PLC0415

    fig.legend([Line2D([0], [0], color=c, ls=ls, lw=1.6) for _, c, ls in items], [n for n, _, _ in items],
               loc="upper right", ncol=len(items), fontsize=7, bbox_to_anchor=(0.995, 0.995))


def _rgb(bgr):
    return bgr[:, :, ::-1]


def _person_model(device: str):
    from app.config import settings  # noqa: PLC0415
    from ultralytics import YOLO  # noqa: PLC0415

    path, imgsz = settings.person_model_for(device)
    return YOLO(path), imgsz, Path(path).name


def q_detection(device: str) -> None:
    import cv2  # noqa: PLC0415

    root = DATASETS_DIR / "coco_val2017_person"
    ann = json.loads((root / "instances_val2017_mini500.json").read_text())
    pid = next(c["id"] for c in ann["categories"] if c["name"] == "person")
    files = {im["id"]: im["file_name"] for im in ann["images"]}
    gts: dict[int, list] = {}
    for a in ann["annotations"]:
        if a["category_id"] == pid:
            x, y, w, h = a["bbox"]
            gts.setdefault(a["image_id"], []).append(([x, y, x + w, y + h], a.get("iscrowd", 0)))
    ids = [i for i in sorted(gts) if (root / "images" / files[i]).is_file()]
    if not ids:
        raise RuntimeError(f"no COCO person images in {root / 'images'}")
    model, imgsz, mname = _person_model(device)
    panels, numbers = [], []
    for k, i in enumerate(ids):
        bgr = cv2.imread(str(root / "images" / files[i]))
        r = model.predict(bgr, imgsz=imgsz, conf=0.5, iou=0.6, classes=[0], device=device, verbose=False)[0]
        pred = r.boxes.xyxy.cpu().numpy()
        g = np.array([b for b, c in gts[i] if not c and b[3] - b[1] >= 24], float).reshape(-1, 4)
        tp, fp, fn = _match(pred, g)
        f1 = 2 * len(tp) / max(2 * len(tp) + len(fp) + len(fn), 1)
        p = {"id": i, "rgb": _rgb(bgr), "pred": pred, "gt": g, "tp": tp, "fp": fp, "fn": fn, "fail": f1 < 0.8}
        if k < 4:
            panels.append(p)
        elif not any(q["fail"] for q in panels) and p["fail"]:
            panels[-1] = p
            break
        if k >= 3 and any(q["fail"] for q in panels):
            break
    fig, axes = plt().subplots(2, 2, figsize=(8, 6.6))
    for ax, p in zip(axes.ravel(), panels):
        _panel(ax, p["rgb"], f"COCO {p['id']}: TP {len(p['tp'])}, FP {len(p['fp'])}, missed {len(p['fn'])}", p["fail"])
        for b in p["gt"]:
            _box(ax, b, SURFACE, ls="--", lw=1)
        for j in p["tp"]:
            _box(ax, p["pred"][j], CAT[0])
        for j in p["fp"]:
            _box(ax, p["pred"][j], CAT[1])
        for j in p["fn"]:
            _box(ax, p["gt"][j], CAT[4], ls="--", lw=1.6)
        numbers.append(f"image {p['id']}: TP {len(p['tp'])}, FP {len(p['fp'])}, missed {len(p['fn'])}"
                       + (" (FAILURE)" if p["fail"] else ""))
    for ax in axes.ravel()[len(panels):]:
        ax.axis("off")
    _legend(fig, [("detection (matched)", CAT[0], "-"), ("false positive", CAT[1], "-"),
                  ("ground truth", "#9a9893", "--"), ("missed person", CAT[4], "--")])
    fig.tight_layout(rect=(0, 0.03, 1, 0.95))
    save(fig, "qual_detection_coco.png", "q_detection",
         f"Person detections of {mname} @{imgsz} (conf 0.5) on the first COCO val2017 person images by id; IoU 0.5 "
         "matching, GT people under 24 px tall ignored. A panel is a FAILURE when its F1 is below 0.8.",
         "inference at figure time", numbers, "COCO val2017 person subset (official annotations)")


def q_tracking(device: str) -> None:
    import caviar  # noqa: PLC0415
    from app.ai.tracker import PersonTracker  # noqa: PLC0415
    from app.config import settings  # noqa: PLC0415

    it = json.loads((MANIFESTS_DIR / "caviar_track.json").read_text())["items"][0]
    seq, view, step = it["seq"], it["view"], it["step"]
    gt = caviar.load_gt(seq, view)
    model, imgsz, mname = _person_model(device)
    tracker = PersonTracker(fps=25 / step)
    frames, last_tid, failure = {}, {}, None
    for idx, frame in caviar.iter_frames(caviar.video_path(seq, view), set(range(0, it["frames"], step))):
        r = model.predict(frame, imgsz=imgsz, conf=settings.PERSON_CONF, iou=0.6, classes=[0], device=device,
                          verbose=False)[0]
        out = tracker.update(r.boxes.xyxy.cpu().numpy(), r.boxes.conf.cpu().numpy(), idx / 25)
        tr = [(t.track_id, np.array([t.bbox_xywh[0], t.bbox_xywh[1], t.bbox_xywh[0] + t.bbox_xywh[2],
                                     t.bbox_xywh[1] + t.bbox_xywh[3]])) for t in out.confirmed]
        g = gt.get(idx, np.zeros((0, 5)))
        g = g[(g[:, 4] - g[:, 2]) >= 24] if len(g) else g
        frames[idx] = (frame, tr, g)
        if failure is None and len(g):
            tb = np.array([b for _, b in tr]).reshape(-1, 4)
            m, _, fn = _match(tb, g[:, 1:5])
            iou = _iou(tb, g[:, 1:5])
            for pi in m:
                gj = int(np.argmax(iou[pi]))
                gid, tid = int(g[gj, 0]), tr[pi][0]
                if gid in last_tid and last_tid[gid] != tid:
                    failure = (idx, f"ID switch: GT person {gid} was track {last_tid[gid]}, now {tid}")
                last_tid[gid] = tid
            if failure is None and fn and idx > 10 * step:
                failure = failure or (idx, f"missed: {len(fn)} GT person(s) without a confirmed track")
    with_people = [i for i in sorted(frames) if len(frames[i][2])]
    chosen = [with_people[int(k)] for k in np.linspace(0, len(with_people) - 1, 3 if failure else 4)]
    fig, axes = plt().subplots(1, 4, figsize=(11, 2.9))
    numbers = []
    for ax, idx in zip(axes, chosen + ([failure[0]] if failure else [])):
        frame, tr, g = frames[idx]
        is_fail = bool(failure) and idx == failure[0] and ax is axes[-1]
        _panel(ax, _rgb(frame), f"frame {idx}" + (f": {failure[1]}" if is_fail else f", {len(tr)} tracks"), is_fail)
        for b in g[:, 1:5] if len(g) else []:
            _box(ax, b, SURFACE, ls="--", lw=0.9)
        for tid, b in tr:
            _box(ax, b, CAT[0], label=f"ID {tid}")
        numbers.append(f"frame {idx}: track ids {[t for t, _ in tr]}; GT ids {[int(x) for x in g[:, 0]] if len(g) else []}"
                       + (f" - FAILURE {failure[1]}" if is_fail else ""))
    if not failure:
        GAPS["q_tracking_failure"] = f"no ID switch or miss found on {seq}/{view}"
    _legend(fig, [("confirmed track", CAT[0], "-"), ("ground truth", "#9a9893", "--")])
    fig.tight_layout(rect=(0, 0.06, 1, 0.92))
    save(fig, "qual_tracking_caviar.png", "q_tracking",
         f"Confirmed track IDs of the shipped tracker ({mname} @{imgsz}, conf {settings.PERSON_CONF}) on CAVIAR "
         f"{seq} ({view}), every {step}nd frame; three evenly spaced frames and the first ID switch (or miss) found.",
         "inference at figure time", numbers, f"CAVIAR {seq} {view} (EC IST CAVIAR project GT)")


def q_reid(device: str) -> None:
    import cv2  # noqa: PLC0415
    from scipy.optimize import linear_sum_assignment  # noqa: PLC0415

    sys.path.insert(0, str(BACKEND / "scripts"))
    from train_reid import HOLDOUT, caviar_crops  # noqa: PLC0415

    from app.ai.reid import PersonReID  # noqa: PLC0415
    from app.config import settings  # noqa: PLC0415

    reid = PersonReID(settings.REID_MODEL, weights=str(WEIGHTS_DIR / settings.REID_WEIGHTS_PATH), device=device)
    if not reid.is_loaded:
        raise RuntimeError(f"Re-ID model not loaded: {reid.error}")
    T = settings.REID_T_MATCH
    crops = caviar_crops()
    seq = next((s for s in sorted(HOLDOUT) if len({i.split("/")[1] for _, i, _ in crops.get(s, [])}) == 2), None)
    if seq is None:
        raise RuntimeError("no hold-out sequence with crops from both views")
    rows = crops[seq]
    by_id: dict[str, list] = {}
    for p, i, f in rows:
        by_id.setdefault(i, []).append((p, f))
    imgs = {p: cv2.imread(str(p)) for p, _, _ in rows}
    feats = dict(zip([p for p, _, _ in rows], reid.extract_batch([imgs[p] for p, _, _ in rows])))

    def tracklet(i):  # mean of the 5 largest crops, as the coordinator does with quality crops
        ps = sorted(by_id[i], key=lambda pf: -imgs[pf[0]].size)[:5]
        v = np.mean([feats[p] for p, _ in ps], 0)
        return v / np.linalg.norm(v), ps[0][0]

    v1 = [i for i in by_id if "/cam1" in i]
    v2 = [i for i in by_id if "/cam2" in i]
    t1 = {i: tracklet(i) for i in v1}
    t2 = {i: tracklet(i) for i in v2}
    S = np.array([[t1[a][0] @ t2[b][0] for b in v2] for a in v1])
    ri, ci = linear_sum_assignment(-S)
    pairs = sorted(((S[a, b], v1[a], v2[b]) for a, b in zip(ri, ci)), reverse=True)[:3]
    out = []  # (left path, right path, text, failure)
    for s, a, b in pairs:
        same = s >= T
        out.append((t1[a][1], t2[b][1], f"view 1 {a.split('/')[-1]} vs view 2 {b.split('/')[-1]}: sim {s:.2f} "
                    + (f">= {T} -> same global ID" if same else f"< {T} -> different global IDs"), False))
    # failure candidates with GT (within one view, ids are known): hardest negative and hardest positive
    best_neg, worst_pos = None, None
    for s_ in sorted(HOLDOUT):
        rs = crops.get(s_, [])
        if not rs:
            continue
        missing = [p for p, _, _ in rs if p not in feats]
        if missing:
            for p, e in zip(missing, reid.extract_batch([cv2.imread(str(p)) for p in missing])):
                feats[p] = e
        E = np.stack([feats[p] for p, _, _ in rs])
        ident = np.array([i for _, i, _ in rs])
        fr = np.array([f for _, _, f in rs])
        view = np.array([i.split("/")[1] for i in ident])
        Sm = E @ E.T
        neg = (ident[:, None] != ident[None, :]) & (view[:, None] == view[None, :])
        pos = (ident[:, None] == ident[None, :]) & (np.abs(fr[:, None] - fr[None, :]) >= 75)
        if neg.any():
            k = np.argmax(np.where(neg, Sm, -2))
            a, b = np.unravel_index(k, Sm.shape)
            if best_neg is None or Sm[a, b] > best_neg[0]:
                best_neg = (Sm[a, b], rs[a][0], rs[b][0], ident[a], ident[b])
        if pos.any():
            k = np.argmin(np.where(pos, Sm, 2))
            a, b = np.unravel_index(k, Sm.shape)
            if worst_pos is None or Sm[a, b] < worst_pos[0]:
                worst_pos = (Sm[a, b], rs[a][0], rs[b][0], ident[a], ident[b])
    if best_neg:
        s, a, b, ia, ib = best_neg
        out.append((a, b, f"different GT people, same view ({ia} vs {ib}): sim {s:.2f}"
                    + (f" >= {T} -> would merge" if s >= T else f" < {T} -> kept apart"), s >= T))
    if worst_pos:
        s, a, b, ia, _ = worst_pos
        out.append((a, b, f"same GT person {ia}, >= 3 s apart: sim {s:.2f}"
                    + (f" < {T} -> split into two IDs" if s < T else f" >= {T} -> matched"), s < T))
    if not any(f for *_, f in out):
        GAPS["q_reid_failure"] = "no false merge or split found on the hold-out sequences"
    fig, axes = plt().subplots(len(out), 3, figsize=(7.5, 1.75 * len(out)), gridspec_kw={"width_ratios": [1, 1, 5]})
    numbers = []
    for (a, b, text, fail), (x1, x2, xt) in zip(out, np.atleast_2d(axes)):
        for ax, p in ((x1, a), (x2, b)):
            ax.imshow(_rgb(cv2.imread(str(p))))
            ax.set_xticks([])
            ax.set_yticks([])
            ax.grid(False)
            for sp in ax.spines.values():
                sp.set_edgecolor(CRITICAL if fail else GRID)
                sp.set_linewidth(2.5 if fail else 0.6)
        xt.axis("off")
        xt.text(0.02, 0.5, ("FAILURE - " if fail else "") + text, va="center", fontsize=8,
                fontweight="bold" if fail else "normal", wrap=True)
        numbers.append(("FAILURE: " if fail else "") + text)
    fig.suptitle(f"Re-ID on held-out CAVIAR {seq}: view 1 (corridor) vs view 2 (front)", x=0.01, ha="left",
                 fontsize=10, fontweight="bold")
    fig.tight_layout(rect=(0, 0.04, 1, 0.95))
    save(fig, "qual_reid_crossview.png", "q_reid",
         f"Top rows: tracklet embeddings (mean of 5 crops) of {seq} matched across the two views with the Hungarian "
         f"algorithm and REID_T_MATCH {T}; CAVIAR GT does not link ids across views, so these rows are judged by eye. "
         "Bottom rows use GT within one view: the most similar pair of different people and the least similar pair "
         "of the same person over all hold-out sequences.", "inference at figure time", numbers,
         "CAVIAR hold-out sequences (never used for Re-ID training)")


def q_crowd(device: str) -> None:
    import cv2  # noqa: PLC0415
    import scipy.io as sio  # noqa: PLC0415
    from crowd import MALL, head_points  # noqa: PLC0415

    from app.ai.crowd_density import CrowdDensityEstimator  # noqa: PLC0415

    res = load("crowd")
    cm = (res or {}).get("metrics", {})
    conf = cm.get("count_conf", 0.5)
    from ultralytics import YOLO  # noqa: PLC0415

    if res:
        name, imgsz = cm["primary"]["model"], cm["primary"]["imgsz"]
        model = YOLO(str(WEIGHTS_DIR / name))
    else:
        model, imgsz, name = _person_model(device)
    items = json.loads((MANIFESTS_DIR / "mall_crowd.json").read_text())["items"]
    mask = sio.loadmat(MALL / "perspective_roi.mat")["roi"][0, 0]["mask"] > 0
    locs = sio.loadmat(MALL / "mall_gt.mat")["frame"][0]
    est = CrowdDensityEstimator()
    zone = est.configure_zone("figure", camera_id=0, max_capacity=cm.get("capacity", 50))
    cand = []
    for k in np.linspace(0, len(items) - 1, 6).astype(int):
        it = items[k]
        bgr = cv2.imread(str(MALL / "frames" / it["frame"]))
        r = model.predict(bgr, imgsz=imgsz, conf=conf, iou=0.6, classes=[0], device=device, verbose=False, max_det=300)[0]
        hp = head_points(r.boxes.xyxy.cpu().numpy())
        xs = np.clip(hp[:, 0].round().astype(int), 0, mask.shape[1] - 1)
        ys = np.clip(hp[:, 1].round().astype(int), 0, mask.shape[0] - 1)
        inside = mask[ys, xs]
        n = int(inside.sum())
        lv_p, lv_g = est._classify(n, zone), est._classify(it["count"], zone)
        fno = int(it["frame"][4:10])
        cand.append({"frame": it["frame"], "rgb": _rgb(bgr), "pred": n, "gt": it["count"], "hp": hp[inside],
                     "gt_pts": locs[fno - 1][0, 0]["loc"], "lv": (getattr(lv_p, "value", lv_p), getattr(lv_g, "value", lv_g)),
                     "fail": lv_p != lv_g})
    shown = cand[:3] + [max(cand[3:], key=lambda c: abs(c["pred"] - c["gt"]))]
    fig, axes = plt().subplots(2, 2, figsize=(8.4, 6.6))
    numbers = []
    for ax, c in zip(axes.ravel(), shown):
        _panel(ax, c["rgb"], f"{c['frame']}: predicted {c['pred']} vs GT {c['gt']} (level {c['lv'][0]} vs {c['lv'][1]})",
               c["fail"])
        ax.contour(mask, levels=[0.5], colors=[SURFACE], linewidths=0.8)
        ax.scatter(c["gt_pts"][:, 0], c["gt_pts"][:, 1], s=9, facecolors="none", edgecolors=SURFACE, linewidths=0.8)
        ax.scatter(c["hp"][:, 0], c["hp"][:, 1], s=10, c=CAT[1], marker="x", linewidths=1)
        numbers.append(f"{c['frame']}: predicted {c['pred']}, GT {c['gt']}, level {c['lv'][0]} vs {c['lv'][1]}"
                       + (" (FAILURE)" if c["fail"] else ""))
    from matplotlib.lines import Line2D  # noqa: PLC0415

    fig.legend([Line2D([], [], ls="", marker="o", mfc="none", mec="#9a9893"), Line2D([], [], ls="", marker="x", mec=CAT[1])],
               ["GT head (annotation)", "counted detection (head point)"], loc="upper right", ncol=2, fontsize=7)
    fig.tight_layout(rect=(0, 0.03, 1, 0.95))
    save(fig, "qual_crowd_mall.png", "q_crowd",
         f"Counted people ({name} @{imgsz}, conf {conf}, head point inside the ROI outline) vs annotated heads on Mall. "
         "Three evenly spaced frames of the frozen sample plus the worst of three more; a FAILURE is a wrong crowd level.",
         "inference at figure time", numbers, "Mall dataset (Chen et al. 2012)")


def q_fire(device: str) -> None:
    import cv2  # noqa: PLC0415
    from fire import DFIRE_DIR, SHIPPED_CONF, parse_label  # noqa: PLC0415
    from ultralytics import YOLO  # noqa: PLC0415

    items = json.loads((MANIFESTS_DIR / "dfire_test.json").read_text())["items"]
    model = YOLO(str(WEIGHTS_DIR / "fire-smoke.pt"))
    names = {i: n.lower() for i, n in model.names.items()}

    def run(it):
        bgr = cv2.imread(str(DFIRE_DIR / "images" / it["file"]))
        h, w = bgr.shape[:2]
        gt = parse_label(it["label"], w, h)
        r = model.predict(bgr, imgsz=640, conf=SHIPPED_CONF, device=device, verbose=False)[0]
        pred = [(names[int(c)], b) for c, b in zip(r.boxes.cls.cpu().numpy(), r.boxes.xyxy.cpu().numpy())]
        truth, said = bool(gt), bool(pred)
        kind = "fire" if any(c == "fire" for c, _ in gt) else ("smoke" if gt else "negative")
        return {"file": it["file"], "rgb": _rgb(bgr), "gt": gt, "pred": pred, "kind": kind, "fail": truth != said}

    want = {"fire": 2, "smoke": 1, "negative": 1}
    shown = []
    for it in items:
        lab = it["label"].strip()
        kind = "negative" if not lab else ("fire" if any(ln.split()[0] in ("1", "1.0") for ln in lab.splitlines()) else "smoke")
        if want.get(kind, 0) > 0:
            want[kind] -= 1
            shown.append(run(it))
        if not any(want.values()):
            break
    if not any(p["fail"] for p in shown):
        for it in items[len(shown):len(shown) + 80]:
            p = run(it)
            if p["fail"]:
                shown[-1] = p
                break
        else:
            GAPS["q_fire_failure"] = "no image-level failure in the first 80 extra D-Fire images"
    fig, axes = plt().subplots(2, 2, figsize=(8, 6.6))
    numbers = []
    for ax, p in zip(axes.ravel(), shown):
        verdict = "alarm" if p["pred"] else "no alarm"
        _panel(ax, p["rgb"], f"{p['kind']} image -> {verdict} ({len(p['pred'])} boxes)", p["fail"])
        for c, b in p["gt"]:
            _box(ax, b, SURFACE, ls="--", lw=1)
        for c, b in p["pred"]:
            _box(ax, b, CAT[1] if c == "fire" else CAT[0], label=c)
        numbers.append(f"{p['file']}: GT {p['kind']}, predicted {[c for c, _ in p['pred']]}" + (" (FAILURE)" if p["fail"] else ""))
    _legend(fig, [("fire", CAT[1], "-"), ("smoke", CAT[0], "-"), ("ground truth", "#9a9893", "--")])
    fig.tight_layout(rect=(0, 0.03, 1, 0.95))
    save(fig, "qual_fire_dfire.png", "q_fire",
         f"fire-smoke.pt at the shipped confidence {SHIPPED_CONF} on the first fire, smoke-only and negative images of "
         "the frozen D-Fire test subset; an image is a FAILURE when the alarm decision disagrees with the label.",
         "inference at figure time", numbers, "D-Fire test split (de Venancio et al. 2022)")


def q_emotion(device: str) -> None:
    import cv2  # noqa: PLC0415
    import onnxruntime as ort  # noqa: PLC0415
    from emotion import FER_CLASSES, FER_TO_MODEL, read_shards  # noqa: PLC0415

    from app.ai.emotion import EmotionRecognizer  # noqa: PLC0415

    sess = ort.InferenceSession(str(WEIGHTS_DIR / "enet_b0_8_best_afew.onnx"), providers=["CPUExecutionProvider"])
    name = sess.get_inputs()[0].name
    shown, extra = [], None
    for k, (_, key, label, data) in enumerate(read_shards()):
        gray = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)
        bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        logits = sess.run(None, {name: EmotionRecognizer._preprocess([bgr])})[0][0]
        pred = int(np.argmax(logits[FER_TO_MODEL]))
        p = {"key": key, "rgb": _rgb(bgr), "true": FER_CLASSES[label], "pred": FER_CLASSES[pred], "fail": pred != label}
        if k < 8:
            shown.append(p)
        elif p["fail"]:
            extra = p
            break
        if k >= 7 and any(q["fail"] for q in shown):
            break
    if extra and not any(q["fail"] for q in shown):
        shown[-1] = extra
    fig, axes = plt().subplots(2, 4, figsize=(8, 4.6))
    for ax, p in zip(axes.ravel(), shown):
        _panel(ax, p["rgb"], f"true {p['true']}\npred {p['pred']}", p["fail"])
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    save(fig, "qual_emotion_fer2013.png", "q_emotion",
         "The first eight FER-2013 test faces in the frozen order with the shipped model's 7-class prediction "
         "(AffectNet-trained, cross-dataset).", "inference at figure time",
         [f"{p['key']}: true {p['true']}, predicted {p['pred']}" + (" (FAILURE)" if p["fail"] else "") for p in shown],
         "FER-2013 public test split")


def q_demographics(device: str) -> None:
    import cv2  # noqa: PLC0415
    import onnxruntime as ort  # noqa: PLC0415
    import polars as pl  # noqa: PLC0415
    from demographics import GENDERS, PARQUET  # noqa: PLC0415

    from app.ai.demographics import AGE_BANDS, DemographicsClassifier  # noqa: PLC0415

    sess = ort.InferenceSession(str(WEIGHTS_DIR / "fairface_res34.onnx"), providers=["CPUExecutionProvider"])
    name = sess.get_inputs()[0].name
    df = pl.scan_parquet(PARQUET).select(["image", "age", "gender"]).head(40).collect()
    shown, extra = [], None
    for k, row in enumerate(df.iter_rows(named=True)):
        bgr = cv2.imdecode(np.frombuffer(row["image"]["bytes"], np.uint8), cv2.IMREAD_COLOR)
        g, a = sess.run(["gender", "age"], {name: DemographicsClassifier.preprocess([bgr])})
        gp, ap = int(g.argmax(1)[0]), int(a.argmax(1)[0])
        fail = gp != int(row["gender"]) or abs(ap - int(row["age"])) > 1
        p = {"row": k, "rgb": _rgb(bgr), "fail": fail,
             "text": f"true {GENDERS[int(row['gender'])]}, {AGE_BANDS[int(row['age'])]}\n"
                     f"pred {GENDERS[gp]}, {AGE_BANDS[ap]}"}
        if k < 8:
            shown.append(p)
        elif fail:
            extra = p
            break
        if k >= 7 and any(q["fail"] for q in shown):
            break
    if extra and not any(q["fail"] for q in shown):
        shown[-1] = extra
    fig, axes = plt().subplots(2, 4, figsize=(8, 4.8))
    for ax, p in zip(axes.ravel(), shown):
        _panel(ax, p["rgb"], p["text"], p["fail"])
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    r = load("demographics")
    gap = ((r or {}).get("metrics") or {}).get("max_gap_between_race_groups")
    save(fig, "qual_demographics_fairface.png", "q_demographics",
         "The first eight FairFace validation faces with predicted gender and age band; FAILURE = wrong gender or "
         "age off by more than one band. Bias caveat: accuracy differs between demographic groups"
         + (f" (max gap between race groups: {gap})" if gap else "")
         + "; the binary gender label is the dataset's and these estimates must not be used to decide anything "
           "about an individual.", "inference at figure time",
         [f"row {p['row']}: {p['text'].replace(chr(10), '; ')}" + (" (FAILURE)" if p["fail"] else "") for p in shown],
         "FairFace validation split (0.25 padding)")


def q_adversarial(device: str) -> None:
    import cv2  # noqa: PLC0415
    import torch  # noqa: PLC0415
    from art.attacks.evasion import FastGradientMethod, ProjectedGradientDescent  # noqa: PLC0415
    from art.estimators.object_detection import PyTorchYolo  # noqa: PLC0415
    from ultralytics import YOLO  # noqa: PLC0415

    from app.security.adversarial_eval import IMGSZ, _letterbox, ensure_person_subset  # noqa: PLC0415

    r = load("adversarial")
    mname = ((r or {}).get("model") or {}).get("name", "yolo11s.pt")
    eps, steps = 8 / 255, 10
    s = ensure_person_subset(1)[0]
    img, boxes, crowd = _letterbox(cv2.cvtColor(cv2.imread(s["path"]), cv2.COLOR_BGR2RGB), s["boxes"], s["crowd"])
    x = img.astype(np.float32).transpose(2, 0, 1)[None] / 255.0
    dev = "cuda" if device.startswith("cuda") else "cpu"
    net = YOLO(str(WEIGHTS_DIR / mname)).model.to(dev).float()
    for p in net.parameters():
        p.requires_grad_(False)
    est = PyTorchYolo(model=net, input_shape=(3, IMGSZ, IMGSZ), clip_values=(0.0, 1.0), channels_first=True,
                      attack_losses=("loss_total",), device_type="gpu" if dev == "cuda" else "cpu",
                      is_ultralytics=True, model_name=Path(mname).stem)
    orig_loss, wrapper = net.loss, est.model

    def loss_tuple(batch, preds=None):  # same adaptation as app.security.adversarial_eval
        loss, items = orig_loss(batch, preds)
        return loss, tuple(items.values()) if isinstance(items, dict) else items

    net.loss = loss_tuple
    orig_train = wrapper.train

    def train_bn_frozen(mode: bool = True):
        orig_train(mode)
        for m in wrapper.modules():
            if isinstance(m, torch.nn.modules.batchnorm._BatchNorm):
                m.eval()
        return wrapper

    wrapper.train = train_bn_frozen
    y = [{"boxes": torch.as_tensor(boxes, dtype=torch.float32), "labels": torch.zeros(len(boxes), dtype=torch.int64)}]
    q = lambda a: np.round(np.clip(a, 0, 1) * 255) / 255  # noqa: E731
    sets = {"clean": x,
            f"FGSM eps {eps * 255:.0f}/255": q(FastGradientMethod(est, norm=np.inf, eps=eps, batch_size=1).generate(x=x, y=y)),
            f"PGD eps {eps * 255:.0f}/255, {steps} steps": q(ProjectedGradientDescent(
                est, norm=np.inf, eps=eps, eps_step=max(eps / 4, 1 / 255), max_iter=steps, batch_size=1,
                verbose=False).generate(x=x, y=y))}
    yolo = YOLO(str(WEIGHTS_DIR / mname))
    g = np.asarray(boxes).reshape(-1, 4)
    g = g[(g[:, 3] - g[:, 1]) >= 24]
    fig, axes = plt().subplots(1, 3, figsize=(10.5, 3.9))
    numbers = []
    for ax, (title, a) in zip(axes, sets.items()):
        rgb = (a[0].transpose(1, 2, 0) * 255).round().astype(np.uint8)
        res = yolo.predict(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), imgsz=IMGSZ, conf=0.5, classes=[0], device=dev,
                           verbose=False)[0]
        pred = res.boxes.xyxy.cpu().numpy()
        tp, fp, fn = _match(pred, g)
        fail = title != "clean" and len(fn) > 0
        _panel(ax, rgb, f"{title}: {len(tp)}/{len(g)} people found, {len(fp)} false", fail)
        for b in g:
            _box(ax, b, SURFACE, ls="--", lw=1)
        for j in tp:
            _box(ax, pred[j], CAT[0])
        for j in fp:
            _box(ax, pred[j], CAT[1])
        numbers.append(f"{title}: TP {len(tp)}, FP {len(fp)}, missed {len(fn)} of {len(g)}" + (" (FAILURE)" if fail else ""))
    _legend(fig, [("detection (matched)", CAT[0], "-"), ("false positive", CAT[1], "-"), ("ground truth", "#9a9893", "--")])
    fig.tight_layout(rect=(0, 0.05, 1, 0.92))
    save(fig, "qual_adversarial.png", "q_adversarial",
         f"The same COCO image clean and after white-box FGSM / PGD attacks on {mname} (ART, L-inf, uint8-quantised), "
         "detections at conf 0.5. The perturbation is invisible but removes detections; attacked panels that lose a "
         "person are marked FAILURE.", "inference at figure time", numbers,
         f"COCO val2017 image {s.get('file', '')} (official annotations)")


# ---- FIGURES.md ------------------------------------------------------------------------------------
def write_index_and_md() -> None:
    old = json.loads(INDEX.read_text()) if INDEX.is_file() else {}
    merged = {k: v for k, v in old.get("entries", {}).items() if (OUT / k).is_file()}
    merged.update(ENTRIES)
    gaps = {**old.get("gaps", {}), **GAPS}
    for k in {e["group"] for e in merged.values()}:
        gaps.pop(k, None)
    INDEX.write_text(json.dumps({"entries": merged, "gaps": gaps}, indent=1))
    order = {g: i for i, g in enumerate(CHARTS + QUAL)}
    lines = ["# Report figures", "",
             "Generated by `eval/report_figures.py` from `eval/results/*.json` (charts) and a few seconds of inference "
             "on frozen dataset samples (qualitative panels). Do not edit by hand: rerun the script. Numbers below are "
             "exactly the ones drawn. Each image names its dataset in a footer; qualitative samples are the first "
             "items of the frozen manifest plus the first failure, never hand-picked.", "",
             "Regenerate (repo root): `backend/.venv/bin/python eval/report_figures.py` "
             "(Windows: `backend\\.venv\\Scripts\\python eval\\report_figures.py`).", ""]
    for name, e in sorted(merged.items(), key=lambda kv: (order.get(kv[1]["group"], 99), kv[0])):
        lines += [f"## {name}", "", f"![{name}]({e['file']})", "", f"**Caption.** {e['caption']}", "",
                  f"**Data.** {e['dataset']}. Source: `{e['source']}`. Generated {e['generated']}.", "",
                  "**Numbers shown.**", ""] + [f"- {n}" for n in e["numbers"]] + [""]
    missing = [g for g in CHARTS + QUAL if g not in {e["group"] for e in merged.values()}]
    if missing or gaps:
        lines += ["## Not generated yet", ""]
        for g in missing:
            lines.append(f"- `{g}`: {gaps.get(g, 'not run yet - run the evaluations and this script on the demo PC')}")
        for g, why in gaps.items():
            if g not in missing and g not in CHARTS + QUAL:
                lines.append(f"- `{g}`: {why}")
        lines.append("")
    MD.write_text("\n".join(lines))
    log(f"wrote {MD.relative_to(REPO)} ({len(merged)} figures, {len(missing)} not generated)")


def main() -> None:
    p = base_parser(__doc__.split("\n")[0])
    p.add_argument("--only", choices=["all", "charts", "qual"], default="all")
    p.add_argument("--figures", default="", help=f"comma list from {CHARTS + QUAL}")
    args = p.parse_args()
    cap_threads(args.threads)
    pick = {x.strip() for x in args.figures.split(",") if x.strip()}
    charts = [c for c in CHARTS if (not pick or c in pick)] if args.only in ("all", "charts") else []
    quals = [q for q in QUAL if (not pick or q in pick)] if args.only in ("all", "qual") else []
    for c in charts:
        try:
            globals()[f"chart_{c}"]()
        except Exception as e:  # one broken result file must not stop the others
            GAPS[c] = f"error: {e}"
            traceback.print_exc()
    if quals:
        with ml_lock():
            torch_threads(args.threads)
            device = resolve_device(args.device)
            for q in quals:
                t = time.time()
                try:
                    globals()[q](device)
                except Exception as e:
                    GAPS[q] = f"error: {type(e).__name__}: {e}"
                    traceback.print_exc()
                log(f"{q}: {time.time() - t:.1f}s")
    write_index_and_md()
    if GAPS:
        log("gaps: " + json.dumps(GAPS, indent=1))


if __name__ == "__main__":
    main()
