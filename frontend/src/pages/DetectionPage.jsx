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
    BrainCircuit, Receipt, ShoppingCart, AlertTriangle, PackagePlus, Store, ScanLine,
} from 'lucide-react';
import {
    detectionAPI, pipelineAPI, footageAPI, modelAPI, humanlessAPI,
} from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import useWebSocket from '../hooks/useWebSocket';
import CameraStream from '../components/CameraStream';
import ContentCard from '../components/ui/ContentCard';
import StatusTile from '../components/ui/StatusTile';

const money = (value) => `$${Number(value || 0).toFixed(2)}`;
const title = (value) => String(value || 'unknown').replace(/[_-]/g, ' ').replace(/\b\w/g, (m) => m.toUpperCase());
const moduleLabel = (name) => ({
    detector: 'Detector',
    tracker: 'Tracker',
    crowd: 'Crowd',
    reid: 'Re-ID',
    fire: 'Fire',
    emotion: 'Emotion',
    shelf: 'Shelf',
    checkout: 'Checkout',
}[name] || title(name));

function ModuleChip({ name, enabled, status }) {
    const ran = status?.ran;
    const tone = ran ? 'pill-success' : enabled ? 'pill-info' : 'pill-warning';
    const reason = status?.reason || (enabled ? 'allowed' : 'disabled by camera role');
    return (
        <span className={`pill ${tone}`} title={reason}>
            {moduleLabel(name)}
        </span>
    );
}

export default function DetectionPage() {
    const [form, setForm] = useState({
        cameraId: 1,
        streamType: 'file',
        source: '',
        zone: 'entrance',
        fps: 30,
        model: '',
    });
    const [models, setModels] = useState([]);
    const [selectedModelInfo, setSelectedModelInfo] = useState(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState(null);
    const [notice, setNotice] = useState(null);
    const [productForm, setProductForm] = useState({ sku: '', name: '', category: '', price: '' });
    const [zoneForm, setZoneForm] = useState({ zone_id: '', name: '', camera_id: '', product_id: '', current_stock: 0 });
    const [eventForm, setEventForm] = useState({
        session_id: '',
        global_id: '',
        product_id: '',
        shelf_zone_id: '',
        event_type: 'pickup',
        quantity_delta: 1,
        confidence: 0.85,
    });

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
    const { data: pipelineResults } = useLivePoll(
        () => pipelineAPI.results(), { intervalMs: 2500 }
    );
    const { data: humanlessOverview } = useLivePoll(
        () => humanlessAPI.overview(8), { intervalMs: 5000 }
    );
    const { data: cashierQueue } = useLivePoll(
        () => humanlessAPI.cashierQueue(null, 8), { intervalMs: 3000 }
    );
    const { data: products, refresh: refreshProducts } = useLivePoll(
        () => humanlessAPI.products(false), { intervalMs: 12000 }
    );
    const { data: zones, refresh: refreshZones } = useLivePoll(
        () => humanlessAPI.shelfZones(false), { intervalMs: 12000 }
    );
    const { data: alerts } = useLivePoll(
        () => humanlessAPI.alerts('open', 8), { intervalMs: 8000 }
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
    const cameraRoles = pipeState?.cameras?.roles || {};
    const cameraRoleRows = Object.entries(cameraRoles).map(([cameraId, role]) => ({
        cameraId,
        ...role,
        latest: pipelineResults?.[cameraId] || pipelineResults?.[Number(cameraId)] || null,
    }));
    const routerStats = {
        cameras: cameraRoleRows.length,
        shelf: cameraRoleRows.filter((r) => r.role === 'shelf').length,
        checkout: cameraRoleRows.filter((r) => r.role === 'checkout').length,
        safety: cameraRoleRows.filter((r) => r.role === 'safety').length,
    };
    const cashierRows = Array.isArray(cashierQueue) ? cashierQueue : [];
    const smartSessions = Array.isArray(humanlessOverview?.sessions) ? humanlessOverview.sessions : [];
    const productRows = Array.isArray(products) ? products : [];
    const zoneRows = Array.isArray(zones) ? zones : [];
    const alertRows = Array.isArray(alerts) ? alerts : [];
    const productOptions = productRows.filter((p) => p.is_active !== false);
    const zoneOptions = zoneRows.filter((z) => z.is_active !== false);
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
            const { cameraId, streamType, source, zone, fps, model } = form;
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

    const createProduct = async (e) => {
        e.preventDefault();
        if (!productForm.sku || !productForm.name) return;
        setBusy(true); setError(null);
        try {
            await humanlessAPI.createProduct({
                ...productForm,
                price: Number(productForm.price || 0),
            });
            setProductForm({ sku: '', name: '', category: '', price: '' });
            setNotice('Product added to catalog.');
            await refreshProducts();
        } catch (e) {
            setError(e?.response?.data?.detail || e.message);
        } finally { setBusy(false); }
    };

    const createZone = async (e) => {
        e.preventDefault();
        if (!zoneForm.zone_id || !zoneForm.name) return;
        setBusy(true); setError(null);
        try {
            await humanlessAPI.createShelfZone({
                zone_id: zoneForm.zone_id,
                name: zoneForm.name,
                camera_id: zoneForm.camera_id ? Number(zoneForm.camera_id) : null,
                product_id: zoneForm.product_id ? Number(zoneForm.product_id) : null,
                current_stock: Number(zoneForm.current_stock || 0),
                low_stock_threshold: 3,
            });
            setZoneForm({ zone_id: '', name: '', camera_id: '', product_id: '', current_stock: 0 });
            setNotice('Shelf zone mapped.');
            await refreshZones();
        } catch (e) {
            setError(e?.response?.data?.detail || e.message);
        } finally { setBusy(false); }
    };

    const createEvent = async (e) => {
        e.preventDefault();
        if (!eventForm.session_id && !eventForm.global_id) return;
        setBusy(true); setError(null);
        try {
            const qty = Number(eventForm.quantity_delta || 0);
            await humanlessAPI.createCartEvent({
                session_id: eventForm.session_id ? Number(eventForm.session_id) : null,
                global_id: eventForm.global_id || null,
                product_id: eventForm.product_id ? Number(eventForm.product_id) : null,
                shelf_zone_id: eventForm.shelf_zone_id ? Number(eventForm.shelf_zone_id) : null,
                event_type: eventForm.event_type,
                quantity_delta: eventForm.event_type === 'putback' ? -Math.abs(qty || 1) : Math.abs(qty || 1),
                confidence: Number(eventForm.confidence || 0.85),
                rule_source: 'operator_console',
                evidence: { source: 'video_feeds_page' },
            });
            setNotice('Cart event recorded.');
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
                <ContentCard
                    title="Add Video Feed"
                    subtitle="Primary flow: upload a video and run it as a virtual camera"
                    accent="teal"
                >
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
                                </div>
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
                </ContentCard>

                <ContentCard
                    title="Session Status"
                    subtitle="Live counters for your active video feeds"
                    accent="cyan"
                >
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
                </ContentCard>
            </div>

            <div className="two-col">
                <ContentCard
                    title="Camera Model Router"
                    subtitle="Automatic per-camera model decisions from area labels, shopper activity, and scheduler guardrails"
                >
                    <div className="ui-status-grid" style={{ marginBottom: 14 }}>
                        <StatusTile icon={Video} label="Routed feeds" value={routerStats.cameras} tone="sky" compact />
                        <StatusTile icon={ShoppingCart} label="Shelf roles" value={routerStats.shelf} tone="teal" compact />
                        <StatusTile icon={Receipt} label="Checkout roles" value={routerStats.checkout} tone="ok" compact />
                        <StatusTile icon={AlertTriangle} label="Safety roles" value={routerStats.safety} tone="warn" compact />
                    </div>
                    <div className="ui-feed-list" style={{ maxHeight: 330, overflow: 'auto' }}>
                        {cameraRoleRows.length === 0 ? (
                            <div className="page-empty-hint">
                                No routed feeds yet. Add area labels like entrance, aisle, shelf, checkout, or storage.
                            </div>
                        ) : cameraRoleRows.map((camera) => {
                            const modules = camera.modules || {};
                            const status = camera.latest?.model_status || {};
                            const latestReason = status.router?.reason || camera.reason || 'auto_role';
                            return (
                                <div key={camera.cameraId} className="ui-lane-row" style={{ gridTemplateColumns: '90px 96px 1fr' }}>
                                    <span className="ui-lane-row-title">Feed {camera.cameraId}</span>
                                    <span className="pill pill-info">{title(camera.role)}</span>
                                    <span>
                                        <span style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 6 }}>
                                            {Object.entries(modules).map(([name, enabled]) => (
                                                <ModuleChip key={name} name={name} enabled={enabled} status={status[name]} />
                                            ))}
                                        </span>
                                        <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                                            {latestReason}
                                        </span>
                                    </span>
                                </div>
                            );
                        })}
                    </div>
                </ContentCard>

                <ContentCard
                    title="Cart Handoff"
                    subtitle="Counter queue and active smart carts mapped from shopper identity instead of cart tracking"
                >
                    <div className="ui-status-grid" style={{ marginBottom: 14 }}>
                        <StatusTile icon={ShoppingCart} label="Open carts" value={humanlessOverview?.open_carts || 0} tone="teal" compact />
                        <StatusTile icon={Receipt} label="At counter" value={cashierRows.length} tone="sky" compact />
                        <StatusTile icon={BrainCircuit} label="Adaptive gating" value={pipeState?.processing?.adaptive_model_gating ? 'On' : 'Off'} tone={pipeState?.processing?.adaptive_model_gating ? 'ok' : 'warn'} compact />
                    </div>
                    <div className="ui-feed-list" style={{ maxHeight: 330, overflow: 'auto' }}>
                        {cashierRows.length === 0 && smartSessions.length === 0 ? (
                            <div className="page-empty-hint">
                                No cart handoffs yet. When a shopper reaches checkout, their bill appears here.
                            </div>
                        ) : [...cashierRows, ...smartSessions].slice(0, 6).map((session) => (
                            <div key={`${session.id}-${session.global_id}`} className="ui-lane-row" style={{ gridTemplateColumns: '120px 1fr 96px' }}>
                                <span className="ui-lane-row-title">{session.global_id || `Session ${session.id}`}</span>
                                <span>{session.last_zone || session.counter_id || 'shopping floor'}</span>
                                <span style={{ fontWeight: 700 }}>{money(session.cart?.subtotal)}</span>
                            </div>
                        ))}
                    </div>
                </ContentCard>
            </div>

            <div className="two-col">
                <ContentCard title="Product & Shelf Setup" subtitle="Planogram mapping for low-cost cart inference">
                    <div className="two-col" style={{ gap: 12 }}>
                        <form className="settings-form" onSubmit={createProduct}>
                            <input className="form-input" placeholder="SKU" value={productForm.sku} onChange={(e) => setProductForm((f) => ({ ...f, sku: e.target.value }))} />
                            <input className="form-input" placeholder="Product name" value={productForm.name} onChange={(e) => setProductForm((f) => ({ ...f, name: e.target.value }))} />
                            <input className="form-input" placeholder="Category" value={productForm.category} onChange={(e) => setProductForm((f) => ({ ...f, category: e.target.value }))} />
                            <input className="form-input" placeholder="Price" type="number" step="0.01" value={productForm.price} onChange={(e) => setProductForm((f) => ({ ...f, price: e.target.value }))} />
                            <button type="submit" className="topbar-pill topbar-pill-button" disabled={busy}>
                                <PackagePlus size={13} />
                                Add Product
                            </button>
                        </form>
                        <form className="settings-form" onSubmit={createZone}>
                            <input className="form-input" placeholder="Zone ID, e.g. aisle1-drinks" value={zoneForm.zone_id} onChange={(e) => setZoneForm((f) => ({ ...f, zone_id: e.target.value }))} />
                            <input className="form-input" placeholder="Zone name" value={zoneForm.name} onChange={(e) => setZoneForm((f) => ({ ...f, name: e.target.value }))} />
                            <input className="form-input" placeholder="Camera ID" type="number" value={zoneForm.camera_id} onChange={(e) => setZoneForm((f) => ({ ...f, camera_id: e.target.value }))} />
                            <select className="form-input" value={zoneForm.product_id} onChange={(e) => setZoneForm((f) => ({ ...f, product_id: e.target.value }))}>
                                <option value="">No product yet</option>
                                {productOptions.map((p) => (
                                    <option key={p.id} value={p.id}>{p.name}</option>
                                ))}
                            </select>
                            <input className="form-input" placeholder="Current stock" type="number" value={zoneForm.current_stock} onChange={(e) => setZoneForm((f) => ({ ...f, current_stock: e.target.value }))} />
                            <button type="submit" className="topbar-pill topbar-pill-button" disabled={busy}>
                                <Store size={13} />
                                Add Zone
                            </button>
                        </form>
                    </div>
                    <div className="ui-feed-list" style={{ marginTop: 14, maxHeight: 220, overflow: 'auto' }}>
                        {[...productRows.slice(0, 4).map((p) => ({ id: `p-${p.id}`, name: p.name, meta: p.sku, value: money(p.price) })),
                          ...zoneRows.slice(0, 4).map((z) => ({ id: `z-${z.id}`, name: z.name, meta: `Cam ${z.camera_id || '-'}`, value: `${z.current_stock} left` }))].map((item) => (
                            <div key={item.id} className="ui-lane-row" style={{ gridTemplateColumns: '1fr 90px 86px' }}>
                                <span className="ui-lane-row-title">{item.name}</span>
                                <span>{item.meta}</span>
                                <span>{item.value}</span>
                            </div>
                        ))}
                    </div>
                </ContentCard>

                <ContentCard title="Manual Correction & Review" subtitle="Operator fallback for demos, low-confidence events, and training data">
                    <form className="settings-form" onSubmit={createEvent}>
                        <select className="form-input" value={eventForm.session_id} onChange={(e) => setEventForm((f) => ({ ...f, session_id: e.target.value }))}>
                            <option value="">Use global ID instead</option>
                            {smartSessions.map((s) => (
                                <option key={s.id} value={s.id}>{s.global_id} / session {s.id}</option>
                            ))}
                        </select>
                        <input className="form-input" value={eventForm.global_id} placeholder="PERSON-00042" onChange={(e) => setEventForm((f) => ({ ...f, global_id: e.target.value }))} />
                        <select className="form-input" value={eventForm.product_id} onChange={(e) => setEventForm((f) => ({ ...f, product_id: e.target.value }))}>
                            <option value="">Infer from shelf zone</option>
                            {productOptions.map((p) => (
                                <option key={p.id} value={p.id}>{p.name} / {money(p.price)}</option>
                            ))}
                        </select>
                        <select className="form-input" value={eventForm.shelf_zone_id} onChange={(e) => setEventForm((f) => ({ ...f, shelf_zone_id: e.target.value }))}>
                            <option value="">No shelf zone</option>
                            {zoneOptions.map((z) => (
                                <option key={z.id} value={z.id}>{z.name}</option>
                            ))}
                        </select>
                        <div className="two-col" style={{ gap: 10 }}>
                            <select className="form-input" value={eventForm.event_type} onChange={(e) => setEventForm((f) => ({ ...f, event_type: e.target.value }))}>
                                <option value="pickup">Pickup</option>
                                <option value="putback">Putback</option>
                                <option value="adjustment">Adjustment</option>
                                <option value="uncertain">Uncertain</option>
                            </select>
                            <input className="form-input" type="number" value={eventForm.quantity_delta} onChange={(e) => setEventForm((f) => ({ ...f, quantity_delta: e.target.value }))} />
                        </div>
                        <button type="submit" className="topbar-pill topbar-pill-primary" disabled={busy}>
                            <ScanLine size={13} />
                            Record Event
                        </button>
                    </form>
                    <div className="ui-feed-list" style={{ marginTop: 14, maxHeight: 220, overflow: 'auto' }}>
                        {alertRows.length === 0 ? (
                            <div className="page-empty-hint">No open review alerts.</div>
                        ) : alertRows.map((a) => (
                            <div key={a.id} className="ui-lane-row" style={{ gridTemplateColumns: '1fr 92px 70px' }}>
                                <span className="ui-lane-row-title">{a.description || a.alert_type}</span>
                                <span className="pill pill-warning">{a.severity}</span>
                                <span style={{ fontSize: 12 }}>{Math.round((a.confidence || 0) * 100)}%</span>
                            </div>
                        ))}
                    </div>
                </ContentCard>
            </div>

            <ContentCard
                title={<> <Video size={16} style={{ verticalAlign: -3, marginRight: 6 }} /> Active Video Feeds ({activeCameras.length}) </>}
                subtitle="People counts update in real time; FPS refreshes every 2 seconds"
                accent="sky"
            >
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
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                )}
            </ContentCard>
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
