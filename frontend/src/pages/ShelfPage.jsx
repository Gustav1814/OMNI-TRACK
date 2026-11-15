/**
 * OmniTrack AI — Shelf
 * Vision only: per shelf zone, passers-by, visits (>= 3 s), engaged visits
 * (> 10 s), dwell percentiles and an attention score, from
 * /api/shelf/engagement. Nothing here is about products or stock.
 */

import React, { useEffect, useState } from 'react';
import {
    Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { shelfAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import Metric from '../components/ui/Metric';
import { useTokens, withAlpha } from '../lib/tokens';
import { rechartsTheme } from '../lib/chartTheme';
import {
    AnalyticsPage, DASH, Definitions, FeatureCard, HeroRow, NoData, Panel, WindowSelect, fmtDuration, fmtNum, fmtPct, isNum,
} from '../components/zones/analyticsUi';

export default function ShelfPage({ embedded = false }) {
    const [win, setWin] = useState('');
    const { data, error, refresh } = useLivePoll(() => shelfAPI.engagement(win || undefined), { intervalMs: 10000 });
    useEffect(() => { refresh(); }, [win, refresh]);
    const t = useTokens();
    const th = rechartsTheme(t);

    const zones = data?.zones || [];
    const measured = zones.filter((z) => z.measured);
    const totals = data?.totals;
    const top = measured.find((z) => z.attention_score != null && (z.visits || 0) > 0);
    const dwellValues = measured.map((z) => z.median_dwell_s).filter((v) => v != null);
    const chartData = measured.slice(0, 12).map((z) => ({
        name: z.zone_name,
        passers: z.passers_by || 0,
        visits: z.visits || 0,
        engaged: z.engaged_visits || 0,
    }));
    const stopRate = totals && isNum(totals.visits) && totals.passers_by > 0 ? totals.visits / totals.passers_by : null;
    const windowLabel = data?.window?.label || 'this window';

    return (
        <AnalyticsPage
            embedded={embedded}
            title="Shelf"
            subtitle="Who walks past, who stops, and who stays in each shelf zone."
            controls={<WindowSelect value={win} onChange={setWin} />}
        >
            {error && <div className="alert-banner danger">Unable to reach shelf analytics.</div>}

            <HeroRow
                hero={{
                    label: `Visits, ${windowLabel}`,
                    value: totals ? totals.visits : null,
                    foot: stopRate != null ? `${fmtPct(stopRate)} of passers-by stopped for 3 s or more` : 'A visit is 3 s or more inside a shelf zone',
                }}
            >
                <Metric label="Passers-by" value={totals ? totals.passers_by : null} />
                <Metric label="Zones measured" value={data ? measured.length : null} unit={data ? `of ${zones.length}` : undefined} />
                <Metric label="Most attention" value={top ? top.zone_name : null} size="sm" sub={top ? `score ${fmtNum(top.attention_score)}` : undefined} />
            </HeroRow>

            <div className="an-grid an-grid--feature">
                <FeatureCard
                    label="Engaged visits"
                    value={totals ? totals.engaged_visits ?? null : null}
                    sub={totals && isNum(totals.engaged_visits) ? `Stayed over 10 s, ${windowLabel}` : 'A visit over 10 s in a shelf zone'}
                />
            {zones.length === 0 ? (
                <Panel title="Shelf zones">
                    <NoData title="No shelf zones configured" setupLink>
                        Draw a shelf zone over the floor in front of each display. Visits and dwell are measured only inside zones.
                    </NoData>
                    <Definitions items={data?.definitions} />
                </Panel>
            ) : measured.length === 0 ? (
                <Panel title="Shelf zones">
                    <NoData title={`No shelf activity measured (${windowLabel})`}>
                        {data?.configured_zones} shelf zone(s) are configured, but no camera covering them has been analysed in this window.
                        Start a session on a feed that covers them.
                    </NoData>
                    <Definitions items={data?.definitions} />
                </Panel>
            ) : (
                    <Panel title="Stops vs passers-by" sub={`Top ${chartData.length} zones, ${windowLabel}`}>
                        <div className="legend an-legend">
                            <span className="legend-item"><span className="swatch swatch-neutral" />Passers-by</span>
                            <span className="legend-item"><span className="swatch swatch-mid" />Visits (3 s or more)</span>
                            <span className="legend-item"><span className="swatch swatch-highlight" />Engaged (over 10 s)</span>
                        </div>
                        <div className="an-chart">
                            <ResponsiveContainer>
                                <BarChart data={chartData} barGap={2} barCategoryGap="24%" margin={{ top: 8, right: 0, left: -8, bottom: 0 }}>
                                    <CartesianGrid {...th.grid} />
                                    <XAxis dataKey="name" {...th.xAxis} interval={0} />
                                    <YAxis {...th.yAxis} allowDecimals={false} />
                                    <Tooltip {...th.tooltip} cursor={{ fill: withAlpha(t.text, 0.04) }} />
                                    <Bar dataKey="passers" name="Passers-by" fill={t.neutral} radius={[4, 4, 0, 0]} maxBarSize={20} />
                                    <Bar dataKey="visits" name="Visits" fill={t.text3} radius={[4, 4, 0, 0]} maxBarSize={20} />
                                    <Bar dataKey="engaged" name="Engaged" fill={t.highlight} radius={[4, 4, 0, 0]} maxBarSize={20} />
                                </BarChart>
                            </ResponsiveContainer>
                        </div>
                    </Panel>
            )}
            </div>

            {measured.length > 0 ? (
                    <Panel
                        title="Shelf zones"
                        sub={dwellValues.length ? 'Ranked by attention score (100 = top shelf in this window)' : undefined}
                    >
                        <div className="table-scroll">
                            <table className="data-table an-table">
                                <thead>
                                    <tr>
                                        <th>Zone</th>
                                        <th className="num">Passers</th>
                                        <th className="num">Visits</th>
                                        <th className="num">Engaged</th>
                                        <th className="num">Stop rate</th>
                                        <th className="num">Median dwell</th>
                                        <th className="num">P90 dwell</th>
                                        <th className="num">Attention</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {zones.map((z) => (
                                        <tr key={z.zone_id}>
                                            <td>
                                                <div className="rank-name">{z.zone_name}</div>
                                                <div className="rank-sub">
                                                    Camera {z.camera_id}{z.present_now != null ? ` · ${z.present_now} here now` : ''}
                                                </div>
                                            </td>
                                            <td className="num">{fmtNum(z.passers_by)}</td>
                                            <td className="num">{fmtNum(z.visits)}</td>
                                            <td className="num">{fmtNum(z.engaged_visits)}</td>
                                            <td className="num">{fmtPct(z.stop_rate)}</td>
                                            <td className="num">{fmtDuration(z.median_dwell_s)}</td>
                                            <td className="num">{fmtDuration(z.p90_dwell_s)}</td>
                                            <td className="num">
                                                {isNum(z.attention_score) ? (
                                                    <span className="an-cell-meter">
                                                        <span className={`meter${top && top.zone_id === z.zone_id ? ' meter--accent' : ''}`}>
                                                            <span className="meter-track">
                                                                <span className="meter-fill" style={{ '--value': `${Math.max(0, Math.min(100, z.attention_score))}%` }} />
                                                            </span>
                                                        </span>
                                                        {fmtNum(z.attention_score)}
                                                    </span>
                                                ) : DASH}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                        <Definitions items={data?.definitions} />
                    </Panel>
            ) : null}
        </AnalyticsPage>
    );
}
