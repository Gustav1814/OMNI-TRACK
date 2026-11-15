import React, { useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { AlertTriangle, ArrowUpRight, Flame } from 'lucide-react';
import { Chart as ChartJS, CategoryScale, LinearScale, PointElement, LineElement, Tooltip } from 'chart.js';
import { Line } from 'react-chartjs-2';
import {
    dashboardAPI, systemAPI, pipelineAPI, detectionAPI, vibeAPI, camerasAPI,
} from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import useWebSocket from '../hooks/useWebSocket';
import CameraFrame from '../components/ops/CameraFrame';
import { useToast } from '../contexts/ToastContext';
import { useTokens } from '../lib/tokens';
import { buildLineChartOptions } from '../lib/chartTheme';
import { Metric } from '../components/ui';
import { kindLabel } from '../components/alerts/alertFormat';

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip);

const fmtSecs = (v) => {
    if (v == null) return '';
    const s = Math.round(Number(v));
    return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${s % 60} s`;
};

function formatEventMessage(type, data = {}) {
    const where = data.zone_name || (data.camera_id != null ? `camera ${data.camera_id}` : 'the store');
    switch (type) {
        case 'zone_visit':
            return `Shopper spent ${fmtSecs(data.duration_s)} at ${where}${data.payload?.engaged ? ' (engaged)' : ''}.`;
        case 'queue_wait':
            return `Customer waited ${fmtSecs(data.duration_s)} in ${where} before being served.`;
        case 'service':
            return `Served at ${where} in ${fmtSecs(data.duration_s)}.`;
        case 'queue_abandon':
            return `Customer left ${where} after ${fmtSecs(data.duration_s)} without being served.`;
        case 'line_cross':
            return `${Number(data.value) > 0 ? 'Entry' : 'Exit'} at ${where}.`;
        case 'crowd_level':
            return `${where} is now ${data.payload?.level || 'changed'} (${data.payload?.count ?? '?'} people).`;
        case 'alert':
            return `${(data.payload?.alert_type || 'Alert').replace(/_/g, ' ')} ${data.payload?.action === 'close' ? 'cleared' : 'raised'} at ${where}.`;
        case 'fire_alert':
            return `Safety alert in ${data.zone || 'store floor'}. Review the camera now.`;
        case 'crowd_alert':
            return `Elevated footfall in ${data.zone || 'a busy zone'}. Consider opening another checkout lane.`;
        case 'reid_match':
            return `Returning shopper recognised on ${data.camera_id ? `camera ${data.camera_id}` : 'another camera'}.`;
        case 'vibe_update':
            return `Store atmosphere shifted to ${data.label || 'a new mood'} (${Math.round(data.overall_score || 0)}/100).`;
        case 'detection_update':
            return `${data.count ?? data.person_count ?? 'Several'} shoppers visible in ${data.zone || 'the sales floor'}.`;
        default:
            return 'New activity recorded.';
    }
}

function eventLabel(type) {
    switch (type) {
        case 'fire_alert': return 'Safety';
        case 'alert': return 'Alert';
        case 'crowd_alert': return 'Traffic';
        case 'crowd_level': return 'Crowd';
        case 'line_cross': return 'Footfall';
        case 'zone_visit': return 'Shelf';
        case 'queue_wait': return 'Queue';
        case 'service': return 'Checkout';
        case 'queue_abandon': return 'Queue';
        case 'reid_match': return 'Journey';
        case 'vibe_update': return 'Atmosphere';
        case 'detection_update': return 'Visits';
        default: return 'Event';
    }
}

const isAlertType = (type) => type === 'fire_alert' || type === 'alert' || type === 'queue_abandon';

function displayStatus(raw, runningLabel = 'Active', idleLabel = 'Standby') {
    if (raw === 'running' || raw === 'healthy' || raw === 'connected') return runningLabel;
    if (raw === 'idle' || raw === 'degraded') return idleLabel;
    if (typeof raw === 'object' && raw?.status) return displayStatus(raw.status, runningLabel, idleLabel);
    return raw ?? '—';
}

const dt = (value) => (value ? new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '—');
const clock = (value) => (value ? new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '—');
const hh = (h) => `${String(h).padStart(2, '0')}:00`;
const numOrNull = (v) => (v == null || Number.isNaN(Number(v)) ? null : Number(v));

const RANGES = ['24h', '7d', '30d'];
// Signals behind the atmosphere score; slot = categorical series colour, same as the Store pulse meters.
const SIGNALS = [
    { key: 'occupancy', label: 'Occupancy', slot: 1 },
    { key: 'flow', label: 'Flow', slot: 2 },
    { key: 'engagement', label: 'Shelf engagement', slot: 3 },
    { key: 'sentiment', label: 'Sentiment', slot: 4 },
];
const numOrNullSignal = (v) => (v == null || Number.isNaN(Number(v)) ? null : Number(v));

function CornerLink({ to, label }) {
    return (
        <Link to={to} className="corner-link" aria-label={label} title={label}>
            <ArrowUpRight size={16} strokeWidth={1.75} aria-hidden />
        </Link>
    );
}

function Empty({ title, children, action }) {
    return (
        <div className="empty-state ops-fill">
            <span className="empty-state-title">{title}</span>
            <span>{children}</span>
            {action}
        </div>
    );
}

/** Hourly bars with value labels; the busiest hour is highlighted, unmeasured hours are stubs. */
function HourBars({ hours, valueKey, nowHour, label }) {
    const values = hours.map((h) => (h.observed ? Number(h[valueKey] || 0) : null));
    const max = Math.max(0, ...values.filter((v) => v != null));
    const maxIdx = max > 0 ? values.indexOf(max) : -1;
    return (
        <div className="ops-bars" role="img" aria-label={`${label}: ${hours.filter((h) => h.observed).map((h) => `${hh(h.hour)} ${h[valueKey] ?? 0}`).join(', ') || 'no measured hours'}`}>
            <div className="ops-bars-plot">
                {hours.map((h, i) => {
                    const v = values[i];
                    return (
                        <div
                            key={h.hour}
                            className={`ops-bar${v == null ? ' is-off' : ''}${i === maxIdx ? ' is-max' : ''}${h.hour === nowHour ? ' is-now' : ''}`}
                            style={{ '--h': v != null && max > 0 ? v / max : 0 }}
                            title={`${hh(h.hour)} · ${v == null ? 'not measured' : v.toLocaleString()}`}
                        >
                            {v != null ? <span className="ops-bar-val">{v.toLocaleString()}</span> : null}
                            <span className="ops-bar-fill" />
                        </div>
                    );
                })}
            </div>
            <div className="ops-bars-axis" aria-hidden>
                {hours.map((h) => (
                    <span key={h.hour} className={h.hour === nowHour ? 'is-now' : ''}>{h.hour % 3 === 0 ? String(h.hour).padStart(2, '0') : ''}</span>
                ))}
            </div>
        </div>
    );
}

export default function DashboardPage() {
    const t = useTokens();
    const { toast } = useToast();
    const navigate = useNavigate();
    const [chartRange, setChartRange] = useState('24h');

    const { data: overview, loading: overviewLoading } = useLivePoll(() => dashboardAPI.overview(), { intervalMs: 5000 });
    const { data: health } = useLivePoll(() => systemAPI.health(), { intervalMs: 10000 });
    const { data: pipelineStatus } = useLivePoll(() => pipelineAPI.status(), { intervalMs: 4000 });
    const { data: detStatus } = useLivePoll(() => detectionAPI.status(), { intervalMs: 3000 });
    const { data: recent } = useLivePoll(() => dashboardAPI.recentEvents(20), { intervalMs: 5000 });
    const hours = chartRange === '7d' ? 168 : chartRange === '30d' ? 720 : 24;
    const { data: vibeTrend } = useLivePoll(() => vibeAPI.trend(hours), { intervalMs: 60000 });
    const { data: cameraList } = useLivePoll(() => camerasAPI.list(), { intervalMs: 30000 });
    const [heroId, setHeroId] = useState(null);

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
    const rawVibeScore = liveVibe?.overall_score ?? vibe.score;
    const hasVibe = rawVibeScore != null && !Number.isNaN(Number(rawVibeScore));
    const vibeScore = hasVibe ? Number(rawVibeScore) : null;
    const vibeLabel = hasVibe ? (liveVibe?.label ?? vibe.label ?? '') : '';
    const avgWait = numOrNull(overview?.median_wait_s);
    const footfallIsEntries = overview?.footfall_metric === 'entries';
    const footfall = numOrNull(footfallIsEntries ? overview?.entries_today : overview?.visitors_today);
    const footfallDelta = footfallIsEntries ? overview?.vs_yesterday?.entries_today : overview?.vs_yesterday?.visitors_today;
    const occupancy = numOrNull(overview?.current_occupancy);
    const medianVisit = numOrNull(overview?.median_visit_s);
    const openZoneAlerts = (overview?.open_alerts || []).filter((a) => a.alert_type !== 'fire');
    const fireFromOverview = overview?.active_fire
        ? (overview.open_alerts || []).find((a) => a.alert_type === 'fire') || { camera_id: null }
        : null;
    const shownFire = activeFire || fireFromOverview;
    const zoneEvents = (recent?.events || []).map((e) => ({ id: `z${e.id}`, type: e.type, data: e, timestamp: e.ts }));
    const highlights = [...events, ...zoneEvents].slice(0, 30);
    // Collapse back-to-back repeats of the same message into one row with a count.
    const feedRows = highlights.reduce((rows, item) => {
        const msg = formatEventMessage(item.type, item.data || {});
        const last = rows[rows.length - 1];
        if (last && last.item.type === item.type && last.msg === msg) last.count += 1;
        else rows.push({ item, msg, count: 1 });
        return rows;
    }, []);
    const loading = overviewLoading && !overview;

    const activeCameras = useMemo(() => {
        const ids = detStatus?.active_cameras;
        if (Array.isArray(ids)) return ids;
        if (pipelineStatus?.cameras) {
            return Object.keys(pipelineStatus.cameras?.zones || {}).map((n) => Number(n));
        }
        return [];
    }, [detStatus, pipelineStatus]);

    const totalCameras = overview?.cameras?.total ?? pipelineStatus?.cameras?.total ?? 0;
    const activeCount = overview?.cameras?.streaming ?? overview?.cameras?.analysed_live ?? activeCameras.length ?? 0;
    const running = pipelineStatus?.state === 'running';

    // Footfall by hour (today), measured hours only.
    const hourly = Array.isArray(overview?.hourly) ? overview.hourly : [];
    const footKey = footfallIsEntries ? 'entries' : 'visitors';
    const hasHourly = hourly.some((h) => h.observed);
    const nowHour = overview?.as_of ? new Date(overview.as_of).getHours() : null;

    const trendSeries = Array.isArray(vibeTrend?.series) ? vibeTrend.series : [];
    // Down-sample to at most ~60 points (rows are stored once a minute). Where monitoring
    // stopped (a gap well beyond the sampling interval) the line breaks instead of bridging it.
    const stride = Math.max(1, Math.ceil(trendSeries.length / 60));
    const sampled = trendSeries.filter((_, i) => i % stride === 0 || i === trendSeries.length - 1);
    const gapMs = Math.max(5 * 60 * 1000, stride * 3 * 60 * 1000);
    const trendData = [];
    sampled.forEach((v, i) => {
        const d = new Date(v.ts);
        if (i > 0 && d - new Date(sampled[i - 1].ts) > gapMs) trendData.push({ score: null, t: '' });
        trendData.push({
            score: Number(v.score),
            ...Object.fromEntries(SIGNALS.map((sg) => [sg.key, numOrNullSignal(v[sg.key])])),
            t: chartRange === '24h'
                ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
                : d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit' }),
        });
    });
    const sparse = trendData.length <= 40;

    const shownSignals = SIGNALS.filter((sg) => trendData.some((d) => d[sg.key] != null));
    const line = (label, key, color) => ({
        label,
        data: trendData.map((d) => d[key] ?? null),
        borderColor: color,
        backgroundColor: color,
        borderWidth: 2,
        fill: false,
        tension: 0,
        spanGaps: false,
        pointRadius: sparse ? 3 : 0,
        pointHoverRadius: 4,
        pointBackgroundColor: color,
        pointBorderWidth: 0,
    });
    const chartData = {
        labels: trendData.map((d) => d.t),
        datasets: [
            line('Atmosphere', 'score', t.text),
            ...shownSignals.map((sg) => line(sg.label, sg.key, t.series[sg.slot - 1])),
        ],
    };
    const baseOptions = buildLineChartOptions(t);
    const chartOptions = buildLineChartOptions(t, {
        scales: {
            ...baseOptions.scales,
            y: { ...baseOptions.scales.y, max: 100, ticks: { ...baseOptions.scales.y.ticks, stepSize: 25 } },
        },
    });


    const cams = Array.isArray(cameraList) ? cameraList : [];
    const activeSet = new Set(activeCameras.map(String));
    const heroCam = cams.find((c) => String(c.id) === String(heroId))
        || cams.find((c) => activeSet.has(String(c.id)))
        || cams[0]
        || (activeCameras.length ? { id: activeCameras[0], name: `Camera ${activeCameras[0]}` } : null);

    // The amber feature card always carries a real, measured number: today's footfall first,
    // then people in store now, then detections today. If none is measured it renders as a plain panel.
    const featureMetric = footfall != null
        ? { label: footfallIsEntries ? 'Entries today' : 'Visitors today', value: footfall, delta: footfallDelta, to: '/floor/rush-hours', link: 'Open rush hours' }
        : occupancy != null
            ? { label: 'People in store now', value: occupancy, to: '/floor/footfall', link: 'Open footfall' }
            : numOrNull(overview?.detections_today) != null
                ? { label: 'Detections today', value: numOrNull(overview.detections_today), to: '/detection', link: 'Open video feeds' }
                : null;

    const dbOk = health?.components?.database?.status === 'healthy' || health?.components?.database === 'healthy';
    const redisOk = ['connected', 'healthy'].includes(health?.components?.redis?.status) || health?.components?.redis === 'healthy';
    const servicesDown = health && (!dbOk || !redisOk);

    return (
        <div className="page-scroll ops-dash">
            <header className="page-header">
                <div>
                    <h1 className="page-title">Store overview</h1>
                    <div className="osd-strip ops-head-osd" aria-label="Session status">
                        <span>
                            <span className={`live-dot ${running ? 'is-live' : pipelineStatus ? 'is-warn' : ''}`} aria-hidden />
                            <strong>{pipelineStatus ? (running ? 'Live' : displayStatus(pipelineStatus.state, 'Live', 'Paused')) : 'Connecting'}</strong>
                        </span>
                        <span>Cameras <strong>{loading ? '—' : `${activeCount}/${totalCameras}`}</strong></span>
                        <span>Updated <strong>{clock(overview?.as_of)}</strong></span>
                        {overview?.timezone ? <span>TZ <strong>{overview.timezone}</strong></span> : null}
                    </div>
                </div>
            </header>

            {shownFire && (
                <div className="alert-banner danger" role="alert">
                    <Flame size={16} strokeWidth={1.75} aria-hidden />
                    <span className="ops-banner-text">
                        <strong>Fire or smoke detected</strong>
                        {' · '}
                        {shownFire.zone || 'Store floor'}
                        {shownFire.camera_id != null ? ` · camera ${shownFire.camera_id}` : ''}
                        {shownFire.confidence != null ? ` · ${Math.round(shownFire.confidence * 100)}% confidence` : ''}
                    </span>
                    <span className="ops-banner-actions">
                        <button className="btn btn-secondary btn-xs" onClick={() => navigate('/incidents/fire')} type="button">Open fire and smoke</button>
                        {activeFire && <button className="btn btn-ghost btn-xs" onClick={() => setActiveFire(null)} type="button">Dismiss</button>}
                    </span>
                </div>
            )}

            {openZoneAlerts.map((a) => (
                <div key={`${a.camera_id}-${a.zone_id}-${a.alert_type}`} className={`alert-banner ${a.alert_type === 'intrusion' ? 'danger' : 'warning'}`} role={a.alert_type === 'intrusion' ? 'alert' : undefined}>
                    <AlertTriangle size={16} strokeWidth={1.75} aria-hidden />
                    <span className="ops-banner-text">
                        {kindLabel(a.alert_type)} at <strong>{a.zone_name}</strong> (camera {a.camera_id}){a.alert_type === 'intrusion' && a.people > 1 ? `, ${a.people} people` : ''}.
                    </span>
                </div>
            ))}

            {servicesDown && (
                <div className="alert-banner danger" role="alert">
                    <AlertTriangle size={16} strokeWidth={1.75} aria-hidden />
                    <span className="ops-banner-text">Some services are offline, so the numbers below may be out of date.</span>
                </div>
            )}

            {Array.isArray(cameraList) && cameraList.length === 0 && (
                <div className="alert-banner info">
                    <span className="ops-banner-text">No cameras yet. Add a camera and draw its zones; every number here comes from what it sees.</span>
                    <span className="ops-banner-actions">
                        <button type="button" className="btn btn-primary btn-xs" onClick={() => navigate('/setup')}>
                            Add a camera
                        </button>
                    </span>
                </div>
            )}

            {/* Hero: a real camera frame + the one feature number */}
            <section className="ops-dash-top" aria-label="Live view and headline">
                <div className="ops-hero">
                    <CameraFrame
                        camera={heroCam}
                        live={heroCam != null && activeSet.has(String(heroCam.id))}
                        tz={overview?.timezone}
                        hero
                    />
                    {cams.length > 1 ? (
                        <div className="seg ops-hero-switch" role="group" aria-label="Camera in view">
                            {cams.map((c) => (
                                <button key={c.id} type="button" aria-pressed={heroCam?.id === c.id} onClick={() => setHeroId(c.id)}>
                                    {c.name || `Camera ${c.id}`}
                                </button>
                            ))}
                        </div>
                    ) : null}
                </div>

                {featureMetric ? (
                    <div className="panel panel--feature ops-feature">
                        <div className="panel-body ops-feature-body">
                            <div className="ops-feature-head">
                                <span className="osd">{featureMetric.label}</span>
                                <CornerLink to={featureMetric.to} label={featureMetric.link} />
                            </div>
                            <Metric size="hero" value={featureMetric.value} delta={featureMetric.delta} />
                            <div className="osd-strip ops-feature-osd">
                                <span>Peak hour <strong>{overview?.peak_hour_today != null ? hh(overview.peak_hour_today) : 'not yet'}</strong></span>
                                {medianVisit != null ? <span>Median visit <strong>{fmtSecs(medianVisit)}</strong></span> : null}
                            </div>
                        </div>
                    </div>
                ) : (
                    <div className="panel ops-feature">
                        <div className="panel-body ops-feature-body">
                            <Empty title={loading ? 'Reading today’s numbers' : 'Nothing counted today'}>
                                {loading ? 'Loading the overview.' : 'Counts start once a camera is analysed with a zone or line drawn.'}
                            </Empty>
                        </div>
                    </div>
                )}
            </section>

            {/* Key figures: flush, split by hairlines */}
            <section className="panel panel--flush ops-flush-kpis" aria-label="Key figures">
                <div className="kpi-strip">
                    <Metric label="In store now" value={loading ? null : occupancy} sub={occupancy == null ? 'Needs an occupancy zone' : 'Live'} />
                    <Metric label="Median queue wait" value={loading ? null : avgWait} unit="s" sub={avgWait == null ? 'No checkout zone data' : 'Checkout, today'} />
                    <Metric label="Atmosphere" value={loading ? null : vibeScore} unit="/100" sub={vibeLabel || 'No reading yet'} />
                    <Metric label="Detections today" value={loading ? null : numOrNull(overview?.detections_today)} sub="People, all cameras" />
                    <Metric
                        label="Fire incidents"
                        value={loading ? null : numOrNull(overview?.fire_incidents_today)}
                        sub={overview?.active_fire ? 'Active now' : 'Today'}
                    />
                </div>
            </section>

            {/* Activity: hourly bars (wide) + live highlights (narrow) */}
            <section className="ops-split" aria-label="Activity">
                <div className="panel">
                    <div className="panel-body ops-col">
                        <div className="panel-head">
                            <div>
                                <h2 className="panel-title">{footfallIsEntries ? 'Entries' : 'Visitors'} by hour</h2>
                                <div className="panel-sub">Today, store time. Busiest hour highlighted.</div>
                            </div>
                            <CornerLink to="/floor/rush-hours" label="Open rush hours" />
                        </div>
                        <div className="ops-tape-area">
                            {hasHourly ? (
                                <HourBars
                                    hours={hourly}
                                    valueKey={footKey}
                                    nowHour={nowHour}
                                    label={`${footfallIsEntries ? 'Entries' : 'Visitors'} by hour today`}
                                />
                            ) : (
                                <Empty title={loading ? 'Loading today' : 'No footfall measured today'}>
                                    {loading ? 'Reading the overview.' : 'Hours fill in once a camera has been analysed today.'}
                                </Empty>
                            )}
                        </div>
                    </div>
                </div>

                <div className="panel">
                    <div className="panel-body ops-col">
                        <div className="panel-head">
                            <div>
                                <h2 className="panel-title">Live highlights</h2>
                                <div className="panel-sub">{highlights.length ? `${highlights.length} recent` : 'Visits, queues, footfall and alerts'}</div>
                            </div>
                            <CornerLink to="/incidents/alerts" label="Open alerts" />
                        </div>
                        {highlights.length === 0 ? (
                            <Empty title="Waiting for activity">Events appear once cameras run and zones are drawn in setup.</Empty>
                        ) : (
                            <ol className="ops-feed">
                                {feedRows.slice(0, 6).map(({ item, msg, count }, i) => (
                                    <li key={item.id ?? `${item.type}-${i}`} className={`ops-feed-row${isAlertType(item.type) ? ' is-alert' : ''}`}>
                                        <span className="ops-feed-time mono">{dt(item.timestamp || item.data?.ts)}</span>
                                        <span className="ops-feed-body">
                                            <span className="ops-feed-kind">{eventLabel(item.type)}{count > 1 ? <span className="ops-feed-count"> ×{count}</span> : null}</span>
                                            <span className="ops-feed-msg">{msg}</span>
                                        </span>
                                    </li>
                                ))}
                            </ol>
                        )}
                    </div>
                </div>
            </section>

            <section aria-label="Atmosphere">
                <div className="panel">
                    <div className="panel-body ops-col">
                        <div className="panel-head">
                            <div>
                                <h2 className="panel-title">Atmosphere</h2>
                                <div className="panel-sub">Score and the signals behind it, 0 to 100</div>
                            </div>
                            <div className="seg" role="group" aria-label="Range">
                                {RANGES.map((r) => (
                                    <button key={r} type="button" aria-pressed={chartRange === r} onClick={() => setChartRange(r)}>{r}</button>
                                ))}
                            </div>
                        </div>
                        {trendData.length > 0 && shownSignals.length ? (
                            <div className="legend an-legend">
                                <span className="legend-item"><span className="an-legend-line swatch-ink" />Atmosphere</span>
                                {shownSignals.map((sg) => (
                                    <span key={sg.key} className="legend-item"><span className={`an-legend-line swatch-${sg.slot}`} />{sg.label}</span>
                                ))}
                            </div>
                        ) : null}
                        <div className="ops-chart-area">
                            {trendData.length > 0 ? (
                                <Line data={chartData} options={chartOptions} aria-label="Atmosphere score and its signals over time" role="img" />
                            ) : (
                                <Empty title="No atmosphere readings">The score is stored once a minute while monitoring runs.</Empty>
                            )}
                        </div>
                    </div>
                </div>
            </section>
        </div>
    );
}
