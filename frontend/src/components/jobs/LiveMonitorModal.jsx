/**
 * OmniTrack AI — Live Monitor modal
 *
 * Ported from the VisRax frontend's LiveMonitorModal: a full-screen portal with
 * the live stream on the left and KPI-specific stats on the right.
 *
 * The right pane differs per activity, because "what is interesting" does:
 *   line_passing  → running IN / OUT totals per line (crossings so far)
 *   roi_region    → what is in each zone in the CURRENT frame
 *   general OD    → what is in the whole frame right now
 *
 * The stream is OmniTrack's MJPEG endpoint rather than VisRax's polled JPEG
 * frames, so no frame-tick timer is needed — the <img> stays open.
 */

import React, { useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { Maximize2, Minimize2, X } from 'lucide-react';
import { liveStreamUrl, pipelineAPI } from '../../services/api';
import useLivePoll from '../../hooks/useLivePoll';

const LINE_ACTIVITIES = new Set(['line_passing', 'line_passing_count']);
const ROI_ACTIVITIES = new Set(['roi_region', 'roi_region_dependency_object_detection']);

/** Stable colour per region name, ported from VisRax. */
const REGION_PALETTE = [
    { fg: '#6172ff', bg: 'rgba(97, 114, 255, 0.13)', ring: 'rgba(97, 114, 255, 0.32)' },
    { fg: '#a855f7', bg: 'rgba(168, 85, 247, 0.13)', ring: 'rgba(168, 85, 247, 0.32)' },
    { fg: '#f97316', bg: 'rgba(249, 115, 22, 0.13)', ring: 'rgba(249, 115, 22, 0.32)' },
    { fg: '#10b981', bg: 'rgba(16, 185, 129, 0.13)', ring: 'rgba(16, 185, 129, 0.32)' },
    { fg: '#f43f5e', bg: 'rgba(244, 63, 94, 0.13)', ring: 'rgba(244, 63, 94, 0.32)' },
    { fg: '#eab308', bg: 'rgba(234, 179, 8, 0.13)', ring: 'rgba(234, 179, 8, 0.32)' },
    { fg: '#06b6d4', bg: 'rgba(6, 182, 212, 0.13)', ring: 'rgba(6, 182, 212, 0.32)' },
    { fg: '#14b8a6', bg: 'rgba(20, 184, 166, 0.13)', ring: 'rgba(20, 184, 166, 0.32)' },
];

function regionColor(region) {
    let hash = 0;
    for (let i = 0; i < region.length; i += 1) hash = (hash * 31 + region.charCodeAt(i)) >>> 0;
    return REGION_PALETTE[hash % REGION_PALETTE.length];
}

/* ── Right pane: line passing ───────────────────────────────────────── */

function LineCounts({ job }) {
    const byRegion = job.kpi?.by_region || {};
    const lines = Object.entries(byRegion);

    const totals = lines.reduce(
        (acc, [, c]) => ({
            in: acc.in + (c.in || 0),
            out: acc.out + (c.out || 0),
            left: acc.left + (c.left || 0),
            right: acc.right + (c.right || 0),
        }),
        { in: 0, out: 0, left: 0, right: 0 },
    );
    const passed = totals.in + totals.out + totals.left + totals.right;

    if (lines.length === 0) {
        return <p className="lmm-stats__hint">Waiting for crossing counts…</p>;
    }

    return (
        <div className="lmm-counts">
            <div className="lmm-counts__total">
                <span className="lmm-counts__value">{passed}</span>
                <span className="lmm-counts__label">Passed</span>
            </div>
            <div className="lmm-counts__row">
                <div className="lmm-counts__stat lmm-counts__stat--in">
                    <span className="lmm-counts__value">{totals.in}</span>
                    <span className="lmm-counts__label">In</span>
                </div>
                <div className="lmm-counts__stat lmm-counts__stat--out">
                    <span className="lmm-counts__value">{totals.out}</span>
                    <span className="lmm-counts__label">Out</span>
                </div>
            </div>

            {/* Each line only ever reports its configured direction, so the
                per-line breakdown is what actually explains the totals. */}
            <div className="detections-sections">
                {lines.map(([name, c]) => {
                    const colour = regionColor(name);
                    const rows = [
                        ['In', c.in], ['Out', c.out], ['Left', c.left], ['Right', c.right],
                    ].filter(([, v]) => v > 0);
                    return (
                        <section key={name} className="det-section">
                            <header className="det-section__head">
                                <span
                                    className="badge det-section__badge"
                                    style={{ color: colour.fg, background: colour.bg, borderColor: colour.ring }}
                                >
                                    {name}
                                </span>
                                <span className="det-section__meta">{c.tracks || 0} tracked</span>
                            </header>
                            <div className="det-section__body">
                                {rows.length === 0 ? (
                                    <div className="det-row">
                                        <span className="det-row__name">No crossings yet</span>
                                    </div>
                                ) : rows.map(([label, value]) => (
                                    <div className="det-row" key={label}>
                                        <span className="det-row__name">{label}</span>
                                        <span className="det-row__count" style={{ color: colour.fg }}>
                                            {value}
                                        </span>
                                    </div>
                                ))}
                            </div>
                        </section>
                    );
                })}
            </div>
        </div>
    );
}

/* ── Right pane: current-frame detections ───────────────────────────── */

/**
 * Groups this frame's detections. ROI jobs group by region so you can see what
 * is inside each zone; general object detection collapses to one "whole frame"
 * group, since it has no regions to attribute anything to.
 */
function FrameDetections({ detections, groupByRegion }) {
    const grouped = useMemo(() => {
        const map = new Map();
        for (const d of detections) {
            const region = groupByRegion
                ? (d.item_at_current_region || d.region_name || 'global')
                : 'whole frame';
            let entry = map.get(region);
            if (!entry) {
                entry = { region, classes: new Map() };
                map.set(region, entry);
            }
            const cls = d.class_name || 'unknown';
            entry.classes.set(cls, (entry.classes.get(cls) ?? 0) + 1);
        }
        return Array.from(map.values()).sort((a, b) => {
            if (a.region === 'global') return 1;
            if (b.region === 'global') return -1;
            return a.region.localeCompare(b.region);
        });
    }, [detections, groupByRegion]);

    if (detections.length === 0) {
        return <p className="lmm-stats__hint">No detections in the current frame yet.</p>;
    }

    return (
        <div className="detections-sections">
            {grouped.map(({ region, classes }) => {
                const colour = regionColor(region);
                const rows = Array.from(classes.entries()).sort((a, b) => b[1] - a[1]);
                const total = rows.reduce((sum, [, count]) => sum + count, 0);
                return (
                    <section key={region} className="det-section">
                        <header className="det-section__head">
                            <span
                                className="badge det-section__badge"
                                style={{ color: colour.fg, background: colour.bg, borderColor: colour.ring }}
                            >
                                {region}
                            </span>
                            <span className="det-section__meta">{total} in frame</span>
                        </header>
                        <div className="det-section__body">
                            {rows.map(([className, count]) => (
                                <div className="det-row" key={`${className}::${region}`}>
                                    <span className="det-row__name">{className}</span>
                                    <span className="det-row__count" style={{ color: colour.fg }}>
                                        {count}
                                    </span>
                                </div>
                            ))}
                        </div>
                    </section>
                );
            })}
        </div>
    );
}

/* ── Modal ──────────────────────────────────────────────────────────── */

export default function LiveMonitorModal({ open, onClose, job }) {
    const [loaded, setLoaded] = useState(false);
    const [error, setError] = useState(false);
    const [maximized, setMaximized] = useState(false);

    const isActive = Boolean(job?.connected);
    const isLine = LINE_ACTIVITIES.has(job?.activity_type);
    const isRoi = ROI_ACTIVITIES.has(job?.activity_type);

    // Current-frame detections, only for the activities that display them.
    const needsFrame = open && isActive && !isLine;
    const { data: frame } = useLivePoll(
        () => pipelineAPI.results(job?.camera_id),
        { intervalMs: 1000, enabled: needsFrame },
    );
    const detections = needsFrame ? (frame?.detections ?? []) : [];

    const src = useMemo(() => {
        if (!open || !job || !isActive) return null;
        const base = liveStreamUrl(job.camera_id);
        return `${base}${base.includes('?') ? '&' : '?'}t=${Date.now()}`;
    }, [open, job, isActive]);

    useEffect(() => {
        if (!open) return undefined;
        const onKey = (e) => {
            if (e.key !== 'Escape') return;
            if (maximized) setMaximized(false);
            else onClose();
        };
        window.addEventListener('keydown', onKey);
        const prev = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
        return () => {
            window.removeEventListener('keydown', onKey);
            document.body.style.overflow = prev;
        };
    }, [open, onClose, maximized]);

    useEffect(() => {
        if (open) {
            setLoaded(false);
            setError(false);
            setMaximized(false);
        }
    }, [open, job?.camera_id]);

    if (!open || !job) return null;

    return createPortal(
        <div
            className={`lmm-backdrop${maximized ? ' is-maximized' : ''}`}
            onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
        >
            <div
                className={`lmm${maximized ? ' is-maximized' : ''}`}
                role="dialog"
                aria-modal="true"
                aria-label="Live monitoring"
            >
                <div className="lmm__controls">
                    <button
                        type="button"
                        className="lmm__size"
                        onClick={() => setMaximized((value) => !value)}
                        aria-label={maximized ? 'Minimize live stream' : 'Maximize live stream'}
                        title={maximized ? 'Minimize live stream' : 'Maximize live stream'}
                    >
                        {maximized ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
                    </button>
                    <button type="button" className="lmm__close" onClick={onClose} aria-label="Close">
                        <X size={16} />
                    </button>
                </div>

                <div className="lmm__stage">
                    {/* Pane 1 — the live stream */}
                    <div className="lmm__stream">
                        <div className="live-monitor__frame lmm__frame">
                            {!isActive ? (
                                <div className="live-monitor__hint">
                                    This job is not running — start it to view the live stream.
                                </div>
                            ) : (
                                <>
                                    <img
                                        src={src ?? ''}
                                        alt={`Live inference feed for ${job.job_id}`}
                                        onLoad={() => { setLoaded(true); setError(false); }}
                                        onError={() => setError(true)}
                                    />
                                    {error ? (
                                        <div className="live-monitor__hint">
                                            Feed not ready yet — waiting for processed frames…
                                        </div>
                                    ) : !loaded ? (
                                        <div className="live-monitor__hint">Connecting to live stream…</div>
                                    ) : null}
                                </>
                            )}
                        </div>
                    </div>

                    {/* Pane 2 — activity-specific live stats */}
                    <aside className="lmm__side">
                        {!isActive ? (
                            <p className="lmm-stats__hint">This job is not running.</p>
                        ) : isLine ? (
                            <LineCounts job={job} />
                        ) : (
                            <FrameDetections detections={detections} groupByRegion={isRoi} />
                        )}
                    </aside>
                </div>
            </div>
        </div>,
        document.body,
    );
}
