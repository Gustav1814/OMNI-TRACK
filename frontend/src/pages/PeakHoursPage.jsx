/**
 * OmniTrack AI — Rush hours
 * Hourly footfall for a day (/api/peak-hours/today) and a weekday x hour
 * heatmap (/api/peak-hours/weekly), in the store time zone. Hours in which no
 * camera was analysed are shown as "not observed", never as zero. Daily totals
 * live on Footfall; the peak hour lives only here.
 */

import React, { useEffect, useMemo, useState } from 'react';
import {
    Bar, BarChart, CartesianGrid, Cell, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { peakHoursAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import Metric from '../components/ui/Metric';
import { useTokens, withAlpha } from '../lib/tokens';
import { rechartsTheme } from '../lib/chartTheme';
import {
    AnalyticsPage, Definitions, FeatureCard, HeroRow, NoData, Panel, Seg, fmtNum, hourLabel,
} from '../components/zones/analyticsUi';

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const DAYS = [
    { value: 7, label: '7 days' },
    { value: 14, label: '14 days' },
    { value: 28, label: '28 days' },
];
const SCALE_STEPS = [0.1, 0.3, 0.5, 0.7, 0.9];

export default function PeakHoursPage({ embedded = false }) {
    const [day, setDay] = useState('');
    const [days, setDays] = useState(7);
    const { data, error, refresh } = useLivePoll(() => peakHoursAPI.today(day || undefined), { intervalMs: 60000 });
    const { data: weekly, refresh: refreshWeekly } = useLivePoll(() => peakHoursAPI.weekly(days), { intervalMs: 300000 });
    useEffect(() => { refresh(); }, [day, refresh]);
    useEffect(() => { refreshWeekly(); }, [days, refreshWeekly]);
    const t = useTokens();
    const th = rechartsTheme(t);

    const metric = data?.metric || 'visitors';
    const metricLabel = metric === 'entries' ? 'Entries' : 'Visitors';
    const chart = useMemo(() => (data?.hourly || []).map((h) => ({
        hour: hourLabel(h.hour),
        value: h.observed ? (h[metric] ?? 0) : null,
        observed: h.observed,
        isPeak: data?.peak_hour === h.hour,
    })), [data, metric]);
    const unobserved = chart.filter((c) => !c.observed).length;
    const observedBars = chart.filter((c) => c.value != null);
    // With one observed hour the quietest is the peak; showing it twice says nothing.
    const quietest = observedBars.length < 2 ? null : observedBars.reduce((best, c) => (!best || c.value < best.value ? c : best), null);
    const hasPeak = data?.status === 'ok' && data?.peak_hour != null;

    const heat = useMemo(() => {
        const grid = {};
        let max = 0;
        for (const c of weekly?.cells || []) {
            grid[`${c.weekday}-${c.hour}`] = c.visitors;
            max = Math.max(max, c.visitors);
        }
        return { grid, max };
    }, [weekly]);
    const weeklyPeak = useMemo(() => (weekly?.cells || []).reduce((best, c) => (c.visitors > 0 && (!best || c.visitors > best.visitors) ? c : best), null), [weekly]);

    const valueLabel = ({ x, y, width, index }) => {
        const c = chart[index];
        if (!c || c.value == null || c.value <= 0) return null;
        return (
            <text x={x + width / 2} y={y - 6} textAnchor="middle" fill={c.isPeak ? t.text : t.text2} fontSize={th.xAxis.tick.fontSize} fontFamily={th.xAxis.tick.fontFamily}>
                {fmtNum(c.value)}
            </text>
        );
    };

    return (
        <AnalyticsPage
            embedded={embedded}
            title="Rush hours"
            subtitle={`${metricLabel} per hour on ${data?.date || 'the selected day'}${data?.timezone ? ` (${data.timezone})` : ''}.`}
            controls={(
                <input
                    className="form-input an-select"
                    type="date"
                    aria-label="Day"
                    value={day}
                    onChange={(e) => setDay(e.target.value)}
                />
            )}
        >
            {error && <div className="alert-banner danger">Unable to reach footfall analytics.</div>}

            <HeroRow
                hero={{
                    label: `Peak hour${data?.date ? `, ${data.date}` : ''}`,
                    value: hasPeak ? hourLabel(data.peak_hour) : null,
                    foot: hasPeak && data?.peak_count != null
                        ? `${fmtNum(data.peak_count)} ${metricLabel.toLowerCase()} in that hour, the most of the day`
                        : 'No hour observed on this day',
                }}
            >
                <Metric label="Quietest observed hour" value={quietest ? quietest.hour : null} size="sm" sub={quietest ? `${fmtNum(quietest.value)} ${metricLabel.toLowerCase()}` : 'Needs two observed hours'} />
                <Metric label="Hours observed" value={data ? data.observed_hours ?? null : null} unit="of 24" />
            </HeroRow>

            <div className="an-grid an-grid--feature">
            <FeatureCard
                label={`Busiest slot, last ${days} days`}
                value={weeklyPeak ? `${WEEKDAYS[weeklyPeak.weekday - 1]} ${hourLabel(weeklyPeak.hour)}` : null}
                sub={weeklyPeak ? `${fmtNum(weeklyPeak.visitors)} visitors in that hour` : 'No visitors recorded in this period'}
            />
            <Panel
                title={`${metricLabel} per hour`}
                sub={data?.status === 'ok' ? `Busiest hour highlighted.${unobserved ? ` ${unobserved} hour(s) not observed have no bar.` : ''}` : undefined}
            >
                {data?.status !== 'ok' ? (
                    <NoData title="No footfall measured on this day">
                        Footfall is counted while cameras are analysed. Draw an entry line at the door for exact entry counts.
                    </NoData>
                ) : (
                    <div className="an-chart">
                        <ResponsiveContainer>
                            <BarChart data={chart} margin={{ top: 24, right: 0, left: -8, bottom: 0 }} barCategoryGap="20%">
                                <CartesianGrid {...th.grid} />
                                <XAxis dataKey="hour" {...th.xAxis} interval={2} />
                                <YAxis {...th.yAxis} allowDecimals={false} />
                                <Tooltip
                                    {...th.tooltip}
                                    cursor={{ fill: withAlpha(t.text, 0.04) }}
                                    formatter={(v, _n, p) => (p?.payload?.observed ? [fmtNum(v), metricLabel] : ['not observed', metricLabel])}
                                />
                                <Bar dataKey="value" name={metricLabel} radius={[4, 4, 0, 0]}>
                                    {chart.map((c) => <Cell key={c.hour} fill={c.isPeak ? t.highlight : t.neutral} />)}
                                    <LabelList dataKey="value" content={valueLabel} />
                                </Bar>
                            </BarChart>
                        </ResponsiveContainer>
                    </div>
                )}
                <Definitions items={data?.definitions} />
            </Panel>
            </div>

            <Panel
                title="Weekly pattern"
                sub={`Visitors per weekday and hour, last ${days} days`}
                actions={<Seg value={days} onChange={setDays} options={DAYS} label="Weekly window" />}
            >
                {weekly?.status !== 'ok' ? (
                    <NoData title="No visitors recorded in this period">
                        The weekly pattern fills in as cameras are analysed on different days and hours.
                    </NoData>
                ) : (
                    <>
                        <div className="an-heat-scroll">
                            <div className="an-heat" role="table" aria-label="Visitors by weekday and hour">
                                <span />
                                {Array.from({ length: 24 }, (_, h) => (
                                    <span key={h} className="an-heat-hour">{h % 3 === 0 ? String(h).padStart(2, '0') : ''}</span>
                                ))}
                                {WEEKDAYS.map((name, i) => (
                                    <React.Fragment key={name}>
                                        <span className="an-heat-label">{name}</span>
                                        {Array.from({ length: 24 }, (_, h) => {
                                            const v = heat.grid[`${i + 1}-${h}`];
                                            const has = v != null && v > 0;
                                            return (
                                                <div
                                                    key={h}
                                                    className={`an-heat-cell${has ? '' : ' is-empty'}`}
                                                    title={`${name} ${hourLabel(h)}: ${v != null ? fmtNum(v) : 'no visitors recorded'}`}
                                                    style={has ? { '--v': heat.max ? v / heat.max : 0 } : undefined}
                                                />
                                            );
                                        })}
                                    </React.Fragment>
                                ))}
                            </div>
                        </div>
                        <div className="an-scale" aria-hidden>
                            <span>Fewer</span>
                            <span className="an-scale-steps">
                                {SCALE_STEPS.map((v) => <span key={v} className="an-heat-cell" style={{ '--v': v }} />)}
                            </span>
                            <span>More (max {fmtNum(heat.max)})</span>
                            <span className="swatch" />
                            <span>None recorded</span>
                        </div>
                    </>
                )}
            </Panel>
        </AnalyticsPage>
    );
}
