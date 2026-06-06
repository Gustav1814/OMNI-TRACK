/**
 * OmniTrack AI — Detection & Live Surveillance
 * ────────────────────────────────────────────
 * • Start/stop the multi-camera pipeline.
 * • Add any source: RTSP URL, local file, uploaded clip, webcam index.
 * • Live MJPEG grid with per-camera detection + track counts.
 * • Per-camera recording (start/stop).
 */

import React, { useMemo, useState } from 'react';
import {
    Plus, Upload, Video, Circle, Square, RefreshCw,
} from 'lucide-react';
import {
    detectionAPI, pipelineAPI, footageAPI, modelAPI,
} from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import useWebSocket from '../hooks/useWebSocket';
import CameraStream from '../components/CameraStream';
import ContentCard from '../components/ui/ContentCard';

const title = (value) => String(value || 'unknown').replace(/[_-]/g, ' ').replace(/\b\w/g, (m) => m.toUpperCase());
const filename = (value) => String(value || '').split(/[\\/]/).pop();

export default function DetectionPage() {
    const [form, setForm] = useState({
        cameraId: 1,
        streamType: 'file',
        source: '',
        zone: 'entrance',
        fps: 30,
        modelMode: 'auto',
        models: [],
        enableReid: true,
    });
    const [models, setModels] = useState([]);
    const [selectedModelInfo, setSelectedModelInfo] = useState([]);
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
    const { data: modelsData } = useLivePoll(
        () => modelAPI.list(), { intervalMs: 30000 }
    );

    // Update models list when data changes
    React.useEffect(() => {
        if (modelsData?.models) {
            setModels(modelsData.models);
        }
    }, [modelsData]);

    const allWeightNames = useMemo(
        () => models.map((m) => m.filename).filter(Boolean),
        [models]
    );

    const getModelTypeTag = (modelInfo) => {
        const name = String(modelInfo?.filename || '').toLowerCase();
        if (/(fire|smoke)/.test(name)) return '🔥 Fire/Smoke';
        if (/(face)/.test(name)) return '👤 Face';
        if (/(product)/.test(name)) return '📦 Product';
        if (/(pose|pe_)/.test(name)) return '🏃 Pose';
        if (/(seg|sam)/.test(name)) return '🎯 Segment';
        return '👁 General';
    };

    const isPersonCapableModel = (modelInfo) => {
        const name = String(modelInfo?.filename || '').toLowerCase();
        if (/(fire|smoke|product|face|pose|pe_)/.test(name)) return false;
        const classes = Array.isArray(modelInfo?.classes) ? modelInfo.classes : [];
        if (!classes.length) return true;
        return classes.some((c) => String(c?.name || '').toLowerCase() === 'person');
    };

    const ensembleModelOptions = useMemo(
        () => models.filter(isPersonCapableModel),
        [models]
    );

    const selectedWeightNames = useMemo(() => {
        if (form.modelMode === 'auto') return [];
        if (form.modelMode === 'auto_ensemble') return [];
        return form.models || [];
    }, [allWeightNames, form.modelMode, form.models]);

    // Update selected model info when model changes
    React.useEffect(() => {
        if (selectedWeightNames.length && models.length > 0) {
            setSelectedModelInfo(models.filter(m => selectedWeightNames.includes(m.filename)));
        } else {
            setSelectedModelInfo([]);
        }
    }, [selectedWeightNames, models]);

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
        : 'No Cameras';
    const recordingIds = new Set(
        (recStatus?.recording_cameras || recStatus?.recording || []).map(Number)
    );
    const activeModelSummary = Object.entries(cameraModels)
        .map(([cameraId, info]) => {
            const names = (info?.paths || []).map(filename).filter(Boolean);
            return `Feed ${cameraId}: ${info?.mode === 'ensemble' ? 'Ensemble' : 'Single'} ${names.join(' + ') || filename(info?.label) || 'default'}`;
        })
        .join(' | ');

    const toggleMinimizedFeed = (id) => {
        setMinimizedFeeds((prev) => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
        });
        if (maximizedFeed === id) setMaximizedFeed(null);
    };

    const toggleMaximizedFeed = (id) => {
        setMaximizedFeed((current) => (current === id ? null : id));
        setMinimizedFeeds((prev) => {
            if (!prev.has(id)) return prev;
            const next = new Set(prev);
            next.delete(id);
            return next;
        });
    };

    const toggleModelWeight = (modelFilename) => {
        setForm((prev) => {
            const current = new Set(prev.models || []);
            if (current.has(modelFilename)) current.delete(modelFilename);
            else current.add(modelFilename);
            return { ...prev, models: Array.from(current) };
        });
    };

    const addCamera = async (e) => {
        e.preventDefault();
        setBusy(true); setError(null); setNotice(null);
        try {
            const { cameraId, streamType, source, zone, fps, enableReid, modelMode } = form;
            const selectedModels = modelMode === 'auto' ? [] : selectedWeightNames;
            const modelsForRequest = modelMode === 'auto_ensemble' ? [] : selectedModels;
            if (!source?.toString().trim()) throw new Error('Pick a video source before adding the feed.');
            if (modelMode === 'single' && selectedModels.length !== 1) {
                throw new Error('Select exactly one model weight for Single model mode.');
            }
            if (modelMode === 'ensemble' && selectedModels.length < 2) {
                throw new Error('Multi-model ensemble needs at least two model weights.');
            }
            const modelPayload = {
                model: modelMode === 'single' ? selectedModels[0] : null,
                models: modelMode === 'ensemble' ? modelsForRequest : null,
            };
            await detectionAPI.start(Number(cameraId), {
                source,
                stream_type: streamType,
                zone: zone || 'default',
                fps: Number(fps) || 30,
                skip_frames: 1,
                model: modelPayload.model,
                models: modelPayload.models,
                model_mode: modelMode,
                enable_reid: enableReid,
                tracker: 'bytetrack',
            });
            const modeLabel = modelMode === 'auto'
                ? 'auto default'
                : modelMode === 'single'
                    ? 'single model'
                    : 'ensemble';
            setNotice(
                modelMode === 'auto_ensemble'
                    ? `Camera ${cameraId} added with smart auto ensemble. ByteTrack. Re-ID ${enableReid ? 'on' : 'off'}.`
                    : `Camera ${cameraId} added with ${modeLabel} ${(selectedModels?.length ? selectedModels.join(' + ') : 'default')} · ByteTrack · Re-ID ${enableReid ? 'on' : 'off'}.`
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

    return (
        <div className="page-scroll detection-page">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Video Feeds</h1>
                    <p className="page-subtitle">Use uploaded videos as virtual cameras and monitor them live</p>
                </div>
                <div className="detection-page-header-actions">
                    <span className={`pill ${pipeState?.state === 'running' ? 'pill-success' : 'pill-warn'}`}>
                        session · {pipeState?.state || 'idle'}
                    </span>
                    <button className="btn btn-secondary btn-xs" onClick={() => { refreshPipeline(); refreshDet(); }}>
                        <RefreshCw size={12} /> Refresh
                    </button>
                </div>
            </div>

            {error && <div className="alert-banner danger">{error}</div>}
            {notice && <div className="alert-banner info">{notice}</div>}

            <ContentCard
                className="detection-feeds-panel"
                title={<> <Video size={16} style={{ verticalAlign: -3, marginRight: 6 }} /> Active Video Feeds ({activeCameras.length}) </>}
                subtitle="People counts update in real time; FPS refreshes every 2 seconds"
                accent="sky"
            >
                {activeCameras.length === 0 ? (
                    <div className="page-empty-hint">
                        No active feeds. Upload a video and add it as a feed in the panel below.
                    </div>
                ) : (
                    <div className={`camera-grid detection-feed-grid ${maximizedFeed ? 'has-maximized-feed' : ''}`}>
                        {activeCameras.map((id) => {
                            const s = cameraStats[id] || cameraStats[String(id)] || {};
                            const live = cameraLive[id] || cameraLive[String(id)] || {};
                            const isRecording = recordingIds.has(id);
                            const isMinimized = minimizedFeeds.has(id);
                            const isMaximized = maximizedFeed === id;
                            return (
                                <div
                                    key={id}
                                    className={`feed-shell ${isMinimized ? 'feed-shell-minimized' : ''} ${isMaximized ? 'feed-shell-maximized' : ''}`}
                                >
                                    <CameraStream
                                        cameraId={id}
                                        label={`Camera ${id}`}
                                        zone={cameraZones[id] || cameraZones[String(id)]}
                                        fps={s.fps ?? s.fps_actual}
                                        connected={s.connected !== false}
                                        detectionCount={live.person_count}
                                        trackCount={live.active_tracks}
                                        minimized={isMinimized}
                                        maximized={isMaximized}
                                        onMinimize={() => toggleMinimizedFeed(id)}
                                        onMaximize={() => toggleMaximizedFeed(id)}
                                        onClose={() => stopCamera(id)}
                                    />
                                    {!isMinimized && (
                                        <div className="feed-shell-actions">
                                            <button
                                                className={`btn ${isRecording ? 'btn-danger' : 'btn-secondary'} btn-xs`}
                                                onClick={() => toggleRecord(id)}
                                            >
                                                {isRecording ? <Square size={12} /> : <Circle size={12} />}
                                                {isRecording ? 'Stop Recording' : 'Record Feed'}
                                            </button>
                                        </div>
                                    )}
                                </div>
                            );
                        })}
                    </div>
                )}
            </ContentCard>

            <div className="two-col detection-page-controls">
                <ContentCard
                    title="Add Video Feed"
                    subtitle="Primary flow: upload a video and run it as a virtual camera"
                    accent="teal"
                >
                    <form onSubmit={addCamera} className="detection-form">
                        <div className="detection-form-row-2">
                            <div>
                                <label className="form-label">Feed Slot ID</label>
                                <input
                                    className="form-input"
                                    type="number" min={1}
                                    value={form.cameraId}
                                    onChange={(e) => setForm({ ...form, cameraId: e.target.value })}
                                    required
                                />
                            </div>
                            <div>
                                <label className="form-label">Area Label</label>
                                <input
                                    className="form-input"
                                    value={form.zone}
                                    onChange={(e) => setForm({ ...form, zone: e.target.value })}
                                    placeholder="entrance, aisle, checkout"
                                />
                            </div>
                        </div>

                        <div>
                            <label className="form-label">Detection Mode</label>
                            <select
                                className="form-select"
                                value={form.modelMode}
                                onChange={(e) => setForm({ ...form, modelMode: e.target.value, models: [] })}
                            >
                                <option value="auto">Auto / default weight</option>
                                <option value="auto_ensemble">Smart auto ensemble</option>
                                <option value="single">Single model weight</option>
                                <option value="ensemble">Multi-model ensemble</option>
                            </select>
                        </div>

                        {form.modelMode === 'single' && (
                            <div>
                                <label className="form-label">Model Weight</label>
                                <select
                                    className="form-select"
                                    value={form.models[0] || ''}
                                    onChange={(e) => setForm({ ...form, models: e.target.value ? [e.target.value] : [] })}
                                    required
                                >
                                    <option value="">Select a model weight...</option>
                                    {models.map((m) => (
                                        <option key={m.filename} value={m.filename}>
                                            {m.filename} ({m.num_classes || 'all'} classes)
                                        </option>
                                    ))}
                                </select>
                            </div>
                        )}

                        {form.modelMode === 'ensemble' && (
                            <div>
                                <label className="form-label">Model Weights (select 2 or more)</label>
                                <div className="detection-form-toolbar">
                                    <button
                                        type="button"
                                        className="btn btn-secondary btn-xs"
                                        onClick={() => setForm({
                                            ...form,
                                            models: models.map((m) => m.filename),
                                        })}
                                    >
                                        Select all
                                    </button>
                                    <button
                                        type="button"
                                        className="btn btn-secondary btn-xs"
                                        onClick={() => setForm({
                                            ...form,
                                            models: ensembleModelOptions.map((m) => m.filename),
                                        })}
                                    >
                                        Person models only
                                    </button>
                                    <button
                                        type="button"
                                        className="btn btn-secondary btn-xs"
                                        onClick={() => setForm({ ...form, models: [] })}
                                    >
                                        Clear
                                    </button>
                                </div>
                                <div className="detection-model-picker">
                                    {models.map((m) => (
                                        <label key={m.filename} className="detection-model-option">
                                            <input
                                                type="checkbox"
                                                checked={(form.models || []).includes(m.filename)}
                                                onChange={() => toggleModelWeight(m.filename)}
                                            />
                                            <span className="detection-model-name">{m.filename}</span>
                                            <span className="detection-model-tag">{getModelTypeTag(m)}</span>
                                            <span className="detection-model-classes">({m.num_classes || 'all'} classes)</span>
                                        </label>
                                    ))}
                                    {models.length === 0 && (
                                        <div className="page-empty-hint">No model weights found in model_weight directory.</div>
                                    )}
                                </div>
                            </div>
                        )}

                        <div className="detection-form-hint">
                            {form.modelMode === 'auto' && <>Default: {modelsData?.default_model || 'yolov8n.pt'}</>}
                            {form.modelMode === 'auto_ensemble' && (
                                <>Smart auto ensemble chooses a small compatible person-detection set from model_weight ({allWeightNames.length} file{allWeightNames.length === 1 ? '' : 's'} available) and merges detections.</>
                            )}
                            {selectedModelInfo.length > 0 && (
                                <><strong>{selectedModelInfo.length > 1 ? `Ensemble (${selectedModelInfo.length}):` : 'Selected:'}</strong> {selectedModelInfo.map((m) => m.filename).join(' + ')}</>
                            )}
                        </div>

                        <div>
                            <label className="form-label">Input Type</label>
                            <select
                                className="form-select"
                                value={form.streamType}
                                onChange={(e) => setForm({ ...form, streamType: e.target.value })}
                            >
                                <option value="file">Uploaded video (recommended)</option>
                                <option value="rtsp">RTSP camera (advanced)</option>
                                <option value="webcam">USB webcam (advanced)</option>
                                <option value="http">HTTP(S) / MJPEG URL (advanced)</option>
                            </select>
                        </div>

                        <label className="detection-form-check">
                            <input
                                type="checkbox"
                                checked={form.enableReid}
                                onChange={(e) => setForm({ ...form, enableReid: e.target.checked })}
                            />
                            <span>Enable Re-ID on this feed</span>
                        </label>

                        <div>
                            <label className="form-label">Source</label>
                            {form.streamType === 'file' ? (
                                <select
                                    className="form-select"
                                    value={form.source}
                                    onChange={(e) => setForm({ ...form, source: e.target.value })}
                                >
                                    <option value="">Select uploaded video...</option>
                                    {(footage || []).map((f) => (
                                        <option key={f.filename} value={`footage:${f.filename}`}>
                                            {f.filename} - feed {f.camera_id ?? '-'}
                                        </option>
                                    ))}
                                </select>
                            ) : (
                                <input
                                    className="form-input"
                                    value={form.source}
                                    onChange={(e) => setForm({ ...form, source: e.target.value })}
                                    placeholder={
                                        form.streamType === 'rtsp' ? 'rtsp://admin:pass@192.168.1.10:554/stream' :
                                            form.streamType === 'http' ? 'https://cam.example.com/mjpeg' :
                                                '0'
                                    }
                                />
                            )}
                        </div>

                        <div className="detection-form-actions">
                            <div>
                                <label className="form-label">FPS cap</label>
                                <input
                                    className="form-input" type="number" min={1} max={60}
                                    value={form.fps}
                                    onChange={(e) => setForm({ ...form, fps: e.target.value })}
                                />
                            </div>
                            <button type="submit" className="btn btn-primary" disabled={busy}>
                                <Plus size={14} /> Add Feed
                            </button>
                        </div>

                        <label className="btn btn-secondary detection-upload-btn">
                            <Upload size={14} />
                            <span>Upload video</span>
                            <input
                                type="file" accept="video/*" hidden
                                onChange={(e) => uploadClip(e.target.files)}
                            />
                        </label>
                    </form>
                </ContentCard>

                <ContentCard
                    title="Session Status"
                    subtitle="Live counters for your active video feeds"
                    accent="cyan"
                >
                    <div className="detection-status-list">
                        <Row label="State" value={pipeState?.state || 'idle'} />
                        <Row label="Total feeds" value={pipeState?.cameras?.total ?? activeCameras.length} />
                        <Row label="Frames processed" value={totalFramesProcessed} />
                        <Row label="People detections"
                            value={processingStatus.total_detections_processed ?? 0} />
                        <Row label="Cross-feed memory size"
                            value={pipeState?.ai_modules?.reid?.gallery_size ?? 0} />
                        <Row label="Tracker"
                            value={trackerLabel} />
                        <Row label="Detection weights"
                            value={activeModelSummary || (modelsData?.default_model ? `Auto ${modelsData.default_model}` : 'Auto default')} />
                        <Row label="Recording"
                            value={recordingIds.size ? `${recordingIds.size} feed(s)` : 'idle'} />
                    </div>
                </ContentCard>
            </div>
        </div>
    );
}

function Row({ label, value }) {
    return (
        <div className="detection-status-row">
            <span className="detection-status-label">{label}</span>
            <span className="detection-status-value" title={String(value)}>{String(value)}</span>
        </div>
    );
}
