/**
 * OmniTrack AI — Detection & Live Surveillance
 * ────────────────────────────────────────────
 * • Start/stop the multi-camera pipeline.
 * • Add any source: RTSP URL, local file, uploaded clip, webcam index.
 * • Live MJPEG grid with per-camera detection + track counts.
 * • Per-camera recording (start/stop).
 */

import React, { useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import {
    PlayCircle, StopCircle, Plus, Upload, Video, Circle, Square, RefreshCw,
} from 'lucide-react';
import {
    detectionAPI, pipelineAPI, footageAPI, liveStreamUrl, modelAPI, systemAPI,
} from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import useWebSocket from '../hooks/useWebSocket';
import CameraStream from '../components/CameraStream';

export default function DetectionPage() {
    const [form, setForm] = useState({
        cameraId: 1,
        streamType: 'file',
        source: '',
        zone: 'entrance',
        fps: 30,
        model: '',
        tracker: 'botsort.yaml',
    });
    const [models, setModels] = useState([]);
    const [selectedModelInfo, setSelectedModelInfo] = useState(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState(null);
    const [notice, setNotice] = useState(null);
    const [modelFetchUrl, setModelFetchUrl] = useState('');

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
    const { data: modelsData, refresh: refreshModels } = useLivePoll(
        () => modelAPI.list(), { intervalMs: 30000 }
    );
    const { data: profileInfo } = useLivePoll(
        () => systemAPI.profile(), { intervalMs: 10000 }
    );

    // Update models list when data changes
    React.useEffect(() => {
        if (modelsData?.models) {
            setModels(modelsData.models);
            // Set default model if none selected
            if (!form.model && modelsData.default_model) {
                setForm(prev => ({ ...prev, model: modelsData.default_model }));
            }
        }
    }, [modelsData]);

    // Update selected model info when model changes
    React.useEffect(() => {
        if (form.model && models.length > 0) {
            const model = models.find(m => m.filename === form.model);
            setSelectedModelInfo(model || null);
        } else {
            setSelectedModelInfo(null);
        }
    }, [form.model, models]);

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
            system_pressure: (d) => setNotice(`System pressure: ${d.status} (rss ${Number(d.memory?.rss_mb || 0).toFixed(0)}MB)`),
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
    const recordingIds = new Set(
        (recStatus?.recording_cameras || recStatus?.recording || []).map(Number)
    );

    const togglePipeline = async () => {
        setBusy(true); setError(null);
        try {
            if (pipeState?.state === 'running') await pipelineAPI.stop();
            else await pipelineAPI.start();
            await refreshPipeline();
        } catch (e) {
            setError(e?.response?.data?.detail || e.message);
        } finally { setBusy(false); }
    };

    const addCamera = async (e) => {
        e.preventDefault();
        setBusy(true); setError(null); setNotice(null);
        try {
            const { cameraId, streamType, source, zone, fps, model, tracker } = form;
            if (!source?.toString().trim()) throw new Error('Pick a video source before adding the feed.');
            await pipelineAPI.addCamera(
                Number(cameraId), source, streamType, zone || 'default', Number(fps) || 30, 1
            );
            // Start detection with selected model
            await detectionAPI.start(Number(cameraId), {
                source,
                stream_type: streamType,
                zone: zone || 'default',
                model: model || undefined,
                tracker: tracker || 'botsort.yaml',
                fps: Number(fps) || 30,
                skip_frames: 1,
            });
            setNotice(`Camera ${cameraId} added with model ${model || 'default'}.`);
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

    const runSegment = async (id) => {
        try {
            const res = await detectionAPI.segmentRun(id);
            const masks = res?.data?.masks || [];
            setNotice(`SAM2 camera ${id}: ${masks.length} mask(s)`);
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

    const uploadModel = async (fileList) => {
        if (!fileList || fileList.length === 0) return;
        setBusy(true); setError(null); setNotice(null);
        try {
            await modelAPI.upload(fileList[0]);
            setNotice(`Model uploaded: ${fileList[0].name}`);
            await refreshModels();
        } catch (e) {
            setError(e?.response?.data?.detail || e.message);
        } finally { setBusy(false); }
    };

    const fetchModel = async () => {
        if (!modelFetchUrl.trim()) return;
        setBusy(true); setError(null); setNotice(null);
        try {
            await modelAPI.fetch(modelFetchUrl.trim());
            setNotice(`Model fetched: ${modelFetchUrl.trim()}`);
            setModelFetchUrl('');
            await refreshModels();
        } catch (e) {
            setError(e?.response?.data?.detail || e.message);
        } finally { setBusy(false); }
    };

    return (
        <div className="page-scroll">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Video Feeds</h1>
                    <p className="page-subtitle">Use uploaded videos as virtual cameras and monitor them live</p>
                </div>
                <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                    <span className={`pill ${pipeState?.state === 'running' ? 'pill-success' : 'pill-warn'}`}>
                        session · {pipeState?.state || 'idle'}
                    </span>
                    <button className="btn btn-secondary btn-xs" onClick={() => { refreshPipeline(); refreshDet(); }}>
                        <RefreshCw size={12} /> Refresh
                    </button>
                    <button
                        className={`btn ${pipeState?.state === 'running' ? 'btn-danger' : 'btn-primary'} btn-xs`}
                        onClick={togglePipeline}
                        disabled={busy}
                    >
                        {pipeState?.state === 'running'
                            ? (<><StopCircle size={14} /> Stop Session</>)
                            : (<><PlayCircle size={14} /> Start Session</>)}
                    </button>
                </div>
            </div>

            {error && <div className="alert-banner danger">{error}</div>}
            {notice && <div className="alert-banner info">{notice}</div>}

            <div className="two-col">
                <div className="card">
                    <div className="card-header">
                        <h3 className="card-title">Add Video Feed</h3>
                        <div className="card-subtitle">Primary flow: upload a video and run it as a virtual camera</div>
                    </div>
                    <form onSubmit={addCamera} style={{ display: 'grid', gap: 10 }}>
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
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
                            <label className="form-label">Detection Model</label>
                            <select
                                className="form-select"
                                value={form.model}
                                onChange={(e) => setForm({ ...form, model: e.target.value })}
                            >
                                <option value="">Default (yolov8n.pt)</option>
                                {models.map((m) => (
                                    <option key={m.filename} value={m.filename}>
                                        {m.filename} ({m.num_classes} classes)
                                    </option>
                                ))}
                            </select>
                            {selectedModelInfo && (
                                <div style={{ marginTop: 8, fontSize: 12, color: 'var(--text-secondary)' }}>
                                    <strong>Detects:</strong> {selectedModelInfo.classes?.slice(0, 5).map(c => c.name).join(', ')}
                                    {selectedModelInfo.classes?.length > 5 && ` +${selectedModelInfo.classes.length - 5} more`}
                                    <br />
                                    <strong>Task:</strong> {selectedModelInfo.task || 'detect'} · <strong>Family:</strong> {selectedModelInfo.family || 'custom'} · <strong>Size:</strong> {selectedModelInfo.size_mb || ((selectedModelInfo.size_bytes || 0) / (1024 * 1024)).toFixed(1)} MB
                                    {selectedModelInfo.recommended_profile && profileInfo?.runtime_profile
                                        && selectedModelInfo.recommended_profile !== profileInfo.runtime_profile && (
                                            <>
                                                <br />
                                                <span style={{ color: 'var(--accent-amber)' }}>
                                                    Warning: recommended for {selectedModelInfo.recommended_profile}, current profile is {profileInfo.runtime_profile}.
                                                </span>
                                            </>
                                        )}
                                </div>
                            )}
                            <div style={{ marginTop: 8, display: 'grid', gridTemplateColumns: '1fr auto', gap: 8 }}>
                                <input
                                    className="form-input"
                                    value={modelFetchUrl}
                                    onChange={(e) => setModelFetchUrl(e.target.value)}
                                    placeholder="Fetch model by URL or asset name (e.g. yolo26l.pt)"
                                />
                                <button type="button" className="btn btn-secondary btn-xs" onClick={fetchModel} disabled={busy}>
                                    Fetch
                                </button>
                            </div>
                            <label
                                className="btn btn-secondary btn-xs"
                                style={{ marginTop: 8, display: 'inline-flex', cursor: 'pointer', justifyContent: 'center' }}
                            >
                                <Upload size={12} />
                                <span style={{ marginLeft: 6 }}>Upload weights</span>
                                <input type="file" accept=".pt,.onnx,.engine,.tflite,.pth" hidden onChange={(e) => uploadModel(e.target.files)} />
                            </label>
                        </div>

                        <div>
                            <label className="form-label">Tracker</label>
                            <select
                                className="form-select"
                                value={form.tracker}
                                onChange={(e) => setForm({ ...form, tracker: e.target.value })}
                            >
                                <option value="botsort.yaml">BoT-SORT</option>
                                <option value="bytetrack.yaml">ByteTrack</option>
                            </select>
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

                        <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: 10, alignItems: 'end' }}>
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

                        <label
                            className="btn btn-secondary"
                            style={{ display: 'inline-flex', cursor: 'pointer', justifyContent: 'center' }}
                        >
                            <Upload size={14} />
                            <span style={{ marginLeft: 8 }}>Upload video</span>
                            <input
                                type="file" accept="video/*" hidden
                                onChange={(e) => uploadClip(e.target.files)}
                            />
                        </label>
                    </form>
                </div>

                <div className="card">
                    <div className="card-header">
                        <h3 className="card-title">Session Status</h3>
                        <div className="card-subtitle">Live counters for your active video feeds</div>
                    </div>
                    <div style={{ display: 'grid', gap: 10 }}>
                        <Row label="State" value={pipeState?.state || 'idle'} />
                        <Row label="Total feeds" value={pipeState?.cameras?.total ?? activeCameras.length} />
                        <Row label="Frames processed"
                            value={pipeState?.frame_counts
                                ? Object.values(pipeState.frame_counts).reduce((a, b) => a + b, 0)
                                : 0} />
                        <Row label="People detections"
                            value={pipeState?.total_detections_processed ?? 0} />
                        <Row label="Cross-feed memory size"
                            value={pipeState?.ai_modules?.reid?.gallery_size ?? 0} />
                        <Row label="Recording"
                            value={recordingIds.size ? `${recordingIds.size} feed(s)` : 'idle'} />
                    </div>
                </div>
            </div>

            <div className="card" style={{ marginTop: 18 }}>
                <div className="card-header">
                    <h3 className="card-title">
                        <Video size={16} style={{ verticalAlign: -3, marginRight: 6 }} />
                        Active Video Feeds ({activeCameras.length})
                    </h3>
                    <div className="card-subtitle">
                        People counts update in real time; FPS refreshes every 2 seconds
                    </div>
                </div>

                {activeCameras.length === 0 ? (
                    <div className="page-empty-hint">
                        No active feeds. Upload a video and add it as a feed above.
                    </div>
                ) : (
                    <div className="camera-grid">
                        {activeCameras.map((id) => {
                            const s = cameraStats[id] || cameraStats[String(id)] || {};
                            const live = cameraLive[id] || cameraLive[String(id)] || {};
                            const isRecording = recordingIds.has(id);
                            return (
                                <div key={id} style={{ position: 'relative' }}>
                                    <CameraStream
                                        cameraId={id}
                                        label={`Camera ${id}`}
                                        zone={cameraZones[id] || cameraZones[String(id)]}
                                        fps={s.fps ?? s.fps_actual}
                                        connected={s.connected !== false}
                                        detectionCount={live.person_count}
                                        trackCount={live.active_tracks}
                                        onClose={() => stopCamera(id)}
                                    />
                                    <div style={{
                                        display: 'flex', gap: 6, justifyContent: 'flex-end',
                                        padding: '8px 2px',
                                    }}>
                                        <button
                                            className={`btn ${isRecording ? 'btn-danger' : 'btn-secondary'} btn-xs`}
                                            onClick={() => toggleRecord(id)}
                                        >
                                            {isRecording ? <Square size={12} /> : <Circle size={12} />}
                                            {isRecording ? 'Stop Recording' : 'Record Feed'}
                                        </button>
                                        <button
                                            className="btn btn-secondary btn-xs"
                                            onClick={() => runSegment(id)}
                                            disabled={!profileInfo?.runtime_profile || profileInfo?.runtime_profile === 'laptop'}
                                            title={profileInfo?.runtime_profile === 'laptop' ? 'Enable workstation profile for SAM2' : 'Run on-demand SAM2 segmentation'}
                                        >
                                            Segment
                                        </button>
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                )}
            </div>
        </div>
    );
}

function Row({ label, value }) {
    return (
        <div style={{
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            padding: '8px 10px', background: 'var(--bg-glass)',
            borderRadius: 10, border: '1px solid var(--border)',
        }}>
            <span style={{ color: 'var(--text-secondary)', fontSize: 13 }}>{label}</span>
            <span style={{ fontWeight: 600, fontSize: 13 }}>{String(value)}</span>
        </div>
    );
}
