import React, { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
    Activity, Users, Flame, Camera, Footprints, Clock3, AlertTriangle, Sparkles, Video,
    Database, Zap, LayoutDashboard, Radio, ShoppingCart, Receipt, ScanLine,
} from 'lucide-react';
import { Chart as ChartJS, CategoryScale, LinearScale, PointElement, LineElement, Tooltip, Filler } from 'chart.js';
import { Line } from 'react-chartjs-2';
import {
    dashboardAPI, systemAPI, pipelineAPI, detectionAPI, fireAPI, vibeAPI, humanlessAPI, setupAPI,
} from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import useWebSocket from '../hooks/useWebSocket';
import CameraStream from '../components/CameraStream';
import PageHeader from '../components/PageHeader';
import KPICard from '../components/KPICard';
import CountUp from '../components/CountUp';
import { useTheme } from '../contexts/ThemeContext';
import { useToast } from '../contexts/ToastContext';
import { buildLineChartOptions, buildLineDatasets } from '../lib/chartTheme';
import {
    PanelHeader,
    EmptyState,
    ActivityTimeline,
    ProgressRing,
    SegmentedControl,
    SkeletonKpiGrid,
    SkeletonChart,
    SkeletonCameraGrid,
    StatusTile,
    ExecPanel,
} from '../components/ui';

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip, Filler);

function formatEventMessage(type, data = {}) {
    switch (type) {
        case 'fire_alert':
            return `Safety alert in ${data.zone || 'store floor'} — immediate review recommended.`;
        case 'crowd_alert':
            return `Elevated footfall in ${data.zone || 'a busy zone'}. Consider opening another checkout lane.`;
        case 'reid_match':
            return `Returning shopper recognized across ${data.camera_id ? `camera ${data.camera_id}` : 'multiple cameras'}.`;
        case 'vibe_update':
            return `Store atmosphere shifted to ${data.label || 'a new mood'} (${Math.round(data.overall_score || 0)}/100).`;
        case 'detection_update':
            return `${data.count ?? data.person_count ?? 'Several'} shoppers visible in ${data.zone || 'the sales floor'}.`;
        default:
            return 'New activity recorded across your store network.';
    }
}

function eventBadgeClass(type) {
    switch (type) {
        case 'fire_alert': return 'safety';
        case 'crowd_alert': return 'crowd';
        case 'reid_match': return 'match';
        case 'vibe_update': return 'pulse';
        default: return 'default';
    }
}

function eventLabel(type) {
    switch (type) {
        case 'fire_alert': return 'Safety';
        case 'crowd_alert': return 'Traffic';
        case 'reid_match': return 'Journey';
        case 'vibe_update': return 'Atmosphere';
        case 'detection_update': return 'Visits';
        default: return 'Insight';
    }
}

function displayStatus(raw, runningLabel = 'Active', idleLabel = 'Standby') {
    if (raw === 'running' || raw === 'healthy' || raw === 'connected') return runningLabel;
    if (raw === 'idle' || raw === 'degraded') return idleLabel;
    if (typeof raw === 'object' && raw?.status) return displayStatus(raw.status, runningLabel, idleLabel);
    return raw ?? '—';
}

const money = (value) => `$${Number(value || 0).toFixed(2)}`;
const dt = (value) => (value ? new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '-');

export default function DashboardPage() {
    const { theme, gradientPreset } = useTheme();
    const { toast } = useToast();
    const navigate = useNavigate();
    const [chartRange, setChartRange] = useState('24h');

    const { data: overview, loading: overviewLoading } = useLivePoll(() => dashboardAPI.overview(), { intervalMs: 5000 });
    const { data: health } = useLivePoll(() => systemAPI.health(), { intervalMs: 10000 });
    const { data: pipelineStatus } = useLivePoll(() => pipelineAPI.status(), { intervalMs: 4000 });
    const { data: detStatus } = useLivePoll(() => detectionAPI.status(), { intervalMs: 3000 });
    const { data: fireAlerts } = useLivePoll(() => fireAPI.alerts(), { intervalMs: 8000 });
    const { data: setupProfile } = useLivePoll(() => setupAPI.profile(), { intervalMs: 30000 });
    const { data: humanlessOverview } = useLivePoll(() => humanlessAPI.overview(8), { intervalMs: 5000 });
    const { data: cashierQueue } = useLivePoll(() => humanlessAPI.cashierQueue(null, 8), { intervalMs: 3000 });
    const { data: humanlessAlerts } = useLivePoll(() => humanlessAPI.alerts('open', 8), { intervalMs: 10000 });
    const hours = chartRange === '7d' ? 168 : chartRange === '30d' ? 720 : 24;
    const { data: vibeTrend } = useLivePoll(() => vibeAPI.trend(hours), { intervalMs: 60000 });

    const [events, setEvents] = useState([]);
    const [activeFire, setActiveFire] = useState(null);
    const [liveVibe, setLiveVibe] = useState(null);

    useWebSocket('/ws/live', {
        onEvent: (evt) => {
            setEvents((prev) => [evt, ...prev].slice(0, 30));
            if (evt.type === 'fire_alert') {
                setActiveFire(evt.data);
                toast({
                    type: 'error',
                    title: 'Safety alert',
                    message: formatEventMessage('fire_alert', evt.data),
                });
            }
            if (evt.type === 'vibe_update') setLiveVibe(evt.data);
        },
    });

    const vibe = overview?.store_vibe || {};
    const vibeScore = liveVibe?.overall_score ?? vibe.overall_score ?? 0;
    const vibeLabel = liveVibe?.label ?? vibe.vibe_label ?? 'Balanced';
    const loading = overviewLoading && !overview;

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
    const totalCameras = overview?.total_cameras ?? pipelineStatus?.cameras?.total ?? 0;
    const activeCount = overview?.active_cameras ?? activeCameras.length ?? 0;

    const trendData = Array.isArray(vibeTrend) ? vibeTrend.slice(0, chartRange === '24h' ? 36 : 48).map((v, i) => ({
        score: Number(v.score) || 0,
        i,
    })).reverse() : [];

    const chartData = {
        labels: trendData.map((_, i) => `${trendData.length - i}h`),
        datasets: buildLineDatasets(theme, [
            { label: 'Energy', data: trendData.map((d) => d.score) },
            { label: 'Engagement', data: trendData.map((d) => Math.max(0, d.score - 8)) },
        ]),
    };

    const chartOptions = buildLineChartOptions(theme, {
        scales: {
            ...buildLineChartOptions(theme).scales,
            y: { ...buildLineChartOptions(theme).scales.y, max: 100, ticks: { ...buildLineChartOptions(theme).scales.y.ticks, stepSize: 25 } },
        },
    });

    const liveDashboards = health?.components?.websocket?.active_connections ?? 0;
    const frameCounts = pipelineStatus?.processing?.frame_counts || {};
    const framesProcessed = Object.values(frameCounts).reduce((a, b) => a + Number(b || 0), 0);
    const cashierRows = Array.isArray(cashierQueue) ? cashierQueue : [];
    const cashierlessSessions = Array.isArray(humanlessOverview?.sessions) ? humanlessOverview.sessions : [];
    const openCartSubtotal = Number(humanlessOverview?.subtotal_open || 0);
    const openCartCount = Number(humanlessOverview?.open_carts || 0);
    const openHumanlessAlerts = Number(humanlessOverview?.open_alerts ?? (Array.isArray(humanlessAlerts) ? humanlessAlerts.length : 0));

    return (
        <div className="page-scroll dashboard-shell">
            <PageHeader
                kicker="Retail intelligence · Live"
                title="Executive"
                highlight="Overview"
                subtitle="Store traffic, atmosphere, safety, and checkout — updated in real time."
            >
                <div className="dashboard-bento-hero">
                    <ProgressRing
                        value={Number(vibeScore) || 0}
                        label={Number(vibeScore).toFixed(0)}
                        sublabel={vibeLabel}
                        accent="emerald"
                        size={96}
                    />
                    <div className="dashboard-bento-hero-copy exec-hero-kpis">
                        <div className="exec-hero-kpi">
                            <div className="exec-hero-kpi-value teal">
                                {loading ? '—' : <CountUp value={liveDashboards} />}
                            </div>
                            <div className="exec-hero-kpi-label">Leaders viewing</div>
                        </div>
                        <div className="exec-hero-kpi">
                            <div className="exec-hero-kpi-value sky">
                                {loading ? '—' : <CountUp value={Number(vibeScore) || 0} format={(n) => n.toFixed(0)} />}
                            </div>
                            <div className="exec-hero-kpi-label">Atmosphere</div>
                        </div>
                        <div className="exec-hero-kpi">
                            <div className="exec-hero-kpi-value coral">
                                {loading ? '—' : <CountUp value={Number(overview?.avg_checkout_wait ?? 0)} format={(n) => n.toFixed(1)} suffix="s" />}
                            </div>
                            <div className="exec-hero-kpi-label">Checkout wait</div>
                        </div>
                    </div>
                </div>
            </PageHeader>

            {activeFire && (
                <div className="fire-banner">
                    <div className="fire-banner-icon"><AlertTriangle size={22} /></div>
                    <div className="fire-banner-content">
                        <div className="fire-banner-title">Safety alert — immediate attention</div>
                        <div className="fire-banner-meta">
                            {activeFire.zone || 'Store floor'} · Camera {activeFire.camera_id} ·
                            {' '}{Math.round((activeFire.confidence || 0) * 100)}% confidence
                        </div>
                    </div>
                    <button className="btn btn-secondary btn-xs" onClick={() => setActiveFire(null)} type="button">Dismiss</button>
                </div>
            )}

            {!setupProfile?.setup_completed_at && (
                <div className="alert-banner info" style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center' }}>
                    <span>Store setup is not complete. Choose a deployment template, map zones, and confirm modules before rollout.</span>
                    <button type="button" className="btn btn-primary btn-xs" onClick={() => navigate('/setup?wizard=1')}>
                        Open Setup
                    </button>
                </div>
            )}

            {loading ? (
                <SkeletonKpiGrid count={6} cols={6} />
            ) : (
                <div className="exec-kpi-grid">
                    <KPICard icon={Camera} label="Cameras online" value={activeCount} suffix={totalCameras ? ` / ${totalCameras}` : ''} accent="teal" progress={(activeCount / Math.max(1, totalCameras)) * 100} tag="Coverage" delta={8} />
                    <KPICard icon={Users} label="Shoppers on floor" value={overview?.current_occupancy ?? 0} accent="cyan" progress={Math.min(100, Number(overview?.current_occupancy ?? 0))} tag="Now" delta={5} />
                    <KPICard icon={Footprints} label="Visitor moments today" value={overview?.total_detections_today ?? 0} accent="coral" progress={Math.min(100, Number((overview?.total_detections_today ?? 0) / 20))} tag="Today" delta={12} />
                    <KPICard icon={Sparkles} label="Store atmosphere" value={Number(vibeScore).toFixed(0)} accent="emerald" progress={Number(vibeScore) || 0} tag={vibeLabel} delta={3} />
                    <KPICard icon={Flame} label="Safety incidents" value={overview?.fire_alerts_today ?? (fireAlerts?.length || 0)} accent="rose" progress={Math.min(100, Number((overview?.fire_alerts_today ?? fireAlerts?.length ?? 0) * 22))} tag="Safety" delta={-2} />
                    <KPICard icon={Clock3} label="Avg queue time" value={Number(overview?.avg_checkout_wait ?? 0).toFixed(1)} suffix=" sec" accent="sky" progress={Math.min(100, Number(overview?.avg_checkout_wait ?? 0) * 4)} tag="Checkout" delta={-4} />
                    <KPICard icon={ShoppingCart} label="Open smart carts" value={openCartCount} accent="teal" progress={Math.min(100, openCartCount * 20)} tag="Cashierless" />
                    <KPICard icon={Receipt} label="Cart value open" value={money(openCartSubtotal)} accent="emerald" progress={Math.min(100, openCartSubtotal / 5)} tag="Live basket" />
                </div>
            )}

            <ExecPanel className="dashboard-section">
                <PanelHeader
                    title="Cashierless operations"
                    subtitle="Cart tracking, counter handoff, and review alerts from the same camera pipeline"
                    badge={pipelineStatus?.processing?.adaptive_model_gating ? 'Adaptive' : 'Manual'}
                />
                <div className="ui-status-grid" style={{ marginBottom: 14 }}>
                    <StatusTile icon={ShoppingCart} label="Open carts" value={openCartCount} tone="teal" compact />
                    <StatusTile icon={ScanLine} label="At counter" value={cashierRows.length} tone="sky" compact />
                    <StatusTile icon={Receipt} label="Open subtotal" value={money(openCartSubtotal)} tone="ok" compact />
                    <StatusTile icon={AlertTriangle} label="Review alerts" value={openHumanlessAlerts} tone={openHumanlessAlerts ? 'warn' : 'neutral'} compact />
                </div>
                {cashierlessSessions.length === 0 && cashierRows.length === 0 ? (
                    <EmptyState
                        compact
                        icon={ShoppingCart}
                        title="No live smart carts yet"
                        description="Smart cart sessions appear here when Re-ID and shelf/counter zones are active."
                    />
                ) : (
                    <div className="ui-feed-list">
                        {[...cashierRows, ...cashierlessSessions].slice(0, 6).map((session) => (
                            <div key={`${session.id}-${session.global_id}`} className="ui-lane-row" style={{ gridTemplateColumns: '130px 1fr 110px 90px' }}>
                                <span className="ui-lane-row-title">{session.global_id || `Session ${session.id}`}</span>
                                <span>{session.last_zone || session.counter_id || 'shopping floor'}</span>
                                <span style={{ fontWeight: 700 }}>{money(session.cart?.subtotal)}</span>
                                <span style={{ color: 'var(--exec-muted)', fontSize: 12 }}>{dt(session.last_seen || session.counter_arrived_at)}</span>
                            </div>
                        ))}
                    </div>
                )}
            </ExecPanel>

            <div className="two-col dashboard-main-grid">
                <ExecPanel className="dashboard-panel-chart">
                    <PanelHeader
                        title="Atmosphere trend"
                        subtitle="Energy and engagement over time"
                        actions={(
                            <SegmentedControl
                                options={[
                                    { value: '24h', label: '24h' },
                                    { value: '7d', label: '7d' },
                                    { value: '30d', label: '30d' },
                                ]}
                                value={chartRange}
                                onChange={setChartRange}
                            />
                        )}
                    />
                    <div className="dashboard-chart-area">
                        {loading ? (
                            <SkeletonChart />
                        ) : trendData.length > 0 ? (
                            <Line key={`${gradientPreset}-${theme}`} data={chartData} options={chartOptions} />
                        ) : (
                            <EmptyState
                                compact
                                icon={Activity}
                                title="No trend data yet"
                                description="Insights appear once monitoring runs for a few minutes."
                            />
                        )}
                    </div>
                </ExecPanel>

                <ExecPanel className="dashboard-panel-health">
                    <PanelHeader title="Platform readiness" badge="Live" />
                    <div className="ui-status-grid">
                        <StatusTile icon={Radio} label="Store monitoring" value={displayStatus(pipelineStatus?.state, 'Live', 'Paused')} ok={pipelineStatus?.state === 'running'} />
                        <StatusTile icon={Database} label="Insights platform" value={displayStatus(health?.components?.database, 'Connected', 'Unavailable')} ok={health?.components?.database?.status === 'healthy' || health?.components?.database === 'healthy'} />
                        <StatusTile
                            icon={Zap}
                            label="Real-time engine"
                            value={displayStatus(health?.components?.redis, 'Online', 'Offline')}
                            ok={['connected', 'healthy'].includes(health?.components?.redis?.status) || health?.components?.redis === 'healthy'}
                        />
                        <StatusTile icon={LayoutDashboard} label="Leadership dashboards" value={`${liveDashboards} active`} tone="sky" />
                        <StatusTile icon={Users} label="Known shoppers" value={`${pipelineStatus?.ai_modules?.reid?.gallery_size ?? 0} profiles`} tone="teal" />
                        <StatusTile icon={Video} label="Video analyzed today" value={framesProcessed.toLocaleString()} tone="neutral" />
                    </div>
                </ExecPanel>
            </div>

            <ExecPanel className="dashboard-section">
                <PanelHeader
                    title="In-store camera wall"
                    subtitle={activeCameras.length
                        ? `${activeCameras.length} location${activeCameras.length > 1 ? 's' : ''} streaming`
                        : 'Connect cameras to begin monitoring'}
                />
                {loading ? (
                    <SkeletonCameraGrid count={2} />
                ) : activeCameras.length > 0 ? (
                    <div className="camera-grid">
                        {activeCameras.map((id) => {
                            const s = cameraStats[id] || cameraStats[String(id)] || {};
                            return (
                                <CameraStream
                                    key={id}
                                    cameraId={id}
                                    label={`Location ${id}`}
                                    zone={cameraZones[id] || cameraZones[String(id)]}
                                    fps={s.fps ?? s.fps_actual}
                                    connected={s.connected !== false}
                                />
                            );
                        })}
                    </div>
                ) : (
                    <EmptyState
                        icon={Video}
                        title="No cameras streaming"
                        description="Upload store footage from In-Store Cameras, then start monitoring."
                        action={(
                            <button type="button" className="btn btn-primary btn-xs" onClick={() => navigate('/detection')}>
                                Open In-Store Cameras
                            </button>
                        )}
                    />
                )}
            </ExecPanel>

            <ExecPanel className="dashboard-section">
                <PanelHeader title="Live store highlights" subtitle="Traffic, safety, and shopper journeys" />
                {events.length === 0 ? (
                    <EmptyState
                        compact
                        icon={Activity}
                        title="Waiting for activity"
                        description="Highlights stream here as your stores generate events."
                    />
                ) : (
                    <ActivityTimeline
                        items={events}
                        renderMessage={formatEventMessage}
                        badgeClass={eventBadgeClass}
                        badgeLabel={eventLabel}
                    />
                )}
            </ExecPanel>
        </div>
    );
}
