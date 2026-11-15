/**
 * OmniTrack AI — Mood (customer emotion)
 * Real face classifications aggregated per zone (/api/emotion/current) and the
 * store sentiment timeline (/api/emotion/store-sentiment). Nothing is shown
 * unless the emotion model actually classified faces.
 */

import React, { useEffect, useState } from 'react';
import {
    Bar, BarChart, CartesianGrid, Cell, LabelList, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { emotionAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import Metric from '../components/ui/Metric';
import { useTokens, withAlpha } from '../lib/tokens';
import { rechartsTheme } from '../lib/chartTheme';
import {
    AnalyticsPage, DASH, Definitions, FeatureCard, HeroRow, NoData, Panel, Seg, fmtNum, fmtPct, fmtTime, pctMetric,
} from '../components/zones/analyticsUi';

// Fixed order = fixed colour slot (t.series[i]); colour follows the emotion, never the ranking.
const ORDER = ['happy', 'neutral', 'surprise', 'sad', 'angry', 'fear', 'disgust', 'contempt'];
const HOURS = [
    { value: 1, label: '1 h' },
    { value: 6, label: '6 h' },
    { value: 24, label: '24 h' },
    { value: 168, label: '7 days' },
];
const MINUTES = [
    { value: 15, label: '15 min' },
    { value: 30, label: '30 min' },
    { value: 60, label: '1 h' },
    { value: 240, label: '4 h' },
];

const fmtSentiment = (s) => (s == null ? DASH : `${s > 0 ? '+' : ''}${s.toFixed(2)}`);
const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);

export default function EmotionPage({ embedded = false }) {
    const [minutes, setMinutes] = useState(30);
    const [hours, setHours] = useState(6);
    const { data: current, error, refresh: refreshCurrent } = useLivePoll(() => emotionAPI.current(minutes), { intervalMs: 10000 });
    const { data: store, refresh: refreshStore } = useLivePoll(() => emotionAPI.sentiment(hours), { intervalMs: 30000 });
    useEffect(() => { refreshCurrent(); }, [minutes, refreshCurrent]);
    useEffect(() => { refreshStore(); }, [hours, refreshStore]);
    const t = useTokens();
    const th = rechartsTheme(t);

    const modelMissing = (current && current.model_present === false) || (store && store.model_present === false);
    const zones = current?.zones || [];
    const counts = store?.counts || {};
    const total = store?.total_faces || 0;
    const distribution = ORDER
        .filter((k) => counts[k])
        .map((k) => ({ label: cap(k), key: k, count: counts[k], share: total ? counts[k] / total : null }));
    const timeline = (store?.timeline || []).map((p) => ({ ...p, label: fmtTime(p.ts, hours > 24) }));
    const happy = pctMetric(total ? (counts.happy || 0) / total : null);
    const hasStore = store?.status === 'ok';
    const topKey = distribution.reduce((best, d) => (!best || d.count > best.count ? d : best), null)?.key;
    const sparse = timeline.filter((p) => p.sentiment != null).length <= 12;

    return (
        <AnalyticsPage
            embedded={embedded}
            title="Mood"
            subtitle="Facial expressions classified by the emotion model, aggregated per zone. No individual is stored."
            controls={<Seg value={hours} onChange={setHours} options={HOURS} label="Store window" />}
        >
            {error && <div className="alert-banner danger">Unable to reach emotion analytics.</div>}
            {modelMissing && (
                <div className="alert-banner warning">
                    <span>The emotion model is not installed, so emotion recognition is off and nothing is recorded.</span>
                </div>
            )}

            <HeroRow
                hero={{
                    label: 'Store sentiment',
                    value: store?.sentiment != null ? fmtSentiment(store.sentiment) : null,
                    foot: 'Mean valence of classified faces, from −1 (negative) to +1 (positive)',
                }}
            >
                <Metric label="Most common" value={store?.dominant ? cap(store.dominant) : null} size="sm" />
                <Metric label="Happy share" value={happy.value} unit={happy.unit} />
            </HeroRow>

            <div className="an-grid an-grid--feature">
                <FeatureCard
                    label="Faces classified"
                    value={hasStore ? store.total_faces ?? null : null}
                    sub={hasStore ? `Last ${HOURS.find((h) => h.value === hours)?.label || `${hours} h`}, all cameras` : 'Counted when the emotion model reads a visible face'}
                />
            {!hasStore ? (
                <Panel title="Sentiment over time">
                    <NoData title="No faces classified in this window">
                        {modelMissing
                            ? 'Install the emotion model to turn this page on.'
                            : 'Emotion is measured when the emotion model is enabled for a running camera and faces are visible.'}
                    </NoData>
                </Panel>
            ) : (
                <Panel title="Sentiment over time" sub="Mean valence per 5-minute bucket">
                    <div className="legend an-legend">
                        <span className="legend-item"><span className="an-legend-line swatch-1" />Sentiment</span>
                        <span className="legend-item"><span className="an-legend-line swatch-mid" />Neutral (0)</span>
                    </div>
                    <div className="an-chart">
                        <ResponsiveContainer>
                            <LineChart data={timeline} margin={{ top: 8, right: 4, left: -8, bottom: 0 }}>
                                <CartesianGrid {...th.grid} />
                                <XAxis dataKey="label" {...th.xAxis} minTickGap={32} />
                                <YAxis domain={[-1, 1]} ticks={[-1, -0.5, 0, 0.5, 1]} {...th.yAxis} />
                                <ReferenceLine y={0} stroke={t.lineStrong} strokeWidth={1} />
                                <Tooltip {...th.tooltip} formatter={(v) => [fmtSentiment(v), 'Sentiment']} />
                                <Line type="linear" dataKey="sentiment" name="Sentiment" stroke={t.series[0]} strokeWidth={2} dot={sparse ? { r: 3, fill: t.series[0], strokeWidth: 0 } : false} connectNulls={false} activeDot={{ r: 4, stroke: t.surface, strokeWidth: 2 }} />
                            </LineChart>
                        </ResponsiveContainer>
                    </div>
                </Panel>
            )}
            </div>

            <div className={hasStore ? 'an-grid an-grid--even' : 'an-grid an-grid--solo'}>
            <Panel
                title="By zone"
                sub={`Share of each expression, last ${MINUTES.find((m) => m.value === minutes)?.label || `${minutes} min`}`}
                actions={<Seg value={minutes} onChange={setMinutes} options={MINUTES} label="Zone window" />}
            >
                {zones.length === 0 ? (
                    <NoData title="No recent classifications" size="sm">
                        Nothing was classified in this window. Zones appear here once faces are seen inside them.
                    </NoData>
                ) : (
                    <>
                        <div className="legend an-legend">
                            {ORDER.map((k, i) => (
                                <span key={k} className="legend-item"><span className={`swatch swatch-${i + 1}`} />{cap(k)}</span>
                            ))}
                        </div>
                        <div className="an-rows">
                            {zones.map((z) => {
                                const parts = ORDER.filter((k) => z.distribution?.[k]);
                                return (
                                    <div key={`${z.camera_id}-${z.zone_id}`} className="an-row an-row--4">
                                        <div>
                                            <div className="an-row-name">{z.zone_name}</div>
                                            <div className="an-row-sub">Camera {z.camera_id} · {fmtNum(z.total)} faces</div>
                                        </div>
                                        <div
                                            className="an-stack"
                                            role="img"
                                            aria-label={parts.map((k) => `${k} ${fmtPct(z.distribution[k])}`).join(', ') || 'no classifications'}
                                        >
                                            {parts.length ? parts.map((k) => (
                                                <span
                                                    key={k}
                                                    className={`an-seg-${ORDER.indexOf(k) + 1}`}
                                                    title={`${cap(k)} ${fmtPct(z.distribution[k])}`}
                                                    style={{ '--w': z.distribution[k] }}
                                                />
                                            )) : <span className="an-stack-empty" />}
                                        </div>
                                        <span className="an-row-val">{z.dominant ? cap(z.dominant) : DASH}</span>
                                        <span className="an-row-val" title="Sentiment">{fmtSentiment(z.sentiment)}</span>
                                    </div>
                                );
                            })}
                        </div>
                    </>
                )}
                <Definitions items={current?.definitions} />
            </Panel>
            {hasStore ? (
                <Panel title="Expression mix" sub={`${fmtNum(total)} classified faces. Most common highlighted.`}>
                    <div className="an-chart">
                        <ResponsiveContainer>
                            <BarChart data={distribution} layout="vertical" margin={{ top: 0, right: 44, left: 0, bottom: 0 }}>
                                <CartesianGrid {...th.grid} horizontal={false} vertical />
                                <XAxis type="number" {...th.xAxis} allowDecimals={false} />
                                <YAxis type="category" dataKey="label" {...th.yAxis} width={72} />
                                <Tooltip {...th.tooltip} cursor={{ fill: withAlpha(t.text, 0.04) }} formatter={(v, _n, p) => [`${fmtNum(v)} (${fmtPct(p.payload.share)})`, 'Faces']} />
                                <Bar dataKey="count" name="Faces" radius={[0, 4, 4, 0]} maxBarSize={18}>
                                    {distribution.map((d) => <Cell key={d.key} fill={d.key === topKey ? t.highlight : t.neutral} />)}
                                    <LabelList dataKey="share" position="right" formatter={(v) => fmtPct(v)} {...th.xAxis.tick} />
                                </Bar>
                            </BarChart>
                        </ResponsiveContainer>
                    </div>
                </Panel>
            ) : null}
            </div>
        </AnalyticsPage>
    );
}
