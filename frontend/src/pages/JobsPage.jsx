/**
 * OmniTrack AI — Jobs
 *
 * Card layout ported from the VisRax frontend's CameraJobsPage: a compact
 * summary per job, with a Live stream button in the footer that opens the
 * full-screen Live Monitor modal rather than expanding a small inline box.
 */

import React, { useCallback, useMemo, useState } from 'react';
import {
    AlertTriangle, Circle, Cpu, Eye, Film, HardDrive, ImageIcon, Layers,
    MonitorPlay, Play, Plus, Square, Trash2, Video,
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
// Checkout lanes are regions as well; without this the card said "No regions"
// for a job that plainly had two.
const REGION_ACTIVITIES = new Set(['checkout_queue', 'checkout']);

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

function JobCard({ job, onDelete, isRecording, onToggleRecord, onToggleRun }) {
    const [liveOpen, setLiveOpen] = useState(false);
    const [busy, setBusy] = useState(false);
    const [recBusy, setRecBusy] = useState(false);

    const toggleRecord = async () => {
        setRecBusy(true);
        try { await onToggleRecord(job.camera_id, isRecording); } finally { setRecBusy(false); }
    };

    const [runBusy, setRunBusy] = useState(false);
    const toggleRun = async () => {
        setRunBusy(true);
        try {
            await onToggleRun(job.camera_id, job.connected);
        } finally {
            setRunBusy(false);
        }
    };

    const { data: alertData } = useLivePoll(
        () => detectionAPI.jobAlerts(job.camera_id, 10),
        { intervalMs: 10000 },
    );
    const alerts = alertData?.alerts ?? [];

    const usesRegions =
        LINE_ACTIVITIES.has(job.activity_type)
        || ROI_ACTIVITIES.has(job.activity_type)
        || REGION_ACTIVITIES.has(job.activity_type);

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
                    Idle — press Start to run it. Config, regions and history are kept.
                </p>
            )}

            <footer className="jcard__foot">
                <span className="jcard__id" title={job.job_id}>{job.job_id}</span>
                <span className="jcard__time">{headline(job)}</span>
                <button
                    type="button"
                    className={`jcard__run${job.connected ? ' is-on' : ''}`}
                    onClick={toggleRun}
                    disabled={runBusy}
                    title={job.connected
                        ? 'Stop the feed — the job and its history stay'
                        : 'Start this job. A clip replays from the beginning.'}
                >
                    {job.connected ? <Square size={12} aria-hidden /> : <Play size={12} aria-hidden />}
                    {runBusy ? 'Working…' : job.connected ? 'Stop' : 'Start'}
                </button>
                <button
                    type="button"
                    className="jcard__live"
                    onClick={() => setLiveOpen(true)}
                    disabled={!job.connected}
                    title={job.connected ? 'Watch the feed' : 'Start the job to watch it'}
                >
                    <MonitorPlay size={13} aria-hidden /> Live stream
                </button>
                <button
                    type="button"
                    className={`jcard__rec${isRecording ? ' is-on' : ''}`}
                    onClick={toggleRecord}
                    disabled={recBusy || !job.connected}
                    title={!job.connected
                        ? 'Start the job before recording'
                        : isRecording
                            ? 'Stop recording and save the clip'
                            : 'Record this feed with its detection overlay'}
                >
                    {isRecording ? <Square size={12} aria-hidden /> : <Circle size={12} aria-hidden />}
                    {recBusy ? 'Working…' : isRecording ? 'Stop recording' : 'Record'}
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
    // Which feeds are recording. Polled rather than held locally so the state
    // survives a reload and reflects recordings started elsewhere.
    const { data: recording, refresh: refreshRecording } = useLivePoll(
        () => detectionAPI.recordingStatus(),
        { intervalMs: 4000 },
    );

    const jobs = useMemo(() => data?.jobs ?? [], [data]);
    const recordingIds = useMemo(() => {
        const ids = recording?.recording_cameras ?? recording?.recording ?? [];
        return new Set((Array.isArray(ids) ? ids : []).map(Number));
    }, [recording]);

    const toggleRun = useCallback(async (cameraId, isRunning) => {
        if (isRunning) await detectionAPI.stop(cameraId);
        else await detectionAPI.startJob(cameraId);
        await refresh();
    }, [refresh]);

    const toggleRecord = useCallback(async (cameraId, isOn) => {
        if (isOn) await detectionAPI.recordingStop(cameraId);
        else await detectionAPI.recordingStart(cameraId);
        await refreshRecording();
    }, [refreshRecording]);
    const [createOpen, setCreateOpen] = useState(false);
    const atCapacity = jobs.length >= MAX_JOBS;
    const runningCount = jobs.filter((j) => j.connected).length;
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
                    <span className="jobs-page__eyebrow">
                        <Layers size={12} aria-hidden />
                        Video workspace
                    </span>
                    <h2 className="jobs-page__title">Registered Jobs</h2>
                    <p className="jobs-page__sub">
                        {runningCount > 0
                            ? `${runningCount} of ${jobs.length} ${jobs.length === 1 ? 'job is' : 'jobs are'} processing frames right now.`
                            : 'Each job runs one camera through its own model, tracker and regions.'}
                    </p>
                </div>
                {/* The empty state carries its own call to action, so showing this
                    one too would put two identical buttons on the same screen. */}
                {jobs.length > 0 && (
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
                )}
            </header>

            {/* One quiet line: no boxes, just the numbers and what they mean. */}
            <div className="jobs-summary">
                {/* With no jobs the empty state already states the limit. */}
                {jobs.length > 0 && (
                    <>
                        <span className={`jobs-summary__item${atCapacity ? ' is-warn' : ''}`}>
                            <Layers size={13} aria-hidden />
                            <b>{jobs.length} of {MAX_JOBS}</b>
                            <em>slots used</em>
                        </span>
                        <span className="jobs-summary__dot" aria-hidden />
                    </>
                )}
                <span className="jobs-summary__item">
                    <ImageIcon size={13} aria-hidden />
                    <b>{(artifacts?.snapshots ?? 0).toLocaleString()}</b>
                    <em>snapshot{artifacts?.snapshots === 1 ? '' : 's'}</em>
                </span>
                <span className="jobs-summary__dot" aria-hidden />
                <span className="jobs-summary__item">
                    <Film size={13} aria-hidden />
                    <b>{(artifacts?.clips ?? 0).toLocaleString()}</b>
                    <em>clip{artifacts?.clips === 1 ? '' : 's'}</em>
                </span>
                <span className="jobs-summary__dot" aria-hidden />
                <span
                    className={`jobs-summary__item${storageNearFull ? ' is-warn' : ''}`}
                    title={artifacts?.root}
                >
                    <HardDrive size={13} aria-hidden />
                    <b>{artifacts?.usage_mb ?? 0} MB</b>
                    <em>of {artifacts?.quota_mb ?? 0} used</em>
                </span>
            </div>

            {loading && jobs.length === 0 && <p className="jobs-page__empty">Loading jobs…</p>}

            {!loading && jobs.length === 0 && (
                <div className="jobs-page__empty-state">
                    <span className="jobs-empty__glyph"><Layers size={26} aria-hidden /></span>
                    <h3>No jobs yet</h3>
                    <p>
                        A job points one camera at one model and watches for a single
                        thing — products on a shelf, people crossing a line, or a region
                        filling up.
                    </p>

                    <ol className="jobs-empty__steps">
                        <li><b>Pick what to watch</b><span>Line crossing, ROI region, or general detection.</span></li>
                        <li><b>Choose a model</b><span>Any weights installed on the backend.</span></li>
                        <li><b>Connect a source</b><span>Upload a clip, or point it at an RTSP camera.</span></li>
                    </ol>

                    <button
                        type="button"
                        className="jobs-page__create jobs-page__create--lg"
                        onClick={() => setCreateOpen(true)}
                    >
                        <Plus size={16} /> Create your first job
                    </button>
                    <p className="jobs-page__cap-note">Up to {MAX_JOBS} jobs can run at once.</p>
                </div>
            )}

            <div className="jobs-grid">
                {jobs.map((job) => (
                    <JobCard
                        key={job.camera_id}
                        job={job}
                        onDelete={onDelete}
                        isRecording={recordingIds.has(Number(job.camera_id))}
                        onToggleRecord={toggleRecord}
                        onToggleRun={toggleRun}
                    />
                ))}
            </div>

            <AddJobModal open={createOpen} onClose={closeCreate} />
        </div>
    );
}
