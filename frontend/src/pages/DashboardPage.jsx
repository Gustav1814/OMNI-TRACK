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
    violet: { rgb: '167,139,250' },
    cyan: { rgb: '34,211,238' },
    amber: { rgb: '251,191,36' },
    rose: { rgb: '251,113,133' },
    emerald: { rgb: '52,211,153' },
    sky: { rgb: '56,189,248' },
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
        () => dashboardAPI.overview(), { intervalMs: 15000 }
    );
    const { data: health } = useLivePoll(() => systemAPI.health(), { intervalMs: 30000 });
    const { data: pipelineStatus, refresh: refreshPipeline } = useLivePoll(
        () => pipelineAPI.status(), { intervalMs: 15000 }
    );
    const { data: detStatus } = useLivePoll(() => detectionAPI.status(), { intervalMs: 20000 });
    const { data: fireAlerts } = useLivePoll(() => fireAPI.alerts(), { intervalMs: 30000 });
    const { data: vibeTrend } = useLivePoll(() => vibeAPI.trend(24), { intervalMs: 120000 });

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
                borderColor: 'rgba(124,62,237,0.9)',
                backgroundColor: (ctx) => {
                    const chart = ctx.chart;
                    const { ctx: canvas, chartArea } = chart;
                    if (!chartArea) return 'rgba(124,62,237,0.25)';
                    const gradient = canvas.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
                    gradient.addColorStop(0, 'rgba(124,62,237,0.35)');
                    gradient.addColorStop(1, 'rgba(124,62,237,0.02)');
                    return gradient;
                },
                fill: true,
                tension: 0.45,
                pointRadius: 0,
                pointHoverRadius: 4,
                pointHoverBackgroundColor: 'rgba(167,139,250,1)',
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

    const liveViewers = health?.components?.websocket?.active_connections ?? 0;

    return (
        <div className="page-scroll dashboard-shell">

            <div className="dashboard-hero">
                <div className="dashboard-hero-lead">
                    <p className="dashboard-kicker"><span className="kicker-dash">—</span> Live Retail Intelligence</p>
                    <h1 className="page-title dashboard-hero-title">
                        Command <span className="hero-center-gradient">Center</span>
                    </h1>
                    <p className="page-subtitle">Real-time view across store videos and active feeds</p>
                </div>
                <div className="hero-kpis">
                    <div className="hero-kpi-box">
                        <div className="hero-kpi-value hero-kpi-cyan">{liveViewers}</div>
                        <div className="hero-kpi-label">Live<br/>Viewers</div>
                    </div>
                    <div className="hero-kpi-box">
                        <div className="hero-kpi-value hero-kpi-emerald">{Number(vibeScore || 0).toFixed(0)}</div>
                        <div className="hero-kpi-label">Store<br/>Vibe</div>
                    </div>
                    <div className="hero-kpi-box">
                        <div className="hero-kpi-value hero-kpi-amber">{Number(overview?.avg_checkout_wait ?? 0).toFixed(1)}s</div>
                        <div className="hero-kpi-label">Avg<br/>Wait</div>
                    </div>
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
                    accent="cyan"
                    progress={Math.min(100, Number(overview?.current_occupancy ?? 0))}
                    tag="Live"
                />
                <KPI
                    icon={Zap}
                    label="Detections Today"
                    value={(overview?.total_detections_today ?? 0).toLocaleString?.() ?? 0}
                    accent="amber"
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
                    tag="Steady"
                />
                <KPI
                    icon={Flame}
                    label="Fire Alerts Today"
                    value={overview?.fire_alerts_today ?? (fireAlerts?.length || 0)}
                    accent="rose"
                    progress={Math.min(100, Number((overview?.fire_alerts_today ?? (fireAlerts?.length || 0)) * 22))}
                    tag="Alerts"
                />
                <KPI
                    icon={Clock3}
                    label="Queue Wait"
                    value={Number(overview?.avg_checkout_wait ?? 0).toFixed(1)}
                    suffix="s"
                    accent="sky"
                    progress={Math.min(100, Number(overview?.avg_checkout_wait ?? 0) * 4)}
                    tag="Avg"
                />
            </div>

            <div className="two-col dashboard-main-grid">
                <div className="card dashboard-panel dashboard-panel-chart">
                    <div className="card-header">
                        <div>
                            <h3 className="card-title">Store<br/>Pulse</h3>
                            <div className="card-subtitle chart-legend-inline">
                                <span>—</span>
                                <span className="legend-energy">■ Energy</span>
                                <span className="legend-engagement">■ Engagement</span>
                            </div>
                        </div>
                        <div className="card-subtitle">Rolling<br/>24h</div>
                    </div>
                    <div className="chart-sublabel">24h Trend</div>
                    <div style={{ height: 310 }}>
                        {trendData.length > 0 ? (
                            <Line data={chartData} options={chartOptions} />
                        ) : (
                            <div style={{
                                height: '100%', display: 'grid', placeItems: 'center',
                                color: 'var(--text-muted)', fontSize: 13,
                            }}
                            >
                                No trend yet. Start session and let it run for a few minutes.
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
                            value={pipelineStatus?.frame_counts ? Object.values(pipelineStatus.frame_counts).reduce((a, b) => a + b, 0) : 0}
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
