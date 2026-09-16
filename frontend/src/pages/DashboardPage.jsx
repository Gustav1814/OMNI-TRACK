import React, { useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import {
    Activity, Users, Flame, Camera, Zap, ShieldCheck, Clock3,
    TrendingUp, PlayCircle, StopCircle, RefreshCw, AlertTriangle,
} from 'lucide-react';
import {
    Chart as ChartJS,
    CategoryScale,
    LinearScale,
    PointElement,
    LineElement,
    Tooltip,
    Filler,
    Legend,
} from 'chart.js';
import { Line } from 'react-chartjs-2';
import {
    dashboardAPI, systemAPI, pipelineAPI, detectionAPI, fireAPI, vibeAPI,
} from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import useWebSocket from '../hooks/useWebSocket';
import CameraStream from '../components/CameraStream';

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip, Filler, Legend);

const ACCENT_BY_KEY = {
    violet: { rgb: '124,156,184' },
    cyan: { rgb: '124,156,184' },
    amber: { rgb: '251,191,36' },
    rose: { rgb: '251,113,133' },
    emerald: { rgb: '52,211,153' },
    sky: { rgb: '124,156,184' },
};

function KPI({
    icon: Icon, label, value, suffix, trend, accent = 'violet', progress = 0, tag = 'Live',
}) {
    const rgb = ACCENT_BY_KEY[accent]?.rgb || ACCENT_BY_KEY.violet.rgb;
    const [tilt, setTilt] = useState({ x: 0, y: 0 });
    const onMove = (e) => {
        const rect = e.currentTarget.getBoundingClientRect();
        const x = ((e.clientX - rect.left) / rect.width - 0.5) * 16;
        const y = ((e.clientY - rect.top) / rect.height - 0.5) * -12;
        setTilt({ x, y });
    };
    return (
        <motion.div
            className={`stat-card stat-card-${accent}`}
            transition={{ type: 'spring', stiffness: 300, damping: 20 }}
            onMouseMove={onMove}
            onMouseLeave={() => setTilt({ x: 0, y: 0 })}
        >
            <div
                className="stat-card-inner"
                style={{ transform: `perspective(900px) rotateY(${tilt.x}deg) rotateX(${tilt.y}deg)` }}
            >
                <div className="stat-card-orb" aria-hidden />
                <div className="stat-card-top">
                    <div className={`stat-icon stat-icon-${accent}`}><Icon size={20} /></div>
                    <div className={`stat-chip stat-chip-${accent}`}>{tag}</div>
                </div>
                <div className="stat-card-mid">
                    <div className="stat-label">{label}</div>
                    <div className="stat-value">
                        {value}{suffix ? <span className="stat-suffix">{suffix}</span> : null}
                    </div>
                </div>
                <div className="stat-card-foot">
                    <div className="stat-trend-pill">
                        <TrendingUp size={11} />
                        {trend != null ? `${trend >= 0 ? '+' : ''}${trend}%` : 'live'}
                    </div>
                    <div className="stat-progress">
                        <span style={{
                            width: `${Math.max(8, Math.min(100, progress))}%`,
                            background: `linear-gradient(90deg, rgba(${rgb},0.95), rgba(${rgb},0.45))`,
                        }}
                        />
                    </div>
                </div>
            </div>
        </motion.div>
    );
}

export default function DashboardPage() {
    const { data: overview, refresh: refreshOverview } = useLivePoll(
        () => dashboardAPI.overview(), { intervalMs: 5000 }
    );
    const { data: health } = useLivePoll(() => systemAPI.health(), { intervalMs: 10000 });
    const { data: pipelineStatus, refresh: refreshPipeline } = useLivePoll(
        () => pipelineAPI.status(), { intervalMs: 4000 }
    );
    const { data: detStatus } = useLivePoll(() => detectionAPI.status(), { intervalMs: 3000 });
    const { data: fireAlerts } = useLivePoll(() => fireAPI.alerts(), { intervalMs: 8000 });
    const { data: vibeTrend } = useLivePoll(() => vibeAPI.trend(24), { intervalMs: 60000 });

    const [events, setEvents] = useState([]);
    const [activeFire, setActiveFire] = useState(null);
    const [liveVibe, setLiveVibe] = useState(null);
    const { status: wsStatus } = useWebSocket('/ws/live', {
        onEvent: (evt) => {
            setEvents((prev) => [evt, ...prev].slice(0, 30));
            if (evt.type === 'fire_alert') setActiveFire(evt.data);
            if (evt.type === 'vibe_update') setLiveVibe(evt.data);
        },
    });

    const [busy, setBusy] = useState(false);
    const vibe = overview?.store_vibe || {};
    const vibeScore = liveVibe?.overall_score ?? vibe.overall_score ?? 0;
    const vibeLabel = liveVibe?.label ?? vibe.vibe_label ?? '—';

    const activeCameras = useMemo(() => {
        const ids = detStatus?.active_cameras;
        if (Array.isArray(ids)) return ids;
        if (pipelineStatus?.cameras) {
            return Object.keys(pipelineStatus.cameras?.zones || {}).map((n) => Number(n));
        }
        return [];
    }, [detStatus, pipelineStatus]);

    const cameraStats = detStatus?.camera_stats || {};
    const cameraZones = pipelineStatus?.cameras?.zones || {};
    const isRunning = pipelineStatus?.state === 'running';
    const frameCounts = pipelineStatus?.processing?.frame_counts || {};
    const framesProcessed = Object.values(frameCounts).reduce((a, b) => a + Number(b || 0), 0);

    const trendData = Array.isArray(vibeTrend) ? vibeTrend.slice(0, 36).map((v, i) => ({
        t: typeof v.hour === 'string' ? v.hour.slice(11, 16) : `T-${i}`,
        score: Number(v.score) || 0,
    })).reverse() : [];

    const occupancySeries = trendData.map((d) => Math.max(0, Math.min(100, d.score - 8)));

    const chartData = {
        labels: trendData.map((_, i) => {
            const h = trendData.length - i;
            return `${h}h`;
        }),
        datasets: [
            {
                label: 'Energy',
                data: trendData.map((d) => d.score),
                borderColor: 'rgba(61,102,133,0.9)',
                backgroundColor: (ctx) => {
                    const chart = ctx.chart;
                    const { ctx: canvas, chartArea } = chart;
                    if (!chartArea) return 'rgba(61,102,133,0.25)';
                    const gradient = canvas.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
                    gradient.addColorStop(0, 'rgba(61,102,133,0.35)');
                    gradient.addColorStop(1, 'rgba(61,102,133,0.02)');
                    return gradient;
                },
                fill: true,
                tension: 0.45,
                pointRadius: 0,
                pointHoverRadius: 4,
                pointHoverBackgroundColor: 'rgba(157,188,212,1)',
                pointHoverBorderColor: '#000',
                pointHoverBorderWidth: 2,
                borderWidth: 2.2,
            },
            {
                label: 'Engagement',
                data: occupancySeries,
                borderColor: 'rgba(34,211,238,0.7)',
                backgroundColor: (ctx) => {
                    const chart = ctx.chart;
                    const { ctx: canvas, chartArea } = chart;
                    if (!chartArea) return 'rgba(34,211,238,0.12)';
                    const gradient = canvas.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
                    gradient.addColorStop(0, 'rgba(34,211,238,0.18)');
                    gradient.addColorStop(1, 'rgba(34,211,238,0.01)');
                    return gradient;
                },
                fill: true,
                tension: 0.42,
                pointRadius: 0,
                pointHoverRadius: 4,
                pointHoverBackgroundColor: 'rgba(34,211,238,0.95)',
                pointHoverBorderColor: '#000',
                pointHoverBorderWidth: 2,
                borderWidth: 1.8,
            },
        ],
    };

    const chartOptions = {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { intersect: false, mode: 'index' },
        plugins: {
            legend: {
                display: false,
            },
            tooltip: {
                backgroundColor: 'rgba(10,10,20,0.95)',
                borderColor: 'rgba(255,255,255,0.1)',
                borderWidth: 1,
                titleColor: 'rgba(255,255,255,0.9)',
                bodyColor: 'rgba(255,255,255,0.78)',
                padding: 10,
                cornerRadius: 8,
                displayColors: false,
            },
        },
        scales: {
            x: {
                grid: { color: 'rgba(255,255,255,0.03)' },
                ticks: { color: 'rgba(255,255,255,0.2)', maxTicksLimit: 10, font: { size: 9 } },
                border: { color: 'rgba(255,255,255,0.06)' },
            },
            y: {
                min: 0,
                max: 100,
                grid: { color: 'rgba(255,255,255,0.03)' },
                ticks: { color: 'rgba(255,255,255,0.2)', stepSize: 20, font: { size: 9 } },
                border: { color: 'rgba(255,255,255,0.06)' },
            },
        },
    };

    const togglePipeline = async () => {
        setBusy(true);
        try {
            if (pipelineStatus?.state === 'running') await pipelineAPI.stop();
            else await pipelineAPI.start();
            await Promise.all([refreshPipeline(), refreshOverview()]);
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="page-scroll dashboard-shell">

            <div className={`dashboard-hero ${isRunning ? 'is-running' : ''}`}>
                <div className="dashboard-hero-lead">
                    <span className={`hero-status ${isRunning ? 'hero-status-live' : ''}`}>
                        <i className="hero-status-dot" />
                        {isRunning ? 'Session running' : 'Session idle'}
                    </span>
                    <h1 className="page-title dashboard-hero-title">Command Center</h1>
                    <p className="page-subtitle">
                        {isRunning
                            ? `Tracking ${activeCameras.length} ${activeCameras.length === 1 ? 'feed' : 'feeds'} · ${framesProcessed.toLocaleString()} frames processed`
                            : 'Upload a store video on Video Feeds, then start a session to see live analytics.'}
                    </p>
                </div>
                <div className="dashboard-hero-actions">
                    <div className="hero-stat">
                        <span className="hero-stat-value">{vibeLabel}</span>
                        <span className="hero-stat-label">Store vibe</span>
                    </div>
                    <button
                        type="button"
                        className={`btn ${isRunning ? 'btn-outline' : 'btn-primary'} hero-cta`}
                        onClick={togglePipeline}
                        disabled={busy}
                    >
                        {isRunning ? <StopCircle size={15} /> : <PlayCircle size={15} />}
                        {busy ? 'Working…' : isRunning ? 'Stop session' : 'Start session'}
                    </button>
                </div>
            </div>

            {activeFire && (
                <div className="fire-banner">
                    <div className="fire-banner-icon"><AlertTriangle size={22} /></div>
                    <div className="fire-banner-content">
                        <div className="fire-banner-title">{activeFire.alert_type?.toUpperCase() || 'FIRE'} DETECTED</div>
                        <div className="fire-banner-meta">
                            Camera {activeFire.camera_id} · {activeFire.zone || 'unknown zone'} ·
                            confidence {Math.round((activeFire.confidence || 0) * 100)}%
                        </div>
                    </div>
                    <button className="btn btn-secondary btn-xs" onClick={() => setActiveFire(null)} type="button">Dismiss</button>
                </div>
            )}

            <div className="stats-grid dashboard-stats">
                <KPI
                    icon={Camera}
                    label="Active Feeds"
                    value={overview?.active_cameras ?? activeCameras.length ?? 0}
                    suffix={`/${overview?.total_cameras ?? pipelineStatus?.cameras?.total ?? 0}`}
                    accent="violet"
                    progress={((overview?.active_cameras ?? activeCameras.length ?? 0) / Math.max(1, overview?.total_cameras ?? pipelineStatus?.cameras?.total ?? 1)) * 100}
                    tag={`${overview?.active_cameras ?? activeCameras.length ?? 0}/${overview?.total_cameras ?? pipelineStatus?.cameras?.total ?? 0}`}
                />
                <KPI
                    icon={Users}
                    label="Current Occupancy"
                    value={overview?.current_occupancy ?? 0}
                    accent="violet"
                    progress={Math.min(100, Number(overview?.current_occupancy ?? 0))}
                    tag={isRunning ? 'Live' : 'Idle'}
                />
                <KPI
                    icon={Zap}
                    label="Detections Today"
                    value={(overview?.total_detections_today ?? 0).toLocaleString?.() ?? 0}
                    accent="violet"
                    progress={Math.min(100, Number((overview?.total_detections_today ?? 0) / 20))}
                    tag="Today"
                />
                <KPI
                    icon={Activity}
                    label="Store Vibe"
                    value={Number(vibeScore).toFixed(0)}
                    suffix=""
                    accent="emerald"
                    progress={Number(vibeScore) || 0}
                    tag={vibeLabel}
                />
                <KPI
                    icon={Flame}
                    label="Fire Alerts Today"
                    value={overview?.fire_alerts_today ?? (fireAlerts?.length || 0)}
                    accent="rose"
                    progress={Math.min(100, Number((overview?.fire_alerts_today ?? (fireAlerts?.length || 0)) * 22))}
                    tag={(overview?.fire_alerts_today ?? (fireAlerts?.length || 0)) > 0 ? 'Action needed' : 'All clear'}
                />
                <KPI
                    icon={Clock3}
                    label="Queue Wait"
                    value={Number(overview?.avg_checkout_wait ?? 0).toFixed(1)}
                    suffix="s"
                    accent="violet"
                    progress={Math.min(100, Number(overview?.avg_checkout_wait ?? 0) * 4)}
                    tag="Average"
                />
            </div>

            <div className="two-col dashboard-main-grid">
                <div className="card dashboard-panel dashboard-panel-chart">
                    <div className="card-header">
                        <div>
                            <h3 className="card-title">Store Pulse</h3>
                            <div className="card-subtitle chart-legend-inline">
                                <span>—</span>
                                <span className="legend-energy">■ Energy</span>
                                <span className="legend-engagement">■ Engagement</span>
                            </div>
                        </div>
                        <div className="card-subtitle">Rolling 24h</div>
                    </div>
                    <div className="chart-area">
                        {trendData.length > 0 ? (
                            <Line data={chartData} options={chartOptions} />
                        ) : (
                            <div className="chart-empty-state">
                                <div className="chart-empty-icon"><Activity size={22} /></div>
                                <p className="chart-empty-title">No trend data yet</p>
                                <p className="chart-empty-copy">
                                    Energy and engagement plot here once a session has been
                                    running for a few minutes.
                                </p>
                                {!isRunning && (
                                    <button type="button" className="btn btn-primary" onClick={togglePipeline} disabled={busy}>
                                        <PlayCircle size={15} />
                                        {busy ? 'Working…' : 'Start session'}
                                    </button>
                                )}
                            </div>
                        )}
                    </div>
                </div>

                <div className="card dashboard-panel dashboard-panel-health">
                    <div className="card-header">
                        <h3 className="card-title">Operational Health</h3>
                        <div className="health-status-pill">
                            <span className="health-status-dot" />
                            All systems
                        </div>
                    </div>
                    <div style={{ display: 'grid', gap: 10 }}>
                        <HealthRow label="Session state" value={pipelineStatus?.state || 'idle'} ok={pipelineStatus?.state === 'running'} />
                        <HealthRow label="Database" value={health?.components?.database || '—'} ok={health?.components?.database === 'healthy'} />
                        <HealthRow label="Cache" value={health?.components?.redis?.status || health?.components?.redis || '—'} />
                        <HealthRow label="Live viewers" value={health?.components?.websocket?.active_connections ?? 0} />
                        <HealthRow label="Re-ID gallery" value={`${pipelineStatus?.ai_modules?.reid?.gallery_size ?? 0} embeddings`} />
                        <HealthRow
                            label="Frames processed (all feeds)"
                            value={framesProcessed.toLocaleString()}
                        />
                        <div className="health-empty-box">
                            {activeCameras.length > 0 ? `${activeCameras.length} active feed(s) running` : <><span>No active feeds running</span><br/><span>Upload a video → Start Session</span></>}
                        </div>
                    </div>
                </div>
            </div>

            <div className="card dashboard-section dashboard-panel">
                <div className="card-header">
                    <h3 className="card-title">Live Feeds</h3>
                    <div className="card-subtitle">
                        {activeCameras.length
                            ? `${activeCameras.length} active video stream${activeCameras.length > 1 ? 's' : ''}`
                            : 'No feeds running - add a video on the Video Feeds page'}
                    </div>
                </div>
                {activeCameras.length > 0 ? (
                    <div className="camera-grid">
                        {activeCameras.map((id) => {
                            const s = cameraStats[id] || cameraStats[String(id)] || {};
                            return (
                                <CameraStream
                                    key={id}
                                    cameraId={id}
                                    label={`Camera ${id}`}
                                    zone={cameraZones[id] || cameraZones[String(id)]}
                                    fps={s.fps ?? s.fps_actual}
                                    connected={s.connected !== false}
                                />
                            );
                        })}
                    </div>
                ) : (
                    <div className="dashboard-empty-note">
                        No active feeds yet. Upload a store video first, then press Start Session.
                    </div>
                )}
            </div>

            <div className="card dashboard-section dashboard-panel">
                <div className="card-header">
                    <h3 className="card-title">Live Activity Stream</h3>
                    <div className="card-subtitle">Real-time highlights from detections, safety, and shopper movement</div>
                </div>
                {events.length === 0 ? (
                    <div className="dashboard-empty-note">
                        Waiting for live events...
                    </div>
                ) : (
                    <ul className="event-list">
                        {events.map((e, i) => (
                            <li key={i}>
                                <span className={`pill ${pillForEvent(e.type)}`}>{eventLabel(e.type)}</span>
                                <code className="event-payload">
                                    {JSON.stringify(e.data)}
                                </code>
                                <span className="event-time">
                                    {new Date(e.timestamp).toLocaleTimeString()}
                                </span>
                            </li>
                        ))}
                    </ul>
                )}
            </div>
        </div>
    );
}

function HealthRow({ label, value, ok }) {
    const statusClass = ok === true ? 'ok' : ok === false ? 'warn' : 'idle';
    return (
        <div className="health-row">
            <span className="health-row-label">
                <i className={`health-dot ${statusClass}`} />
                {label}
            </span>
            <span className={`health-row-value ${statusClass}`}>{String(value)}</span>
        </div>
    );
}

function pillForEvent(type) {
    switch (type) {
        case 'fire_alert': return 'pill-danger';
        case 'crowd_alert': return 'pill-warn';
        case 'reid_match': return 'pill-info';
        case 'vibe_update': return 'pill-success';
        default: return '';
    }
}

function eventLabel(type) {
    switch (type) {
        case 'fire_alert': return 'Safety alert';
        case 'crowd_alert': return 'Crowd alert';
        case 'reid_match': return 'Cross-feed match';
        case 'vibe_update': return 'Pulse update';
        case 'detection_update': return 'Detection update';
        default: return 'Live event';
    }
}
