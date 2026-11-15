/**
 * OmniTrack AI — Fire & smoke
 * Live per-camera state (/api/fire/status) and confirmed incidents
 * (/api/fire/alerts). An incident needs k-of-n confirming frames, so single
 * false positives never raise an alarm.
 */

import React, { useEffect, useMemo, useState } from 'react';
import { Check, Flame, Minus, TriangleAlert } from 'lucide-react';
import { Link } from 'react-router-dom';
import { camerasAPI, fireAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import Metric from '../components/ui/Metric';
import {
    AnalyticsHeader, CameraFrame, DASH, Definitions, FeatureCard, HeroRow, NoData, Panel, Seg, fmtDuration, fmtPct, fmtTime,
} from '../components/zones/analyticsUi';

const STATUS_PILL = { active: 'danger', cleared: 'success' };
const HOURS = [
    { value: 24, label: '24 h' },
    { value: 168, label: '7 days' },
    { value: 720, label: '30 days' },
];

/** One safety state, always with a text label next to the status colour. */
function safetyState(status, error) {
    if (error && !status) {
        return { key: 'unknown', label: 'State unknown', detail: 'Fire detection could not be reached. Check that the backend is running.', Icon: Minus };
    }
    if (!status) return { key: 'unknown', label: 'Checking', detail: 'Reading the live fire state.', Icon: Minus };
    if (status.active) {
        const cams = (status.active_cameras || []).join(', ');
        return { key: 'alarm', label: 'Fire or smoke detected', detail: `Camera ${cams}. Check the feed now.`, Icon: Flame, role: 'alert' };
    }
    if (status.model_present === false) {
        return {
            key: 'disabled',
            label: 'Detection off',
            detail: 'The fire and smoke model is not installed. Add the weights and restart the backend to turn detection on.',
            Icon: TriangleAlert,
        };
    }
    const cams = status.cameras || [];
    if (cams.length === 0) {
        return { key: 'idle', label: 'Not monitoring', detail: 'No camera is being analysed, so nothing is being watched for fire or smoke.', Icon: Minus };
    }
    return { key: 'clear', label: 'All clear', detail: `${cams.length} camera(s) analysed live. No confirmed fire or smoke.`, Icon: Check };
}

function EmbedWrap({ children }) {
    return <div className="an-embed">{children}</div>;
}

function PageWrap({ children }) {
    return <div className="an-page">{children}</div>;
}

export default function FirePage({ embedded = false }) {
    const [hours, setHours] = useState(24);
    const { data: status, error } = useLivePoll(() => fireAPI.status(), { intervalMs: 3000 });
    const { data: alerts, refresh } = useLivePoll(() => fireAPI.alerts(hours), { intervalMs: 10000 });
    const { data: cameraList } = useLivePoll(() => camerasAPI.list(), { intervalMs: 60000 });
    useEffect(() => { refresh(); }, [hours, refresh]);

    const incidents = alerts?.incidents || [];
    const cams = status?.cameras || [];
    const active = status?.active;
    const state = safetyState(status, error);
    const { Icon } = state;
    const windowLabel = HOURS.find((h) => h.value === hours)?.label || `${hours} h`;

    const cameras = useMemo(() => (Array.isArray(cameraList) ? cameraList : []), [cameraList]);
    const camById = (id) => cameras.find((c) => String(c.id) === String(id));
    const camName = (id) => camById(id)?.name || `Camera ${id}`;
    const liveIds = cams.map((c) => c.camera_id);
    const frameId = (active ? status?.active_cameras?.[0] : null) ?? liveIds[0] ?? cameras[0]?.id;
    const frameCamera = frameId != null ? (camById(frameId) || { id: frameId }) : null;

    const windowSeg = <Seg value={hours} onChange={setHours} options={HOURS} label="Incident window" />;
    const Wrap = embedded ? EmbedWrap : PageWrap;

    return (
        <Wrap>
            {embedded ? <div className="an-embed-bar">{windowSeg}</div> : (
                <AnalyticsHeader title="Fire and smoke" subtitle="Confirmed fire and smoke incidents from the fire detection model.">
                    {windowSeg}
                </AnalyticsHeader>
            )}

            <HeroRow
                lead={(
                    <div className={`an-safety-lead an-safety--${state.key}`} role={state.role || 'status'} aria-live="polite">
                        <div className="an-safety-state">
                            <span className="an-safety-mark" aria-hidden><Icon size={20} strokeWidth={1.75} /></span>
                            <span className="an-safety-label">{state.label}</span>
                        </div>
                        <p className="an-safety-detail">{state.detail}</p>
                        {active ? <Link to="/detection" className="btn btn-secondary btn-sm">Open live view</Link> : null}
                    </div>
                )}
                hero={{
                    label: 'Incidents today',
                    value: status?.incidents_today ?? null,
                    foot: 'Confirmed incidents since midnight, store time',
                }}
                aside={frameCamera ? <CameraFrame camera={frameCamera} live={liveIds.some((id) => String(id) === String(frameCamera.id))} /> : null}
            >
                <Metric label="Active now" value={status ? (active ? (status.active_cameras || []).length : 0) : null} />
                <Metric label="Cameras watched" value={status ? cams.length : null} unit={cameras.length ? `of ${cameras.length}` : undefined} />
                <Metric label="Model" value={status ? (status.model_present ? 'Installed' : 'Missing') : null} size="sm" />
            </HeroRow>

            <div className="an-grid an-grid--feature">
                <FeatureCard
                    label={`Incidents, last ${windowLabel}`}
                    value={alerts ? incidents.length : null}
                    sub={alerts ? (incidents.length ? `Latest ${fmtTime(incidents[0].started_at, true)}` : 'Nothing confirmed in this window') : undefined}
                />
                <Panel title="Cameras" sub="Live fire state per analysed camera">
                    {cams.length === 0 ? (
                        <NoData title="No camera is being analysed">
                            Start a video feed to monitor it for fire and smoke.
                        </NoData>
                    ) : (
                        <div className="an-rows">
                            {cams.map((c) => (
                                <div key={c.camera_id} className="an-row">
                                    <div>
                                        <div className="an-row-name">{camName(c.camera_id)}</div>
                                        <div className="an-row-sub">
                                            {c.recent_hits ?? 0} of last {c.window ?? DASH} frames
                                            {c.active ? ` · ${c.class_name || 'fire'} · peak ${fmtPct(c.peak_conf)}` : ''}
                                        </div>
                                    </div>
                                    <span />
                                    <span className={`pill pill-${c.active ? 'danger' : 'success'}`}>{c.active ? 'Alarm' : 'Clear'}</span>
                                </div>
                            ))}
                        </div>
                    )}
                </Panel>
            </div>

            <Panel title="Incidents" sub={`${incidents.length} in the selected window`}>
                    {incidents.length === 0 ? (
                        <NoData title="No fire or smoke incidents">
                            Nothing has been confirmed in this window. An incident needs several confirming frames, so single detections never raise one.
                        </NoData>
                    ) : (
                        <div className="table-scroll">
                            <table className="data-table an-table">
                                <thead>
                                    <tr>
                                        <th>Incident</th>
                                        <th className="num">Peak conf.</th>
                                        <th className="num">Lasted</th>
                                        <th className="num">Frames</th>
                                        <th>Status</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {incidents.map((f) => (
                                        <tr key={f.id}>
                                            <td>
                                                <div className="rank-name">{f.class_name ? f.class_name.charAt(0).toUpperCase() + f.class_name.slice(1) : 'Fire'} · {camName(f.camera_id)}</div>
                                                <div className="rank-sub">{fmtTime(f.started_at, true)}</div>
                                            </td>
                                            <td className="num">{fmtPct(f.peak_conf)}</td>
                                            <td className="num">{fmtDuration(f.duration_s)}</td>
                                            <td className="num">{f.hits ?? DASH}{f.retriggers ? ` · ${f.retriggers}× re-trig.` : ''}</td>
                                            <td><span className={`pill pill-${STATUS_PILL[f.status] || 'neutral'}`}>{f.status || DASH}</span></td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                    <Definitions items={alerts?.definitions || status?.definitions} />
            </Panel>
        </Wrap>
    );
}
