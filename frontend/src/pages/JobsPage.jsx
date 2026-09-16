/**
 * OmniTrack AI — Jobs
 *
 * Card layout ported from the VisRax frontend's CameraJobsPage: a compact
 * summary per job, with a Live stream button in the footer that opens the
 * full-screen Live Monitor modal rather than expanding a small inline box.
 */

import React, { useCallback, useMemo, useState } from 'react';
import {
    AlertTriangle, Cpu, Eye, Film, HardDrive, ImageIcon, Layers,
    MonitorPlay, Plus, Trash2, Video,
} from 'lucide-react';
import { detectionAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import LiveMonitorModal from '../components/jobs/LiveMonitorModal';
import { AddJobModal } from '../components/jobs/AddJobModal';

const MAX_JOBS = 2;

const ACTIVITY_LABEL = {
    line_passing: 'Line passing count',
    line_passing_count: 'Line passing count',
    roi_region: 'ROI region',
    roi_region_dependency_object_detection: 'ROI region',
    general_object_detection: 'General object detection',
};

const LINE_ACTIVITIES = new Set(['line_passing', 'line_passing_count']);
const ROI_ACTIVITIES = new Set(['roi_region', 'roi_region_dependency_object_detection']);

function sourceLabel(job) {
    const src = (job.source || '').toLowerCase();
    if (src.startsWith('rtsp://')) return 'Live camera';
    if (/^\d{1,2}$/.test(src)) return 'Webcam';
    return 'Video file';
}

/** One-line headline so the card says something useful at a glance. */
function headline(job) {
    const kpi = job.kpi || {};
    if (LINE_ACTIVITIES.has(job.activity_type)) {
        const total = Object.values(kpi.by_region || {}).reduce(
            (a, c) => a + (c.in || 0) + (c.out || 0) + (c.left || 0) + (c.right || 0), 0,
        );
        return `${total} passed`;
    }
    if (ROI_ACTIVITIES.has(job.activity_type)) {
        const now = Object.values(kpi.occupancy || {}).reduce((a, n) => a + n, 0);
        return `${now} in zones`;
    }
    const current = Object.values(kpi.current || {}).reduce((a, n) => a + n, 0);
    return `${current} in frame`;
}

/* ── Card ───────────────────────────────────────────────────────────── */

function JobCard({ job, onDelete }) {
    const [liveOpen, setLiveOpen] = useState(false);
    const [busy, setBusy] = useState(false);

    const { data: alertData } = useLivePoll(
        () => detectionAPI.jobAlerts(job.camera_id, 10),
        { intervalMs: 10000 },
    );
    const alerts = alertData?.alerts ?? [];

    const usesRegions =
        LINE_ACTIVITIES.has(job.activity_type) || ROI_ACTIVITIES.has(job.activity_type);

    const regions = job.regions || [];
    const classes = job.classes || [];
    const visibleRegions = usesRegions ? regions.slice(0, 2) : [];
    const visibleClasses = classes.slice(0, usesRegions ? 3 : 5);
    const overflow =
        (usesRegions ? regions.length - visibleRegions.length : 0) +
        (classes.length - visibleClasses.length);

    const remove = async () => {
        setBusy(true);
        try { await onDelete(job.camera_id); } finally { setBusy(false); }
    };

    return (
        <article className={`jcard jcard--${job.connected ? 'running' : 'stopped'}`}>
            <header className="jcard__top">
                <span className="jcard__name" title={job.zone || String(job.camera_id)}>
                    {job.zone || `Camera ${job.camera_id}`}
                </span>
                <span className="jcard__status">{job.connected ? 'Running' : 'Idle'}</span>
                <button
                    type="button"
                    className="jcard__menu-btn"
                    onClick={remove}
                    disabled={busy}
                    aria-label="Delete job"
                    title="Stop this job and remove it"
                >
                    <Trash2 size={15} />
                </button>
            </header>

            <div className="jcard__kpi">
                <span className="jcard__kpi-icon" aria-hidden>
                    <Eye size={14} strokeWidth={2.2} />
                </span>
                <span className="jcard__kpi-body">
                    <span className="jcard__kpi-label">Watching for</span>
                    <span className="jcard__kpi-name">
                        {ACTIVITY_LABEL[job.activity_type] || job.activity_type || '—'}
                    </span>
                </span>
            </div>

            <div className="jcard__facts">
                <span className="jcard__fact">
                    <Video size={12} aria-hidden />
                    {sourceLabel(job)}
                </span>
                {job.model && (
                    <span className="jcard__fact" title={job.model}>
                        <Cpu size={12} aria-hidden />
                        {job.model}
                    </span>
                )}
                <span className="jcard__fact">
                    <Layers size={12} aria-hidden />
                    {usesRegions
                        ? `${regions.length} region${regions.length === 1 ? '' : 's'}`
                        : 'No regions'}
                </span>
            </div>

            {(visibleRegions.length > 0 || visibleClasses.length > 0) && (
                <div className="jcard__chips">
                    {visibleRegions.map((r, i) => (
                        <span key={`${r.name}-${i}`} className="jcard__chip jcard__chip--region">
                            {r.name}
                        </span>
                    ))}
                    {visibleClasses.map((c) => (
                        <span key={c} className="jcard__chip">{c}</span>
                    ))}
                    {overflow > 0 && <span className="jcard__chip jcard__chip--more">+{overflow}</span>}
                </div>
            )}

            {alerts.length > 0 && (
                <p className="jcard__alert">
                    <AlertTriangle size={12} aria-hidden /> {alerts[0].message}
                </p>
            )}

            {!job.connected && (
                <p className="jcard__idle-note">
                    Saved from a previous run — config and history intact, feed not running.
                </p>
            )}

            <footer className="jcard__foot">
                <span className="jcard__id" title={job.job_id}>{job.job_id}</span>
                <span className="jcard__time">{headline(job)}</span>
                <button type="button" className="jcard__live" onClick={() => setLiveOpen(true)}>
                    <MonitorPlay size={13} aria-hidden /> Live stream
                </button>
            </footer>

            <LiveMonitorModal open={liveOpen} onClose={() => setLiveOpen(false)} job={job} />
        </article>
    );
}

/* ── Page ───────────────────────────────────────────────────────────── */

export default function JobsPage() {
    const { data, loading, refresh } = useLivePoll(
        () => detectionAPI.jobs(),
        { intervalMs: 2000 },
    );
    const { data: artifacts } = useLivePoll(
        () => detectionAPI.jobArtifacts(),
        { intervalMs: 15000 },
    );

    const jobs = useMemo(() => data?.jobs ?? [], [data]);
    const [createOpen, setCreateOpen] = useState(false);
    const atCapacity = jobs.length >= MAX_JOBS;
    const storagePct = artifacts?.quota_mb
        ? Math.min(100, (artifacts.usage_mb / artifacts.quota_mb) * 100)
        : 0;
    const storageNearFull = storagePct >= 85;

    // Refresh straight after the modal closes so a new job appears without
    // waiting for the next poll tick.
    const closeCreate = useCallback(() => {
        setCreateOpen(false);
        refresh();
    }, [refresh]);

    const onDelete = useCallback(async (cameraId) => {
        await detectionAPI.deleteJob(cameraId);
        await refresh();
    }, [refresh]);

    return (
        <div className="jobs-page">
            <header className="jobs-page__head">
                <div className="jobs-page__lead">
                    <h2 className="jobs-page__title">Registered Jobs</h2>
                    <p className="jobs-page__sub">
                        Each job runs one camera through its own model, tracker and regions.
                    </p>
                </div>
                <button
                    type="button"
                    className="jobs-page__create"
                    onClick={() => setCreateOpen(true)}
                    disabled={atCapacity}
                    title={atCapacity
                        ? `Job limit reached (${MAX_JOBS}) — delete one first`
                        : 'Register a new job'}
                >
                    <Plus size={15} /> Create Job
                </button>
            </header>

            {/* Same stat-card language as the Live Overview KPI row. */}
            <div className="jobs-stats">
                <div className={`jobs-statcard${atCapacity ? ' is-warn' : ''}`}>
                    <span className="jobs-statcard__icon"><Layers size={16} aria-hidden /></span>
                    <span className="jobs-statcard__label">Job slots</span>
                    <span className="jobs-statcard__value">
                        {jobs.length}<i>/{MAX_JOBS}</i>
                    </span>
                    <span className="jobs-statcard__track" aria-hidden>
                        <span style={{ width: `${Math.min(100, (jobs.length / Math.max(1, MAX_JOBS)) * 100)}%` }} />
                    </span>
                </div>

                <div className="jobs-statcard">
                    <span className="jobs-statcard__icon"><ImageIcon size={16} aria-hidden /></span>
                    <span className="jobs-statcard__label">Snapshots</span>
                    <span className="jobs-statcard__value">
                        {(artifacts?.snapshots ?? 0).toLocaleString()}
                    </span>
                </div>

                <div className="jobs-statcard">
                    <span className="jobs-statcard__icon"><Film size={16} aria-hidden /></span>
                    <span className="jobs-statcard__label">Clips</span>
                    <span className="jobs-statcard__value">
                        {(artifacts?.clips ?? 0).toLocaleString()}
                    </span>
                </div>

                <div className={`jobs-statcard${storageNearFull ? ' is-warn' : ''}`} title={artifacts?.root}>
                    <span className="jobs-statcard__icon"><HardDrive size={16} aria-hidden /></span>
                    <span className="jobs-statcard__label">Storage</span>
                    <span className="jobs-statcard__value">
                        {artifacts?.usage_mb ?? 0}<i>/{artifacts?.quota_mb ?? 0} MB</i>
                    </span>
                    <span className="jobs-statcard__track" aria-hidden>
                        <span style={{ width: `${storagePct}%` }} />
                    </span>
                </div>
            </div>

            {loading && jobs.length === 0 && <p className="jobs-page__empty">Loading jobs…</p>}

            {!loading && jobs.length === 0 && (
                <div className="jobs-page__empty-state">
                    <Layers size={28} />
                    <h3>No jobs yet</h3>
                    <p>Register one to start watching a camera.</p>
                    <button
                        type="button"
                        className="jobs-page__create jobs-page__create--lg"
                        onClick={() => setCreateOpen(true)}
                    >
                        <Plus size={15} /> Create Job
                    </button>
                    <p className="jobs-page__cap-note">Up to {MAX_JOBS} jobs can run at once.</p>
                </div>
            )}

            <div className="jobs-grid">
                {jobs.map((job) => (
                    <JobCard key={job.camera_id} job={job} onDelete={onDelete} />
                ))}
            </div>

            <AddJobModal open={createOpen} onClose={closeCreate} />
        </div>
    );
}
