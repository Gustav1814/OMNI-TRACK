/**
 * OmniTrack AI — Queues (checkout)
 * Lanes are queue + counter zones that share a lane name. Wait = queue entry
 * to counter entry, service = time at the counter, from /api/checkout/*.
 */

import React, { useEffect, useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import {
    Bar, BarChart, CartesianGrid, Cell, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { checkoutAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import Metric from '../components/ui/Metric';
import { useTokens, withAlpha } from '../lib/tokens';
import { rechartsTheme } from '../lib/chartTheme';
import {
    AnalyticsPage, DASH, Definitions, FeatureCard, HeroRow, NoData, Panel, WindowSelect,
    durationMetric, fmtDuration, fmtNum, fmtPct, isNum, pctMetric,
} from '../components/zones/analyticsUi';

export default function CheckoutPage({ embedded = false }) {
    const [win, setWin] = useState('');
    const { data: metrics, error, refresh: refreshMetrics } = useLivePoll(
        () => checkoutAPI.metrics(win || undefined), { intervalMs: 4000 },
    );
    const { data: summary, refresh: refreshSummary } = useLivePoll(
        () => checkoutAPI.summary(win || undefined), { intervalMs: 10000 },
    );
    useEffect(() => { refreshMetrics(); refreshSummary(); }, [win, refreshMetrics, refreshSummary]);
    const t = useTokens();
    const th = rechartsTheme(t);

    const lanes = metrics?.lanes || [];
    const unpaired = lanes.filter((l) => !l.paired);
    const longQueues = lanes.filter((l) => l.long_queue_alert);
    const waitMetric = durationMetric(summary?.median_wait_s);
    const serviceMetric = durationMetric(summary?.median_service_s);
    const abandon = pctMetric(summary?.abandonment_rate);
    const windowLabel = metrics?.window?.label || summary?.window?.label;

    const waits = lanes
        .filter((l) => isNum(l.median_wait_s))
        .map((l) => ({ lane: l.lane, wait: Math.round(l.median_wait_s) }))
        .sort((a, b) => b.wait - a.wait);
    const longest = waits[0]?.lane;

    const lanesPanel = (
        <Panel title="Lanes" sub={windowLabel ? windowLabel.charAt(0).toUpperCase() + windowLabel.slice(1) : undefined}>
            {lanes.length === 0 ? (
                <NoData title="No checkout lanes configured" setupLink>
                    Draw a queue zone where customers wait and a counter zone where they are served, and give both the same lane name.
                </NoData>
            ) : (
                <>
                    {unpaired.length > 0 && (
                        <div className="alert-banner info">
                            <span>
                                {unpaired.map((l) => l.lane).join(', ')}: needs both a queue zone and a counter zone with the same lane name before wait can be measured.
                            </span>
                        </div>
                    )}
                    <div className="table-scroll">
                        <table className="data-table an-table">
                            <thead>
                                <tr>
                                    <th>Lane</th>
                                    <th className="num">Queue now</th>
                                    <th className="num">Median wait</th>
                                    <th className="num">P90 wait</th>
                                    <th className="num">Served</th>
                                    <th className="num">Median service</th>
                                    <th className="num">Per hour</th>
                                    <th className="num">Left queue</th>
                                </tr>
                            </thead>
                            <tbody>
                                {lanes.map((l) => (
                                    <tr key={`${l.camera_id}-${l.lane}`}>
                                        <td>
                                            <div className="rank-name">{l.lane}</div>
                                            <div className="rank-sub">
                                                Camera {l.camera_id} · {l.queue_zone?.name || 'no queue zone'} → {l.counter_zone?.name || 'no counter zone'}
                                            </div>
                                        </td>
                                        <td className="num">
                                            {l.queue_length_now != null
                                                ? <>{l.queue_length_now}{l.long_queue_alert ? <> <span className="pill pill-warn">long</span></> : null}</>
                                                : <span className="an-row-val--muted">not live</span>}
                                        </td>
                                        <td className="num">{fmtDuration(l.median_wait_s)}</td>
                                        <td className="num">{fmtDuration(l.p90_wait_s)}</td>
                                        <td className="num">{fmtNum(l.served)}</td>
                                        <td className="num">{fmtDuration(l.median_service_s)}</td>
                                        <td className="num">{fmtNum(l.throughput_per_hour, 1)}</td>
                                        <td className="num">{l.abandoned != null ? `${l.abandoned} (${fmtPct(l.abandonment_rate)})` : DASH}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                    {metrics?.status === 'no_data' && (
                        <p className="an-note">No checkout activity measured in this window yet. Start a camera that covers these lanes.</p>
                    )}
                </>
            )}
            <Definitions items={metrics?.definitions} />
        </Panel>
    );

    return (
        <AnalyticsPage
            embedded={embedded}
            title="Queues"
            subtitle="Queue length, wait and service time per lane, measured from tracked people."
            controls={<WindowSelect value={win} onChange={setWin} />}
        >
            {error && <div className="alert-banner danger">Unable to reach checkout analytics.</div>}
            {longQueues.map((l) => (
                <div key={`${l.camera_id}-${l.lane}`} className="alert-banner warning" role="alert">
                    <AlertTriangle size={16} strokeWidth={1.75} />
                    <span>Long queue at lane <strong>{l.lane}</strong>: {l.queue_length_now} waiting.</span>
                </div>
            ))}

            <HeroRow
                hero={{
                    label: 'Median wait',
                    value: waitMetric.value,
                    unit: waitMetric.unit,
                    decimals: waitMetric.decimals,
                    foot: waitMetric.value != null
                        ? `From joining a queue to reaching the counter${windowLabel ? `, ${windowLabel.toLowerCase()}` : ''}`
                        : 'Wait is measured once someone moves from a queue zone to its counter zone',
                }}
            >
                <Metric label="In queues now" value={summary?.queue_length_now ?? null} />
                <Metric label="Median service" value={serviceMetric.value} unit={serviceMetric.unit} decimals={serviceMetric.decimals} />
                <Metric label="Left the queue" value={abandon.value} unit={abandon.unit} sub={abandon.value != null ? 'before reaching a counter' : undefined} />
            </HeroRow>

            <div className="an-grid an-grid--feature">
                <FeatureCard
                    label="Customers waited"
                    value={summary?.customers_waited ?? null}
                    sub={summary?.customers_waited != null
                        ? `${fmtNum(summary.served ?? 0)} served at a counter${windowLabel ? `, ${windowLabel.toLowerCase()}` : ''}`
                        : 'Counted once someone stands in a queue zone'}
                />
                {waits.length > 1 ? (
                <Panel title="Median wait by lane" sub={`Seconds${windowLabel ? `, ${windowLabel.toLowerCase()}` : ''}. Longest wait highlighted.`}>
                    <div className="an-chart an-chart--sm">
                        <ResponsiveContainer>
                            <BarChart data={waits} layout="vertical" margin={{ top: 0, right: 48, left: 0, bottom: 0 }}>
                                <CartesianGrid {...th.grid} horizontal={false} vertical />
                                <XAxis type="number" {...th.xAxis} allowDecimals={false} />
                                <YAxis type="category" dataKey="lane" {...th.yAxis} width={88} />
                                <Tooltip {...th.tooltip} cursor={{ fill: withAlpha(t.text, 0.04) }} formatter={(v) => [fmtDuration(v), 'Median wait']} />
                                <Bar dataKey="wait" name="Median wait" radius={[0, 4, 4, 0]} maxBarSize={20}>
                                    {waits.map((w) => <Cell key={w.lane} fill={w.lane === longest ? t.highlight : t.neutral} />)}
                                    <LabelList dataKey="wait" position="right" formatter={(v) => fmtDuration(v)} {...th.xAxis.tick} />
                                </Bar>
                            </BarChart>
                        </ResponsiveContainer>
                    </div>
                </Panel>
                ) : lanesPanel}
            </div>

            {waits.length > 1 ? lanesPanel : null}
        </AnalyticsPage>
    );
}
