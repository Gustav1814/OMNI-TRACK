/**
 * OmniTrack AI — Footfall (crowd density, live)
 * Live smoothed occupancy per crowd zone (/api/crowd/status), the measured
 * history for the store or one zone (/api/crowd/history) and today's footfall
 * total (/api/peak-hours/today). The only place footfall totals are shown.
 */

import React, { useEffect, useMemo, useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import { camerasAPI, crowdAPI, peakHoursAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import Metric from '../components/ui/Metric';
import Tape from '../components/ui/Tape';
import {
    AnalyticsPage, CameraFrame, DASH, Definitions, FeatureCard, HeroRow, NoData, Panel, Seg, fmtNum, fmtPct, fmtTime, pctMetric,
} from '../components/zones/analyticsUi';

const LEVEL_PILL = { empty: 'neutral', low: 'success', moderate: 'success', high: 'warn', critical: 'danger' };
const LEVEL_METER = { high: 'meter--warn', critical: 'meter--danger' };
const HOURS = [
    { value: 2, label: '2 h' },
    { value: 6, label: '6 h' },
    { value: 24, label: '24 h' },
    { value: 168, label: '7 days' },
];
const CRITICAL_AT = 90; // % of capacity, from the API's level definitions

export default function CrowdPage({ embedded = false }) {
    const { data, error } = useLivePoll(() => crowdAPI.status(), { intervalMs: 3000 });
    const { data: footfall } = useLivePoll(() => peakHoursAPI.today(), { intervalMs: 60000 });
    const { data: cameraList } = useLivePoll(() => camerasAPI.list(), { intervalMs: 60000 });
    const [zoneId, setZoneId] = useState('');
    const [hours, setHours] = useState(6);
    const { data: history, refresh: refreshHistory } = useLivePoll(
        () => crowdAPI.history(zoneId === '' ? null : Number(zoneId), hours),
        { intervalMs: 30000 },
    );
    useEffect(() => { refreshHistory(); }, [zoneId, hours, refreshHistory]);

    const zones = data?.zones || [];
    const live = zones.filter((z) => z.live);
    const alerts = live.filter((z) => z.alert_open);
    const series = (history?.series || []).map((p) => ({ ...p, label: fmtTime(p.ts, hours > 24) }));
    const fullest = live.filter((z) => z.occupancy_pct != null).sort((a, b) => b.occupancy_pct - a.occupancy_pct)[0];
    const fullestPct = pctMetric(fullest?.occupancy_pct);
    const tape = series.map((p, i) => ({ t: i, label: `${p.label} · avg ${fmtNum(p.avg, 1)}, peak ${fmtNum(p.max)}`, value: p.avg ?? 0 }));
    const peakPoint = series.reduce((best, p) => (p.max != null && (!best || p.max > best.max) ? p : best), null);
    const axis = series.length > 2
        ? [series[0].label, series[Math.floor(series.length / 2)].label, series[series.length - 1].label]
        : series.map((p) => p.label);

    const cameras = useMemo(() => {
        const m = {};
        for (const c of Array.isArray(cameraList) ? cameraList : []) m[c.id] = c;
        return m;
    }, [cameraList]);
    const camName = (id) => cameras[id]?.name || `Camera ${id}`;
    const selectedZone = zones.find((z) => String(z.zone_id) === String(zoneId));
    const frameZone = selectedZone || live[0] || zones[0];
    const frameCamera = frameZone ? (cameras[frameZone.camera_id] || { id: frameZone.camera_id }) : null;

    // Hero: live occupancy; when nothing is live, the last stored reading, labelled as such.
    const occupancyLive = data?.store_occupancy != null;
    const lastPoint = [...series].reverse().find((p) => p.avg != null);
    const heroValue = occupancyLive ? data.store_occupancy : lastPoint?.avg ?? null;
    const heroLabel = occupancyLive || !lastPoint ? 'People in store now' : `People in ${history?.zone_name ? history.zone_name.toLowerCase() : 'store'}, last recorded`;
    const heroFoot = occupancyLive
        ? `${data.cameras_live} camera(s) analysed live`
        : lastPoint
            ? `Average at ${lastPoint.label}. No camera is being analysed right now.`
            : 'No camera is being analysed right now';

    const footfallIsEntries = footfall?.metric === 'entries';
    const footfallTotal = footfall?.status === 'ok' ? (footfallIsEntries ? footfall.total_entries : footfall.total_visitors) ?? null : null;

    const historyControls = (
        <>
            <select className="form-select an-select" aria-label="Zone" value={zoneId} onChange={(e) => setZoneId(e.target.value)}>
                <option value="">Whole store</option>
                {zones.map((z) => <option key={z.zone_id} value={z.zone_id}>{z.zone_name} · {camName(z.camera_id)}</option>)}
            </select>
            <Seg value={hours} onChange={setHours} options={HOURS} label="History window" />
        </>
    );

    return (
        <AnalyticsPage
            embedded={embedded}
            title="Footfall"
            subtitle="People in the store and in each crowd zone, smoothed, against the capacity you set."
        >
            {error && <div className="alert-banner danger">Unable to reach crowd analytics.</div>}
            {alerts.map((z) => (
                <div key={z.zone_id} className="alert-banner danger" role="alert">
                    <AlertTriangle size={16} strokeWidth={1.75} />
                    <span><strong>{z.zone_name}</strong> is overcrowded: {z.count} people ({fmtPct(z.occupancy_pct)} of {z.capacity}).</span>
                </div>
            ))}

            <HeroRow
                hero={{ label: heroLabel, value: heroValue, decimals: occupancyLive ? 0 : 1, foot: heroFoot }}
                aside={frameCamera ? <CameraFrame camera={frameCamera} live={!!frameZone?.live} /> : null}
            >
                <Metric label="Zones live" value={data ? live.length : null} unit={data ? `of ${data.configured_zones ?? 0}` : undefined} />
                <Metric label="Fullest zone" value={fullestPct.value} unit={fullestPct.unit} sub={fullest ? `${fullest.zone_name} · ${camName(fullest.camera_id)}` : 'No zone live'} />
                <Metric
                    label={`Peak, last ${HOURS.find((h) => h.value === hours)?.label || `${hours} h`}`}
                    value={peakPoint ? peakPoint.max : null}
                    unit={peakPoint ? 'people' : undefined}
                    sub={peakPoint ? `at ${peakPoint.label}` : undefined}
                />
                <Metric label="Overcrowding alerts" value={data ? alerts.length : null} />
            </HeroRow>

            <div className="an-grid an-grid--feature">
                <FeatureCard
                    label={`${footfallIsEntries ? 'Entries' : 'Visitors'} today`}
                    value={footfallTotal}
                    sub={footfallTotal != null
                        ? (footfallIsEntries ? 'Inward crossings of the entry line' : 'Distinct people seen across cameras')
                        : 'No footfall measured today yet'}
                />
                <Panel title="Crowd zones" sub={zones.length ? `Marker at ${CRITICAL_AT} % = critical` : undefined}>
                    {zones.length === 0 ? (
                        <NoData title="No crowd zones configured" setupLink>
                            Draw a crowd zone on a camera and give it a capacity. Occupancy is only measured inside zones.
                        </NoData>
                    ) : (
                        <div className="an-rows">
                            {zones.map((z) => {
                                const pct = z.live && z.occupancy_pct != null ? Math.min(100, z.occupancy_pct * 100) : null;
                                return (
                                    <div key={z.zone_id} className="an-row">
                                        <div>
                                            <div className="an-row-name">{z.zone_name}</div>
                                            <div className="an-row-sub">{camName(z.camera_id)} · cap {z.capacity}</div>
                                        </div>
                                        <div className={`meter ${LEVEL_METER[z.level] || ''}`.trim()}>
                                            <div className="meter-track" role="img" aria-label={pct != null ? `${z.zone_name}: ${fmtPct(z.occupancy_pct)} of capacity` : `${z.zone_name}: not live`}>
                                                {pct != null ? <div className="meter-fill" style={{ '--value': `${pct}%` }} /> : null}
                                                <span className="meter-marker" style={{ '--marker': `${CRITICAL_AT}%` }} />
                                            </div>
                                        </div>
                                        <div className="an-actions">
                                            <span className={`an-row-val${z.live ? '' : ' an-row-val--muted'}`}>{z.live ? fmtNum(z.count) : DASH}</span>
                                            <span className={`pill pill-${z.live ? (LEVEL_PILL[z.level] || 'neutral') : 'neutral'}`}>
                                                {z.live ? `${z.level || DASH} · ${fmtPct(z.occupancy_pct)}` : 'offline'}
                                            </span>
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    )}
                    <Definitions items={data?.definitions} />
                </Panel>
            </div>

            <Panel
                title="Occupancy history"
                sub={history?.zone_name ? `${history.zone_name} · average per ${Math.round((history.bucket_s || 60) / 60)} min` : undefined}
                actions={historyControls}
            >
                {series.length === 0 ? (
                    <NoData title="No occupancy recorded in this window">
                        Samples are stored every few seconds while a camera is analysed. Start a session on a feed with a crowd zone.
                    </NoData>
                ) : (
                    <>
                        <Tape
                            data={tape}
                            height={160}
                            playhead={tape.length - 1}
                            axisLabels={axis}
                            ariaLabel={`Average people per bucket, ${history?.zone_name || 'whole store'}`}
                        />
                        <div className="legend an-legend an-tape-legend">
                            <span className="legend-item"><span className="swatch swatch-neutral" />Below median</span>
                            <span className="legend-item"><span className="swatch swatch-mid" />Above median</span>
                            <span className="legend-item"><span className="swatch swatch-highlight" />Busiest 10 %</span>
                        </div>
                    </>
                )}
            </Panel>
        </AnalyticsPage>
    );
}
