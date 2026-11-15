/**
 * OmniTrack AI — Detection & Live Surveillance
 * ────────────────────────────────────────────
 * • Start/stop the multi-camera pipeline.
 * • Add any source: RTSP URL, local file, uploaded clip, webcam index.
 * • Live MJPEG grid with per-camera detection + track counts.
 * • Per-camera recording (start/stop).
 * • Focus view: one feed full-screen with its live zone KPIs (Esc closes).
 */

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import {
    Plus, Upload, Circle, Square, RefreshCw, Focus, X, Flame,
} from 'lucide-react';
import {
    detectionAPI, pipelineAPI, footageAPI, modelAPI, zonesAPI, liveStreamUrl, camerasAPI,
} from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import useWebSocket from '../hooks/useWebSocket';
import CameraStream from '../components/CameraStream';
import CameraFrame from '../components/ops/CameraFrame';
import { Metric } from '../components/ui';
import withViewTransition, { prefersReducedMotion } from '../lib/viewTransition';

const title = (value) => String(value || 'unknown').replace(/[_-]/g, ' ').replace(/\b\w/g, (m) => m.toUpperCase());
const filename = (value) => String(value || '').split(/[\\/]/).pop();

export default function DetectionPage() {
    const [form, setForm] = useState({
        cameraId: '1',
        streamType: 'file',
        source: '',
        zone: 'entrance',
        fps: '30',
        enableReid: true,
        loop: false,
    });
    const [focusFeed, setFocusFeed] = useState(null);
    const [minimizedFeeds, setMinimizedFeeds] = useState(() => new Set());
    const [maximizedFeed, setMaximizedFeed] = useState(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState(null);
    const [notice, setNotice] = useState(null);

    const { data: pipeState, refresh: refreshPipeline } = useLivePoll(
        () => pipelineAPI.status(), { intervalMs: 3000 }
    );
    const { data: detStatus, refresh: refreshDet } = useLivePoll(
        () => detectionAPI.status(), { intervalMs: 2000 }
    );
    const { data: recStatus, refresh: refreshRec } = useLivePoll(
        () => detectionAPI.recordingStatus(), { intervalMs: 5000 }
    );
    const { data: footage, refresh: refreshFootage } = useLivePoll(
        () => footageAPI.list(), { intervalMs: 10000 }
    );
    // Configured cameras: shown as standby frames (latest snapshot) while no feed runs.
    const { data: cameraList } = useLivePoll(() => camerasAPI.list(), { intervalMs: 30000 });
    // One person detector for every feed, chosen from evaluation (backend settings).
    const { data: currentModel } = useLivePoll(
        () => modelAPI.current(), { intervalMs: 60000 }
    );
    const personModel = currentModel
        ? `${currentModel.default_model} @${currentModel.imgsz} (${currentModel.device})`
        : '—';

    // Per-camera detection counters via WebSocket
    const [cameraLive, setCameraLive] = useState({});
    useWebSocket('/ws/live', {
        onType: {
            detection_update: (d) => setCameraLive((prev) => ({
                ...prev,
                [d.camera_id]: {
                    person_count: d.person_count,
                    active_tracks: d.active_tracks,
                    ts: Date.now(),
                },
            })),
        },
    });

    const activeCameras = useMemo(() => {
        const ids = detStatus?.active_cameras;
        if (Array.isArray(ids)) return ids.map(Number);
        if (pipeState?.cameras?.zones) return Object.keys(pipeState.cameras.zones).map(Number);
        return [];
    }, [detStatus, pipeState]);

    const cameraStats = detStatus?.camera_stats || {};
    const cameraZones = pipeState?.cameras?.zones || {};
    const cameraModels = pipeState?.cameras?.models || {};
    const processingStatus = pipeState?.processing || {};
    const frameCounts = processingStatus.frame_counts || {};
    const totalFramesProcessed = Object.values(frameCounts).reduce((sum, value) => sum + Number(value || 0), 0);
    const trackerLabel = pipeState?.ai_modules?.tracker
        ? title(pipeState.ai_modules.tracker)
        : '—';
    const recordingIds = new Set(
        (recStatus?.recording_cameras || recStatus?.recording || []).map(Number)
    );
    const activeModelSummary = Object.entries(cameraModels)
        .map(([cameraId, info]) => {
            const names = (info?.paths || []).map(filename).filter(Boolean);
            return `Feed ${cameraId}: ${names.join(' + ') || filename(info?.label) || 'person detector'}`;
        })
        .join(' | ');

    const toggleMinimizedFeed = (id) => withViewTransition(() => {
        setMinimizedFeeds((prev) => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
        });
        if (maximizedFeed === id) setMaximizedFeed(null);
    });

    const toggleMaximizedFeed = (id) => withViewTransition(() => {
        setMaximizedFeed((current) => (current === id ? null : id));
        setMinimizedFeeds((prev) => {
            if (!prev.has(id)) return prev;
            const next = new Set(prev);
            next.delete(id);
            return next;
        });
    });

    // A maximised feed moves to the top of the wall; bring it into view. Esc restores it.
    const gridRef = useRef(null);
    useEffect(() => {
        if (maximizedFeed == null) return undefined;
        gridRef.current?.scrollIntoView({ block: 'start', behavior: prefersReducedMotion() ? 'auto' : 'smooth' });
        const onKey = (e) => {
            if (e.key === 'Escape' && focusFeed == null) withViewTransition(() => setMaximizedFeed(null));
        };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [maximizedFeed, focusFeed]);

    const addCamera = async (e) => {
        e.preventDefault();
        setBusy(true); setError(null); setNotice(null);
        try {
            const { cameraId, streamType, source, zone, fps, enableReid, loop } = form;
            if (!source?.toString().trim()) throw new Error('Pick a video source before adding the feed.');
            const res = await detectionAPI.start(Number(cameraId), {
                source,
                stream_type: streamType,
                zone: zone || 'default',
                fps: Number(fps) || 30,
                skip_frames: 1,
                model_mode: 'auto',
                enable_reid: enableReid,
                tracker: 'bytetrack',
                // Only sent for uploaded clips; live sources never loop.
                ...(streamType === 'file' ? { loop } : {}),
            });
            const usedModel = res?.data?.model || currentModel?.default_model || 'person detector';
            setNotice(
                `Camera ${cameraId} added · ${usedModel} · ByteTrack · Re-ID ${enableReid ? 'on' : 'off'}.`
            );
            await Promise.all([refreshPipeline(), refreshDet()]);
        } catch (e) {
            setError(e?.response?.data?.detail || e.message);
        } finally { setBusy(false); }
    };

    const stopCamera = async (id) => {
        setBusy(true); setError(null);
        try {
            await detectionAPI.stop(id);
            await Promise.all([refreshPipeline(), refreshDet()]);
        } catch (e) {
            setError(e?.response?.data?.detail || e.message);
        } finally { setBusy(false); }
    };

    const toggleRecord = async (id) => {
        try {
            if (recordingIds.has(id)) await detectionAPI.recordingStop(id);
            else await detectionAPI.recordingStart(id);
            await refreshRec();
        } catch (e) {
            setError(e?.response?.data?.detail || e.message);
        }
    };

    const uploadClip = async (fileList) => {
        if (!fileList || fileList.length === 0) return;
        setBusy(true); setError(null);
        try {
            await footageAPI.upload(fileList[0], Number(form.cameraId) || 1);
            setNotice('Clip uploaded. Pick it from the dropdown below.');
            await refreshFootage();
        } catch (e) {
            setError(e?.response?.data?.detail || e.message);
        } finally { setBusy(false); }
    };

    const running = pipeState?.state === 'running';
    const ic = { size: 16, strokeWidth: 1.75, 'aria-hidden': true };

    return (
        <div className="page-scroll ops-det">
            <header className="page-header">
                <div>
                    <h1 className="page-title">Video feeds</h1>
                    <div className="osd-strip ops-head-osd" aria-label="Session status">
                        <span>
                            <span className={`live-dot ${running ? 'is-live' : pipeState ? 'is-warn' : ''}`} aria-hidden />
                            <strong>{pipeState ? (running ? 'Live' : (pipeState.state || 'Idle')) : 'Connecting'}</strong>
                        </span>
                        <span>Feeds <strong>{activeCameras.length}</strong></span>
                        <span>Rec <strong>{recordingIds.size}</strong></span>
                        <span>Detector <strong>{personModel}</strong></span>
                    </div>
                </div>
            </header>

            {error && <div className="alert-banner danger" role="alert">{error}</div>}
            {notice && <div className="alert-banner info">{notice}</div>}

            <section className="ops-det-wall" aria-label="Active feeds">
                <div className="section-head">
                    <h2 className="section-title">Active feeds</h2>
                    <span className="count-badge">{activeCameras.length}</span>
                </div>
                {activeCameras.length === 0 ? (
                    Array.isArray(cameraList) && cameraList.length ? (
                        <>
                            <p className="ops-note ops-muted">No feed is being analysed. These are the latest frames from your cameras; add a feed below to go live.</p>
                            <div className="ops-camera-grid">
                                {cameraList.map((cam) => (
                                    <CameraFrame key={cam.id} camera={cam} live={false} />
                                ))}
                            </div>
                        </>
                    ) : (
                        <div className="empty-state ops-det-empty">
                            <span className="empty-state-title">No active feeds</span>
                            <span>Upload a clip or enter a stream below, then add it as a feed.</span>
                        </div>
                    )
                ) : (
                    <div ref={gridRef} className={`ops-det-grid ${maximizedFeed ? 'has-maximized-feed' : ''}`}>
                        {activeCameras.map((id) => {
                            const s = cameraStats[id] || cameraStats[String(id)] || {};
                            const live = cameraLive[id] || cameraLive[String(id)] || {};
                            const isRecording = recordingIds.has(id);
                            const isMinimized = minimizedFeeds.has(id);
                            const isMaximized = maximizedFeed === id;
                            const models = cameraModels[id] || cameraModels[String(id)];
                            const modelNames = (models?.paths || []).map(filename).filter(Boolean).join(' + ') || filename(models?.label);
                            return (
                                <div
                                    key={id}
                                    className={`ops-det-tile${isMinimized ? ' is-minimized' : ''}${isMaximized ? ' is-maximized' : ''}`}
                                    style={{ viewTransitionName: `feed-${id}` }}
                                >
                                    <div className={isMinimized ? 'ops-det-media' : 'ops-det-media vf vf--on-media'}>
                                        <CameraStream
                                            cameraId={id}
                                            label={`Camera ${id}`}
                                            zone={cameraZones[id] || cameraZones[String(id)]}
                                            fps={s.fps ?? s.fps_actual}
                                            connected={s.connected !== false}
                                            finished={Boolean(s.finished)}
                                            detectionCount={live.person_count}
                                            trackCount={live.active_tracks}
                                            minimized={isMinimized}
                                            maximized={isMaximized}
                                            onMinimize={() => toggleMinimizedFeed(id)}
                                            onMaximize={() => toggleMaximizedFeed(id)}
                                            onClose={() => stopCamera(id)}
                                        />
                                    </div>
                                    {!isMinimized && (
                                        <div className="ops-det-tile-bar">
                                            <div className="osd-strip ops-det-tile-osd">
                                                <span>Cam <strong>{String(id).padStart(2, '0')}</strong></span>
                                                {isRecording ? <span><span className="live-dot is-live" aria-hidden /><strong>Rec</strong></span> : null}
                                                {modelNames ? <span className="ops-det-tile-model" title={modelNames}>{modelNames}</span> : null}
                                            </div>
                                            <div className="ops-det-tile-actions">
                                                <button
                                                    type="button"
                                                    className="btn btn-ghost btn-xs"
                                                    onClick={() => setFocusFeed(id)}
                                                    title="Full-screen view with live zone figures"
                                                >
                                                    <Focus size={14} strokeWidth={1.75} aria-hidden /> Focus
                                                </button>
                                                <button
                                                    type="button"
                                                    className={`btn ${isRecording ? 'btn-danger' : 'btn-ghost'} btn-xs`}
                                                    onClick={() => toggleRecord(id)}
                                                    aria-pressed={isRecording}
                                                >
                                                    {isRecording ? <Square size={14} strokeWidth={1.75} aria-hidden /> : <Circle size={14} strokeWidth={1.75} aria-hidden />}
                                                    {isRecording ? 'Stop' : 'Record'}
                                                </button>
                                            </div>
                                        </div>
                                    )}
                                </div>
                            );
                        })}
                    </div>
                )}
            </section>

            <div className="ops-split">
                <section className="panel" aria-label="Add a feed">
                    <div className="panel-body">
                        <div className="panel-head">
                            <div>
                                <h2 className="panel-title">Add a feed</h2>
                                <div className="panel-sub">Run an uploaded clip as a virtual camera, or connect a live source.</div>
                            </div>
                            <label className="btn btn-secondary btn-sm ops-upload-btn">
                                <Upload {...ic} />
                                <span>Upload video</span>
                                <input
                                    type="file" accept="video/*" hidden
                                    onChange={(e) => uploadClip(e.target.files)}
                                />
                            </label>
                        </div>
                        <form onSubmit={addCamera} className="ops-form">
                            <div className="ops-form-grid">
                                <div className="form-group">
                                    <label className="form-label" htmlFor="det-slot">Feed slot</label>
                                    <input
                                        id="det-slot"
                                        className="form-input"
                                        type="number" min={1}
                                        value={form.cameraId}
                                        onChange={(e) => setForm({ ...form, cameraId: e.target.value })}
                                        required
                                    />
                                </div>
                                <div className="form-group">
                                    <label className="form-label" htmlFor="det-zone">Area label</label>
                                    <input
                                        id="det-zone"
                                        className="form-input"
                                        value={form.zone}
                                        onChange={(e) => setForm({ ...form, zone: e.target.value })}
                                        placeholder="entrance, aisle, checkout"
                                    />
                                </div>
                                <div className="form-group">
                                    <label className="form-label" htmlFor="det-type">Input type</label>
                                    <select
                                        id="det-type"
                                        className="form-select"
                                        value={form.streamType}
                                        onChange={(e) => setForm({ ...form, streamType: e.target.value })}
                                    >
                                        <option value="file">Uploaded video (recommended)</option>
                                        <option value="rtsp">RTSP camera</option>
                                        <option value="webcam">USB webcam</option>
                                        <option value="http">HTTP(S) or MJPEG URL</option>
                                    </select>
                                </div>
                                <div className="form-group">
                                    <label className="form-label" htmlFor="det-fps">FPS cap</label>
                                    <input
                                        id="det-fps"
                                        className="form-input" type="number" min={1} max={60}
                                        value={form.fps}
                                        onChange={(e) => setForm({ ...form, fps: e.target.value })}
                                    />
                                </div>
                                <div className="form-group ops-form-span">
                                    <label className="form-label" htmlFor="det-source">Source</label>
                                    {form.streamType === 'file' ? (
                                        <select
                                            id="det-source"
                                            className="form-select"
                                            value={form.source}
                                            onChange={(e) => setForm({ ...form, source: e.target.value })}
                                        >
                                            <option value="">Select an uploaded video</option>
                                            {(footage || []).map((f) => (
                                                <option key={f.filename} value={`footage:${f.filename}`}>
                                                    {f.filename} · feed {f.camera_id ?? '—'}
                                                </option>
                                            ))}
                                        </select>
                                    ) : (
                                        <input
                                            id="det-source"
                                            className="form-input mono"
                                            value={form.source}
                                            onChange={(e) => setForm({ ...form, source: e.target.value })}
                                            placeholder={
                                                form.streamType === 'rtsp' ? 'rtsp://user:pass@192.168.1.10:554/stream' :
                                                    form.streamType === 'http' ? 'https://cam.example.com/mjpeg' :
                                                        '0'
                                            }
                                        />
                                    )}
                                </div>
                            </div>

                            <div className="ops-form-checks">
                                <label className="ops-check">
                                    <input
                                        type="checkbox"
                                        checked={form.enableReid}
                                        onChange={(e) => setForm({ ...form, enableReid: e.target.checked })}
                                    />
                                    <span>Re-ID on this feed</span>
                                </label>
                                {form.streamType === 'file' && (
                                    <label className="ops-check">
                                        <input
                                            type="checkbox"
                                            checked={form.loop}
                                            onChange={(e) => setForm({ ...form, loop: e.target.checked })}
                                        />
                                        <span>Loop when the video ends</span>
                                    </label>
                                )}
                            </div>

                            <div className="ops-form-foot">
                                <p className="form-hint">
                                    Person detector <span className="mono">{personModel}</span>. Fire, product and emotion
                                    run as separate streams and never change people counts.
                                </p>
                                <button type="submit" className="btn btn-primary" disabled={busy}>
                                    <Plus {...ic} /> Add feed
                                </button>
                            </div>
                        </form>
                    </div>
                </section>

                <section className="panel" aria-label="Session">
                    <div className="panel-body">
                        <div className="panel-head">
                            <h2 className="panel-title">Session</h2>
                        </div>
                        <ul className="ops-status-list">
                            <Row label="State" value={pipeState?.state || 'idle'} />
                            <Row label="Total feeds" value={pipeState?.cameras?.total ?? activeCameras.length} />
                            <Row label="Frames processed" value={pipeState ? totalFramesProcessed.toLocaleString() : '—'} />
                            <Row label="People detections" value={processingStatus.total_detections_processed != null ? Number(processingStatus.total_detections_processed).toLocaleString() : '—'} />
                            <Row label="Cross-feed identities" value={pipeState?.ai_modules?.reid?.unique_identities ?? '—'} />
                            <Row label="Tracker" value={trackerLabel} />
                            <Row label="Detection weights" value={activeModelSummary || personModel} />
                            <Row label="Recording" value={recordingIds.size ? `${recordingIds.size} feed${recordingIds.size > 1 ? 's' : ''}` : 'Off'} />
                        </ul>
                    </div>
                </section>
            </div>

            {focusFeed != null && (
                <FocusView
                    cameraId={focusFeed}
                    zoneLabel={cameraZones[focusFeed] || cameraZones[String(focusFeed)]}
                    stats={cameraStats[focusFeed] || cameraStats[String(focusFeed)] || {}}
                    live={cameraLive[focusFeed] || cameraLive[String(focusFeed)] || {}}
                    isActive={activeCameras.includes(focusFeed)}
                    onClose={() => setFocusFeed(null)}
                />
            )}
        </div>
    );
}

const FOCUS_POLL_MS = 2000;
const STALE_MS = 10000;
const fmtNum = (v, digits = 0) => (v == null || !Number.isFinite(Number(v)) ? '—' : Number(v).toFixed(digits));
const fmtSecs = (v) => {
    if (v == null || !Number.isFinite(Number(v))) return '—';
    const s = Math.round(Number(v));
    return s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`;
};
// Crowd levels from analytics/definitions.py CROWD_LEVELS.
const LEVEL_TONE = { critical: 'danger', high: 'warn', moderate: 'info' };

/** Full-screen single-feed monitor: big live stream + live zone KPIs from /api/zones/live. */
function FocusView({ cameraId, zoneLabel, stats, live, isActive, onClose }) {
    const [snap, setSnap] = useState(null);
    const [error, setError] = useState(null);
    const [streamKey, setStreamKey] = useState(() => Date.now());
    const [streamFailed, setStreamFailed] = useState(false);
    const dialogRef = useRef(null);

    useEffect(() => {
        const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); onClose(); } };
        window.addEventListener('keydown', onKey);
        const prevOverflow = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
        const opener = document.activeElement;
        dialogRef.current?.focus();
        return () => {
            window.removeEventListener('keydown', onKey);
            document.body.style.overflow = prevOverflow;
            if (opener && typeof opener.focus === 'function') opener.focus();
        };
    }, [onClose]);

    useEffect(() => {
        let alive = true;
        let timer = null;
        const tick = async () => {
            try {
                const res = await zonesAPI.live(cameraId);
                if (!alive) return;
                const cams = Array.isArray(res.data?.cameras) ? res.data.cameras : [];
                setSnap(cams.find((c) => String(c.camera_id) === String(cameraId)) || null);
                setError(null);
            } catch (e) {
                if (alive) setError(e?.response?.data?.detail || e.message || 'Could not load live zone data');
            }
            if (alive) timer = setTimeout(tick, FOCUS_POLL_MS);
        };
        tick();
        return () => { alive = false; clearTimeout(timer); };
    }, [cameraId]);

    const streamUrl = useMemo(() => {
        const u = liveStreamUrl(cameraId);
        return `${u}${u.includes('?') ? '&' : '?'}t=${streamKey}`;
    }, [cameraId, streamKey]);

    const snapTs = snap?.ts ? new Date(typeof snap.ts === 'number' && snap.ts < 1e12 ? snap.ts * 1000 : snap.ts).getTime() : null;
    const stale = snapTs != null && Number.isFinite(snapTs) && Date.now() - snapTs > STALE_MS;
    const zones = Array.isArray(snap?.zones) ? snap.zones : [];
    const lanes = Array.isArray(snap?.lanes) ? snap.lanes : [];
    const fire = snap?.fire || null;
    const fps = stats.fps ?? stats.fps_actual;

    const toNum = (v) => (v == null || !Number.isFinite(Number(v)) ? null : Number(v));

    return createPortal(
        <div className="ops-focus-backdrop" role="presentation">
            <div className="ops-focus" role="dialog" aria-modal="true" aria-label={`Camera ${cameraId} focus view`} tabIndex={-1} ref={dialogRef}>
                <header className="ops-focus-head">
                    <div className="osd-strip ops-focus-osd">
                        <span>
                            <span className={`live-dot ${isActive && !streamFailed ? 'is-live' : ''}`} aria-hidden />
                            <strong>Camera {String(cameraId).padStart(2, '0')}</strong>
                        </span>
                        {zoneLabel && <span>Zone <strong>{zoneLabel}</strong></span>}
                        {fps != null && <span>FPS <strong>{fmtNum(fps, 1)}</strong></span>}
                        {!isActive && <span className="ops-osd-warn">Feed stopped</span>}
                    </div>
                    <div className="ops-focus-actions">
                        <span className="osd ops-hide-sm">Esc to close</span>
                        <button type="button" className="fp-icon-btn" onClick={onClose} aria-label="Close focus view"><X size={16} strokeWidth={1.75} /></button>
                    </div>
                </header>

                <div className="ops-focus-body">
                    <div className="ops-focus-stage vf vf--on-media">
                        {streamFailed ? (
                            <div className="ops-focus-stream-error">
                                <p>The live stream is not available.</p>
                                <button type="button" className="btn btn-secondary btn-xs" onClick={() => { setStreamFailed(false); setStreamKey(Date.now()); }}>
                                    <RefreshCw size={14} strokeWidth={1.75} aria-hidden /> Retry
                                </button>
                            </div>
                        ) : (
                            <img src={streamUrl} alt={`Camera ${cameraId} live`} className="ops-focus-stream" onError={() => setStreamFailed(true)} />
                        )}
                    </div>

                    <aside className="ops-focus-pane">
                        <div className="ops-focus-kpis">
                            <Metric size="sm" label="In view" value={toNum(snap?.occupancy ?? live.person_count)} />
                            <Metric size="sm" label="Active tracks" value={toNum(snap?.active_tracks ?? live.active_tracks)} />
                        </div>

                        {error && <div className="alert-banner danger">{error}</div>}
                        {stale && <div className="alert-banner warning">Zone data is stale (last update over {STALE_MS / 1000}s ago).</div>}
                        {snap?.status && snap.status !== 'ok' && (
                            <div className="alert-banner info">Zone analytics: {String(snap.status).replace(/_/g, ' ')}</div>
                        )}

                        {fire && (
                            <div className={`alert-banner ${fire.active ? 'danger' : 'success'}`}>
                                <Flame size={14} strokeWidth={1.75} aria-hidden />
                                <span>
                                    {fire.active
                                        ? `${fire.class_name || 'Fire or smoke'} detected · peak ${fmtNum((fire.peak_conf ?? 0) * 100)}%`
                                        : 'No fire or smoke detected'}
                                </span>
                            </div>
                        )}

                        <section className="ops-focus-section">
                            <h3 className="osd">Zones</h3>
                            {!snap && !error && <p className="ops-muted">Loading zones…</p>}
                            {snap && zones.length === 0 && <p className="ops-muted">No zones drawn for this camera. Add them in store setup.</p>}
                            {zones.map((z) => {
                                const tone = z.alert_open ? 'danger' : (LEVEL_TONE[String(z.level || '').toLowerCase()] || 'neutral');
                                // pct is occupancy / capacity as a fraction (may exceed 1 when over capacity).
                                const pct = z.pct != null && Number.isFinite(Number(z.pct)) ? Math.max(0, Math.min(100, Number(z.pct) * 100)) : null;
                                const meterTone = tone === 'danger' ? ' meter--danger' : tone === 'warn' ? ' meter--warn' : '';
                                return (
                                    <div key={z.id} className="ops-zone">
                                        <div className="ops-zone-head">
                                            <span className="ops-zone-name">{z.name || `Zone ${z.id}`}</span>
                                            <span className="ops-zone-type">{String(z.zone_type || '').replace(/_/g, ' ')}</span>
                                            {z.alert_open && <span className="pill pill-danger">Alert</span>}
                                        </div>
                                        <div className="ops-zone-count num">
                                            <span className="ops-zone-big">{fmtNum(z.count)}</span>
                                            {z.capacity ? <span className="ops-muted">/ {z.capacity}</span> : null}
                                            {z.level ? <span className="ops-zone-level">{z.level}{pct != null ? ` · ${Math.round(Number(z.pct) * 100)}%` : ''}</span> : null}
                                        </div>
                                        {pct != null && z.capacity ? (
                                            <div className={`meter${meterTone}`} style={{ '--value': `${pct}%` }}>
                                                <div className="meter-track"><div className="meter-fill" /></div>
                                            </div>
                                        ) : null}
                                        <dl className="ops-zone-stats">
                                            <div><dt>Visits</dt><dd>{fmtNum(z.visits)}</dd></div>
                                            <div><dt>Engaged</dt><dd>{fmtNum(z.engaged_visits)}</dd></div>
                                            <div><dt>Median dwell</dt><dd>{fmtSecs(z.median_dwell_s)}</dd></div>
                                            <div><dt>In / out</dt><dd>{fmtNum(z.entries)} / {fmtNum(z.exits)}</dd></div>
                                        </dl>
                                    </div>
                                );
                            })}
                        </section>

                        {lanes.length > 0 && (
                            <section className="ops-focus-section">
                                <h3 className="osd">Checkout lanes</h3>
                                {lanes.map((l) => (
                                    <div key={l.lane} className="ops-zone">
                                        <div className="ops-zone-head"><span className="ops-zone-name">Lane {l.lane}</span></div>
                                        <dl className="ops-zone-stats">
                                            <div><dt>Queue</dt><dd>{fmtNum(l.queue_length)}</dd></div>
                                            <div><dt>Median wait</dt><dd>{fmtSecs(l.median_wait_s)}</dd></div>
                                            <div><dt>Served</dt><dd>{fmtNum(l.served)}</dd></div>
                                            <div><dt>Abandoned</dt><dd>{fmtNum(l.abandoned)}</dd></div>
                                        </dl>
                                    </div>
                                ))}
                            </section>
                        )}
                    </aside>
                </div>
            </div>
        </div>,
        document.body,
    );
}

function Row({ label, value }) {
    return (
        <li className="ops-status-row">
            <span className="ops-status-label">{label}</span>
            <span className="ops-status-value" title={String(value)}>{String(value)}</span>
        </li>
    );
}
