import React, { useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { ArrowUpRight, ExternalLink } from 'lucide-react';
import {
    camerasAPI, liveStreamUrl, modelAPI, pipelineAPI, systemAPI, zonesAPI,
} from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import useUserRole from '../hooks/useUserRole';
import PageHeader from '../components/PageHeader';
import ZoneEditor from '../components/zones/ZoneEditor';
import FloorCalibration from '../components/zones/FloorCalibration';

const mb = (bytes) => (bytes ? `${(bytes / 1048576).toFixed(1)} MB` : '—');
const statusPill = (s) => (s === 'healthy' || s === 'ok' ? 'pill-success' : s ? 'pill-warn' : 'pill-neutral');

function PanelHead({ title, sub, children }) {
    return (
        <div className="panel-head">
            <div>
                <h2 className="panel-title">{title}</h2>
                {sub && <p className="panel-sub">{sub}</p>}
            </div>
            {children}
        </div>
    );
}

const isStreaming = (pipeline, id) => {
    const st = pipeline?.cameras?.stats?.[String(id)];
    return Boolean(st && st.connected !== false && !st.finished);
};

const clock = (d) => (d ? d.toLocaleTimeString([], { hour12: false }) : '');

/** One real frame per camera: the live MJPEG while the pipeline runs it, else the authed snapshot. */
function CameraFrame({ camera, streaming }) {
    const [snap, setSnap] = useState({ url: null, at: null, error: null });
    const [liveFailed, setLiveFailed] = useState(false);
    const [now, setNow] = useState(() => new Date());
    const urlRef = useRef(null);
    const live = streaming && !liveFailed;

    useEffect(() => { setLiveFailed(false); }, [streaming]);

    useEffect(() => {
        if (!live) return undefined;
        const t = setInterval(() => setNow(new Date()), 1000);
        return () => clearInterval(t);
    }, [live]);

    useEffect(() => {
        if (live) return undefined;
        let cancelled = false;
        zonesAPI.snapshot(camera.id)
            .then((res) => {
                if (cancelled) return;
                const next = URL.createObjectURL(res.data);
                if (urlRef.current) URL.revokeObjectURL(urlRef.current);
                urlRef.current = next;
                setSnap({ url: next, at: new Date(), error: null });
            })
            .catch(() => { if (!cancelled) setSnap({ url: null, at: null, error: 'No frame available yet' }); });
        return () => { cancelled = true; };
    }, [camera.id, live]);

    useEffect(() => () => { if (urlRef.current) URL.revokeObjectURL(urlRef.current); }, []);

    const w = Number(camera.resolution_w) || 4;
    const h = Number(camera.resolution_h) || 3;
    const name = camera.name || `Camera ${camera.id}`;
    return (
        <figure className="adm-cam">
            <div className="adm-frame vf vf--on-media" style={{ aspectRatio: `${w} / ${h}` }}>
                {live ? (
                    <img src={liveStreamUrl(camera.id)} alt={`Live view of ${name}`} onError={() => setLiveFailed(true)} />
                ) : snap.url ? (
                    <img src={snap.url} alt={`Last frame from ${name}`} />
                ) : (
                    <span className="adm-frame-msg">{snap.error || 'Loading a frame…'}</span>
                )}
                <div className="osd-strip" aria-hidden="true">
                    <span><i className={`live-dot ${live ? 'is-live' : ''}`} />{live ? 'Rec' : 'Snapshot'}</span>
                    <span>CAM {String(camera.id).padStart(2, '0')}</span>
                    <span>{live ? clock(now) : clock(snap.at)}</span>
                </div>
            </div>
            <figcaption className="adm-cam-meta">
                <span className="adm-cam-name">{name}</span>
                <span className={`pill ${streaming ? 'pill-success' : camera.is_active ? 'pill-neutral' : 'pill-warn'}`}>
                    {streaming ? 'Streaming' : camera.is_active ? 'Idle' : 'Inactive'}
                </span>
            </figcaption>
        </figure>
    );
}

const TABS = [
    { id: 'cameras', path: '/setup', label: 'Cameras' },
    { id: 'zones', path: '/setup/zones', label: 'Zones' },
    { id: 'models', path: '/setup/models', label: 'Models' },
    { id: 'integrations', path: '/setup/integrations', label: 'Integrations' },
    { id: 'users', path: '/setup/users', label: 'Access' },
];

function activeTabFromPath(pathname) {
    const clean = pathname.replace(/\/+$/, '') || '/setup';
    if (clean === '/setup/cameras') return 'cameras';
    return TABS.find((item) => item.path === clean)?.id || 'cameras';
}

const PIPELINE_PILL = { running: 'pill-success', starting: 'pill-warn', stopping: 'pill-warn', error: 'pill-danger' };

const capitalise = (s) => (s ? `${s.charAt(0).toUpperCase()}${s.slice(1)}` : s);

export default function SetupPage() {
    const location = useLocation();
    const navigate = useNavigate();
    const { canAdmin, canOperate, role } = useUserRole();
    const activeTab = activeTabFromPath(location.pathname);

    const { data: cameras } = useLivePoll(() => camerasAPI.list(), { intervalMs: 15000 });
    const { data: pipeline } = useLivePoll(() => pipelineAPI.status(), { intervalMs: 10000 });
    const { data: models } = useLivePoll(() => modelAPI.list(), { intervalMs: 30000 });
    const { data: zones } = useLivePoll(() => zonesAPI.list(), { intervalMs: 30000 });
    const { data: health } = useLivePoll(
        () => systemAPI.health(),
        { intervalMs: 12000, enabled: activeTab === 'integrations' }
    );

    const cameraRows = Array.isArray(cameras) ? cameras : [];
    const modelRows = Array.isArray(models?.models) ? models.models : [];
    const zoneCount = Array.isArray(zones?.zones) ? zones.zones.length : null;
    const streamingCount = cameraRows.filter((c) => isStreaming(pipeline, c.id)).length;
    const loadedModels = modelRows.filter((m) => m.loaded).length;
    const pipelineState = pipeline?.state || null;
    const comps = health?.components || {};
    const plugins = comps.plugins?.loaded || [];

    return (
        <div className="adm-page">
            <PageHeader
                title="Cameras & Zones"
                subtitle="The feeds OmniTrack watches, the zones analytics run inside, and the models behind them."
            />

            {cameras != null && (
                <div className="adm-overview">
                    <section className="panel panel--feature adm-feature" aria-label="Cameras">
                        <div className="panel-body">
                            <div className="adm-feature-head">
                                <span className="metric-label">Cameras registered</span>
                                <button type="button" className="corner-link" onClick={() => navigate('/detection')} aria-label="Open video feeds">
                                    <ArrowUpRight size={16} strokeWidth={1.75} />
                                </button>
                            </div>
                            <div className="metric metric--hero">
                                <span className="metric-value tabular">{cameraRows.length}</span>
                                <span className="metric-sub">
                                    {streamingCount > 0 ? `${streamingCount} streaming now` : 'None streaming right now'}
                                </span>
                            </div>
                        </div>
                    </section>
                    <section className="panel panel--flush adm-flush" aria-label="Pipeline">
                        <div className="panel-body">
                            <div className="adm-status">
                                <span className={`pill ${PIPELINE_PILL[pipelineState] || 'pill-neutral'}`}>
                                    {pipelineState ? `Pipeline ${pipelineState}` : 'Pipeline unknown'}
                                </span>
                                <h2 className="adm-status-title">
                                    {streamingCount > 0
                                        ? `${streamingCount} of ${cameraRows.length} cameras processing`
                                        : 'No camera is processing'}
                                </h2>
                            </div>
                            <div className="kpi-strip">
                                <div className="metric">
                                    <span className="metric-label">Zones drawn</span>
                                    <span className="metric-value tabular">{zoneCount ?? '—'}</span>
                                </div>
                                <div className="metric">
                                    <span className="metric-label">Models loaded</span>
                                    <span className="metric-value tabular">
                                        {models == null ? '—' : <>{loadedModels}<small>/{modelRows.length}</small></>}
                                    </span>
                                </div>
                                <div className="metric">
                                    <span className="metric-label">Re-ID scope</span>
                                    <span className="adm-text-value">{capitalise(pipeline?.reid_scope) || '—'}</span>
                                </div>
                            </div>
                        </div>
                    </section>
                </div>
            )}

            <nav className="adm-tabs" aria-label="Camera sections">
                {TABS.map((tab) => (
                    <button
                        key={tab.id}
                        type="button"
                        className="adm-tab"
                        aria-current={activeTab === tab.id ? 'page' : undefined}
                        onClick={() => navigate(tab.path)}
                    >
                        {tab.label}
                    </button>
                ))}
            </nav>

            {!canOperate ? <div className="alert-banner info">Read-only: your role can view cameras and zones but not change them.</div> : null}

            {activeTab === 'zones' && (
                <div className="panel">
                    <div className="panel-body">
                        <PanelHead
                            title="Analytics zones"
                            sub="Crowd, shelf, queue and footfall analytics only run inside the zones you draw on each camera."
                        />
                        <ZoneEditor cameras={cameraRows} canEdit={canAdmin} />
                    </div>
                </div>
            )}

            {activeTab === 'zones' && (
                <div className="panel">
                    <div className="panel-body">
                        <PanelHead
                            title="Floor calibration"
                            sub="Maps each camera onto one floor plan, so a person seen by two overlapping cameras is counted once."
                        />
                        <FloorCalibration cameras={cameraRows} canEdit={canAdmin} />
                    </div>
                </div>
            )}

            {activeTab === 'cameras' && (
                <div className="panel">
                    <div className="panel-body">
                        <PanelHead title="Cameras" sub="Cameras are registered when you add a video feed.">
                            <button type="button" className="btn btn-secondary btn-sm" onClick={() => navigate('/detection')}>
                                Open video feeds
                            </button>
                        </PanelHead>
                        {cameraRows.length === 0 ? (
                            <div className="empty-state">
                                <span className="empty-state-title">No cameras registered</span>
                                Add an RTSP stream or upload a clip in Video feeds. It appears here once registered.
                            </div>
                        ) : (
                            <>
                            <div className="adm-cams">
                                {cameraRows.map((camera) => (
                                    <CameraFrame key={camera.id} camera={camera} streaming={isStreaming(pipeline, camera.id)} />
                                ))}
                            </div>
                            <div className="table-scroll">
                                <table className="data-table adm-table adm-table--mid">
                                    <thead><tr><th>Camera</th><th>Zone</th><th>Type</th><th className="num">Resolution</th><th>Status</th></tr></thead>
                                    <tbody>
                                        {cameraRows.map((camera) => (
                                            <tr key={camera.id}>
                                                <td className="adm-strong">{camera.name || `Camera ${camera.id}`}</td>
                                                <td>{camera.zone || '—'}</td>
                                                <td>{camera.camera_type || 'general'}</td>
                                                <td className="num mono">{camera.resolution_w && camera.resolution_h ? `${camera.resolution_w}×${camera.resolution_h}` : '—'}</td>
                                                <td>
                                                    <span className={`pill ${isStreaming(pipeline, camera.id) ? 'pill-success' : camera.is_active ? 'pill-neutral' : 'pill-warn'}`}>
                                                        {isStreaming(pipeline, camera.id) ? 'Streaming' : camera.is_active ? 'Idle' : 'Inactive'}
                                                    </span>
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                            </>
                        )}
                    </div>
                </div>
            )}

            {activeTab === 'models' && (
                <div className="panel">
                    <div className="panel-body">
                        <PanelHead title="Model weights" sub="Files in the backend model directory. Each camera runs one person detector.">
                            <span className="count-badge">{modelRows.length} files</span>
                        </PanelHead>
                        {models == null ? (
                            <div className="empty-state">
                                <span className="empty-state-title">Loading model list…</span>
                                Reading the backend model directory.
                            </div>
                        ) : modelRows.length === 0 ? (
                            <div className="empty-state">
                                <span className="empty-state-title">No model weights found</span>
                                Run scripts/download_default_weights.py on the backend to fetch and verify them.
                            </div>
                        ) : (
                            <div className="table-scroll">
                                <table className="data-table adm-table adm-table--mid">
                                    <thead><tr><th>File</th><th>Role</th><th className="num">Size</th><th>Status</th></tr></thead>
                                    <tbody>
                                        {modelRows.map((model) => (
                                            <tr key={model.filename}>
                                                <td><span className="adm-mono adm-strong">{model.filename}</span></td>
                                                <td>{model.role || '—'}</td>
                                                <td className="num tabular">{mb(model.size_bytes)}</td>
                                                <td><span className={`pill ${model.loaded ? 'pill-success' : 'pill-neutral'}`}>{model.loaded ? 'Loaded' : 'On disk'}</span></td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        )}
                    </div>
                </div>
            )}

            {activeTab === 'integrations' && (
                <div className="panel">
                    <div className="panel-body">
                        <PanelHead title="Services" sub="Live from the backend health check, refreshed every 12 seconds." />
                        {!health ? (
                            <div className="empty-state">
                                <span className="empty-state-title">Checking services…</span>
                                Waiting for the backend health check.
                            </div>
                        ) : (
                            <dl className="adm-kv">
                                <dt>API</dt>
                                <dd><span className={`pill ${statusPill(health.status)}`}>{health.status || 'unknown'}</span>{health.version ? <span className="adm-mono"> v{health.version}</span> : null}</dd>
                                <dt>Database</dt>
                                <dd>
                                    <span className={`pill ${statusPill(comps.database?.status)}`}>{comps.database?.status || 'unknown'}</span>
                                    {comps.database?.revision ? <span className="adm-mono"> {comps.database.revision}{comps.database.pgvector ? ' · pgvector' : ''}</span> : null}
                                </dd>
                                <dt>Redis</dt>
                                <dd>
                                    <span className={`pill ${statusPill(comps.redis?.status)}`}>{comps.redis?.status || 'unknown'}</span>
                                    {comps.redis?.latency_ms != null ? <span className="adm-mono"> {comps.redis.latency_ms} ms</span> : null}
                                </dd>
                                <dt>Pipeline</dt>
                                <dd>{comps.pipeline ? <span className="tabular">{comps.pipeline.state} · {comps.pipeline.cameras_active} of {comps.pipeline.cameras_total} cameras active</span> : '—'}</dd>
                                <dt>WebSocket</dt>
                                <dd>{comps.websocket ? <span className="tabular">{comps.websocket.active_connections} open connections</span> : '—'}</dd>
                                <dt>Plugins</dt>
                                <dd>{plugins.length ? plugins.join(', ') : (comps.plugins?.enabled === false ? 'Disabled' : 'None loaded')}</dd>
                            </dl>
                        )}
                    </div>
                </div>
            )}

            {activeTab === 'users' && (
                <div className="panel">
                    <div className="panel-body">
                        <PanelHead title="Access" sub="Navigation follows your role. The backend enforces the same rules on every request." />
                        <dl className="adm-kv">
                            <dt>Your role</dt><dd>{role || '—'}</dd>
                            <dt>User management</dt><dd>{canAdmin ? 'Allowed' : 'Admins only'}</dd>
                            <dt>Camera and zone changes</dt><dd>{canOperate ? 'Allowed' : 'Read-only'}</dd>
                        </dl>
                        <div className="adm-form-actions">
                            <button type="button" className="btn btn-secondary btn-sm" onClick={() => navigate('/system/account')}>
                                {canAdmin ? 'Manage users' : 'Your account'}
                            </button>
                            {canAdmin && (
                                <button
                                    type="button"
                                    className="btn btn-ghost btn-sm"
                                    onClick={() => {
                                        const adminUrl = `${window.location.protocol}//${window.location.hostname}:8000/admin`;
                                        window.open(adminUrl, '_blank', 'noopener,noreferrer');
                                    }}
                                >
                                    Open SQLAdmin <ExternalLink size={14} strokeWidth={1.75} />
                                </button>
                            )}
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
