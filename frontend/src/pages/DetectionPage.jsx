/**
 * OmniTrack AI — Detection Page
 * Live person detection: add camera feeds, start pipeline, see real YOLO + ByteTrack results.
 */

import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { Camera, Play, Square, Wifi, Loader2, Circle, Square as SquareIcon, Building2, Film } from 'lucide-react';
import { detectionAPI, pipelineAPI, footageAPI, API_BASE } from '../services/api';

const STREAM_TYPES = [
    { value: 'webcam', label: 'Webcam' },
    { value: 'file', label: 'Video file' },
    { value: 'rtsp', label: 'RTSP (IP camera)' },
];

export default function DetectionPage() {
    const [status, setStatus] = useState(null);
    const [results, setResults] = useState({});
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [adding, setAdding] = useState(false);
    const [form, setForm] = useState({ cameraId: 1, source: '0', streamType: 'webcam', zone: 'default' });
    const [recordingCameras, setRecordingCameras] = useState([]);
    const [footageList, setFootageList] = useState([]);
    const [startingStore, setStartingStore] = useState(false);
    const token = useMemo(() => typeof localStorage !== 'undefined' ? localStorage.getItem('omnitrack_token') : null, []);

    const fetchStatus = useCallback(async () => {
        try {
            const res = await detectionAPI.status();
            setStatus(res.data);
            setError(null);
        } catch (e) {
            setError(e.response?.data?.detail || e.message || 'Failed to load status');
        } finally {
            setLoading(false);
        }
    }, []);

    const fetchResults = useCallback(async () => {
        if (!status?.active_cameras?.length) return;
        const next = {};
        for (const camId of status.active_cameras) {
            try {
                const res = await detectionAPI.results(camId);
                next[camId] = Array.isArray(res.data) ? res.data : [];
            } catch {
                next[camId] = [];
            }
        }
        setResults(next);
    }, [status?.active_cameras]);

    useEffect(() => {
        fetchStatus();
    }, [fetchStatus]);

    useEffect(() => {
        footageAPI.list().then((r) => setFootageList(r.data || [])).catch(() => setFootageList([]));
    }, []);

    useEffect(() => {
        if (!status?.active_cameras?.length) return;
        fetchResults();
        const t = setInterval(fetchResults, 2000);
        return () => clearInterval(t);
    }, [status?.active_cameras, fetchResults]);

    const fetchRecordingStatus = useCallback(async () => {
        try {
            const res = await detectionAPI.recordingStatus();
            setRecordingCameras(res.data?.recording_cameras ?? res.data?.recording ?? []);
        } catch {
            setRecordingCameras([]);
        }
    }, []);
    useEffect(() => {
        if (!status?.active_cameras?.length) return;
        fetchRecordingStatus();
        const t = setInterval(fetchRecordingStatus, 3000);
        return () => clearInterval(t);
    }, [status?.active_cameras, fetchRecordingStatus]);

    const handleStart = async () => {
        setAdding(true);
        setError(null);
        try {
            await detectionAPI.start(form.cameraId, {
                source: form.source,
                stream_type: form.streamType,
                zone: form.zone,
            });
            await fetchStatus();
        } catch (e) {
            setError(e.response?.data?.detail || e.message || 'Failed to start');
        } finally {
            setAdding(false);
        }
    };

    const handleStopPipeline = async () => {
        try {
            await pipelineAPI.stop();
            setStatus(null);
            setResults({});
            await fetchStatus();
        } catch (e) {
            setError(e.response?.data?.detail || e.message || 'Failed to stop');
        }
    };

    const handleStopCamera = async (cameraId) => {
        try {
            await detectionAPI.stop(cameraId);
            await fetchStatus();
        } catch (e) {
            setError(e.response?.data?.detail || e.message || 'Failed to stop camera');
        }
    };

    const handleStartRecording = async (cameraId) => {
        setError(null);
        try {
            await detectionAPI.recordingStart(cameraId);
            await fetchRecordingStatus();
        } catch (e) {
            setError(e.response?.data?.detail || e.message || 'Failed to start recording');
        }
    };
    const handleStopRecording = async (cameraId) => {
        setError(null);
        try {
            await detectionAPI.recordingStop(cameraId);
            await fetchRecordingStatus();
        } catch (e) {
            setError(e.response?.data?.detail || e.message || 'Failed to stop recording');
        }
    };

    /** Start one stored footage file as a live camera (full CV as in store). */
    const handleStartFromFootage = async (cameraId, filename, zone = 'store') => {
        setError(null);
        try {
            await detectionAPI.start(cameraId, {
                source: `footage:${filename}`,
                stream_type: 'file',
                zone: zone || 'store',
            });
            await fetchStatus();
        } catch (e) {
            setError(e.response?.data?.detail || e.message || 'Failed to start camera from footage');
        }
    };

    /** Start all stored footage as store cameras (Cam 1, 2, 3, ...) — prototype demo. */
    const handleStartAllStoreCameras = async () => {
        if (!footageList.length) {
            setError('Upload or add footage first (Dashboard → Stored CCTV Footage).');
            return;
        }
        setStartingStore(true);
        setError(null);
        try {
            for (let i = 0; i < footageList.length; i++) {
                await detectionAPI.start(i + 1, {
                    source: `footage:${footageList[i].filename}`,
                    stream_type: 'file',
                    zone: ['entrance', 'aisle', 'checkout', 'store'][i % 4] || 'store',
                });
            }
            await fetchStatus();
        } catch (e) {
            setError(e.response?.data?.detail || e.message || 'Failed to start store cameras');
        } finally {
            setStartingStore(false);
        }
    };

    const cameraStats = status?.camera_stats || {};
    const activeCameras = status?.active_cameras || [];
    const totalActive = status?.total_active ?? 0;
    const pipelineState = status?.state ?? 'idle';

    const totalDetections = Object.values(results).reduce((s, arr) => s + (arr?.length || 0), 0);
    const avgFps = activeCameras.length
        ? activeCameras.reduce((a, id) => a + (cameraStats[id]?.fps || 0), 0) / activeCameras.length
        : 0;

    if (loading && !status) {
        return (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: 280 }}>
                <Loader2 size={32} className="animate-spin" style={{ color: 'var(--text-secondary)' }} />
            </div>
        );
    }

    return (
        <div>
            <div className="page-header">
                <h2 className="page-title">Live Detection Feed</h2>
                <p className="page-description">Run full computer vision (detection, tracking, Re-ID) on live or stored feeds — same pipeline as deployed in-store.</p>
            </div>

            {error && (
                <div className="card" style={{ marginBottom: 16, borderColor: 'var(--accent-rose)', background: 'rgba(244,63,94,0.08)' }}>
                    <span style={{ color: 'var(--accent-rose)' }}>{error}</span>
                </div>
            )}

            {/* Store CCTV prototype: use downloaded footage as live store cameras */}
            <div className="card animate-in" style={{ marginBottom: 24, borderColor: 'var(--accent-primary)', background: 'rgba(99,102,241,0.06)' }}>
                <div className="card-header">
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <Building2 size={20} />
                        <span className="card-title">Store CCTV prototype</span>
                    </div>
                </div>
                <div className="card-body">
                    <p style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 16 }}>
                        Use <strong>downloaded store CCTV</strong> (one video per camera). Upload clips in Dashboard → Stored CCTV Footage, then add them here. The system runs the <strong>same live computer vision</strong> (detection, tracking, Re-ID) as when deployed. <strong>Global Re-ID:</strong> all cameras share one pipeline and one identity gallery — the same person on different feeds gets the same ID.
                    </p>
                    {footageList.length > 0 ? (
                        <>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12, flexWrap: 'wrap' }}>
                                <button
                                    type="button"
                                    onClick={handleStartAllStoreCameras}
                                    disabled={startingStore}
                                    className="btn btn-primary"
                                    style={{ display: 'flex', alignItems: 'center', gap: 6 }}
                                >
                                    {startingStore ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} />}
                                    Start all as store cameras (Cam 1, 2, 3…)
                                </button>
                                <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                                    {footageList.length} clip{footageList.length !== 1 ? 's' : ''} → Camera 1, 2, …
                                </span>
                            </div>
                            <div style={{ display: 'grid', gap: 8 }}>
                                {footageList.map((item, idx) => (
                                    <div
                                        key={item.filename}
                                        style={{
                                            display: 'flex',
                                            alignItems: 'center',
                                            gap: 12,
                                            padding: '10px 12px',
                                            background: 'var(--bg-secondary)',
                                            borderRadius: 8,
                                            border: '1px solid var(--border)',
                                            flexWrap: 'wrap',
                                        }}
                                    >
                                        <Film size={16} style={{ color: 'var(--text-muted)' }} />
                                        <span style={{ flex: 1, fontSize: 13, minWidth: 120 }}>{item.filename}</span>
                                        <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>as Camera {idx + 1}</span>
                                        <button
                                            type="button"
                                            className="btn"
                                            style={{ fontSize: 12, padding: '6px 10px' }}
                                            onClick={() => handleStartFromFootage(idx + 1, item.filename, ['entrance', 'aisle', 'checkout'][idx % 3] || 'store')}
                                        >
                                            <Play size={12} style={{ marginRight: 4 }} /> Start as live camera
                                        </button>
                                    </div>
                                ))}
                            </div>
                        </>
                    ) : (
                        <p style={{ fontSize: 13, color: 'var(--text-muted)' }}>
                            No footage yet. Upload store CCTV clips in <strong>Dashboard → Stored CCTV Footage</strong>, or use the upload there. Then return here and start them as store cameras.
                        </p>
                    )}
                </div>
            </div>

            <div className="stats-grid" style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}>
                {[
                    { label: 'Pipeline', value: pipelineState, color: pipelineState === 'running' ? '#10b981' : '#64748b' },
                    { label: 'Active Feeds', value: String(totalActive), color: '#10b981' },
                    { label: 'Total Detections', value: String(totalDetections), color: '#6366f1' },
                    { label: 'Avg FPS', value: avgFps ? avgFps.toFixed(1) : '—', color: '#06b6d4' },
                ].map((s, i) => (
                    <div key={i} className="stat-card animate-in">
                        <span className="stat-card-label">{s.label}</span>
                        <div className="stat-card-value" style={{ color: s.color }}>{s.value}</div>
                    </div>
                ))}
            </div>

            {/* Add camera + Start detection */}
            <div className="card animate-in" style={{ marginBottom: 24 }}>
                <div className="card-header">
                    <span className="card-title">Add camera & start detection</span>
                </div>
                <div className="card-body" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 12, alignItems: 'end' }}>
                    <div>
                        <label style={{ fontSize: 11, color: 'var(--text-secondary)', display: 'block', marginBottom: 4 }}>Camera ID</label>
                        <input
                            type="number"
                            min={1}
                            value={form.cameraId}
                            onChange={(e) => setForm((f) => ({ ...f, cameraId: Number(e.target.value) || 1 }))}
                            style={{ width: '100%', padding: '8px 10px', background: 'var(--bg-secondary)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--text-primary)' }}
                        />
                    </div>
                    <div>
                        <label style={{ fontSize: 11, color: 'var(--text-secondary)', display: 'block', marginBottom: 4 }}>Source (0 = webcam, or path/URL)</label>
                        <input
                            type="text"
                            placeholder="0 or /path/to/video.mp4 or rtsp://..."
                            value={form.source}
                            onChange={(e) => setForm((f) => ({ ...f, source: e.target.value }))}
                            style={{ width: '100%', padding: '8px 10px', background: 'var(--bg-secondary)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--text-primary)' }}
                        />
                    </div>
                    <div>
                        <label style={{ fontSize: 11, color: 'var(--text-secondary)', display: 'block', marginBottom: 4 }}>Type</label>
                        <select
                            value={form.streamType}
                            onChange={(e) => setForm((f) => ({ ...f, streamType: e.target.value }))}
                            style={{ width: '100%', padding: '8px 10px', background: 'var(--bg-secondary)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--text-primary)' }}
                        >
                            {STREAM_TYPES.map((o) => (
                                <option key={o.value} value={o.value}>{o.label}</option>
                            ))}
                        </select>
                    </div>
                    <div>
                        <label style={{ fontSize: 11, color: 'var(--text-secondary)', display: 'block', marginBottom: 4 }}>Zone</label>
                        <input
                            type="text"
                            placeholder="default"
                            value={form.zone}
                            onChange={(e) => setForm((f) => ({ ...f, zone: e.target.value }))}
                            style={{ width: '100%', padding: '8px 10px', background: 'var(--bg-secondary)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--text-primary)' }}
                        />
                    </div>
                    <div style={{ display: 'flex', gap: 8 }}>
                        <button
                            type="button"
                            onClick={handleStart}
                            disabled={adding}
                            className="btn btn-primary"
                            style={{ display: 'flex', alignItems: 'center', gap: 6 }}
                        >
                            {adding ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} />}
                            Start detection
                        </button>
                        {pipelineState === 'running' && (
                            <button type="button" onClick={handleStopPipeline} className="btn" style={{ background: 'var(--accent-rose)', color: '#fff' }}>
                                <Square size={16} /> Stop all
                            </button>
                        )}
                    </div>
                </div>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(350px, 1fr))', gap: 16 }}>
                {activeCameras.length === 0 ? (
                    <div className="card animate-in">
                        <div className="card-body" style={{ textAlign: 'center', padding: 40, color: 'var(--text-secondary)' }}>
                            <Camera size={48} style={{ opacity: 0.4, marginBottom: 12 }} />
                            <p>No cameras running. Add a source above and click &quot;Start detection&quot;.</p>
                            <p style={{ fontSize: 12, marginTop: 8 }}>Use source <code>0</code> for default webcam, or a path to a .mp4 file.</p>
                        </div>
                    </div>
                ) : (
                    activeCameras.map((camId) => {
                        const stats = cameraStats[camId] || {};
                        const dets = results[camId] || [];
                        const isActive = stats.connected ?? true;
                        return (
                            <div key={camId} className="card animate-in">
                                <div className="card-header">
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                        <Camera size={16} />
                                        <span className="card-title">Camera {camId}</span>
                                    </div>
                                    <span className={`badge ${isActive ? 'badge-success' : 'badge-neutral'}`}>
                                        {isActive ? 'active' : 'connecting'}
                                    </span>
                                </div>
                                <div className="card-body">
                                    <div style={{
                                        height: 200, borderRadius: 8,
                                        background: '#000',
                                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                                        border: '1px solid var(--border)', marginBottom: 12,
                                        position: 'relative', overflow: 'hidden',
                                    }}>
                                        {isActive && token ? (
                                            <>
                                                <div style={{ position: 'absolute', top: 8, left: 8, zIndex: 2, fontSize: 10, color: '#f43f5e', display: 'flex', alignItems: 'center', gap: 4, fontWeight: 600 }}>
                                                    <span style={{ width: 6, height: 6, background: '#f43f5e', borderRadius: '50%' }} /> LIVE
                                                </div>
                                                <div style={{ position: 'absolute', top: 8, right: 8, zIndex: 2, fontSize: 10, color: 'var(--text-muted)' }}>{stats.fps != null ? Number(stats.fps).toFixed(1) : '—'} FPS</div>
                                                <img
                                                    src={`${API_BASE}/stream/camera/${camId}/live?token=${encodeURIComponent(token)}`}
                                                    alt={`Camera ${camId}`}
                                                    style={{ width: '100%', height: '100%', objectFit: 'contain' }}
                                                />
                                            </>
                                        ) : isActive ? (
                                            <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>Loading stream…</span>
                                        ) : (
                                            <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>Connecting…</span>
                                        )}
                                    </div>
                                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8, fontSize: 12, color: 'var(--text-secondary)' }}>
                                        <span>{dets.length} persons detected</span>
                                        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                                            {recordingCameras.includes(camId) ? (
                                                <button type="button" onClick={() => handleStopRecording(camId)} className="btn" style={{ fontSize: 11, padding: '4px 8px', background: 'var(--accent-rose)', color: '#fff' }}>
                                                    <SquareIcon size={12} style={{ marginRight: 4 }} /> Stop recording
                                                </button>
                                            ) : (
                                                <button type="button" onClick={() => handleStartRecording(camId)} className="btn btn-primary" style={{ fontSize: 11, padding: '4px 8px' }}>
                                                    <Circle size={12} style={{ marginRight: 4 }} /> Record
                                                </button>
                                            )}
                                            <button type="button" onClick={() => handleStopCamera(camId)} className="btn" style={{ fontSize: 11, padding: '4px 8px' }}>Stop feed</button>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        );
                    })
                )}
            </div>
        </div>
    );
}
