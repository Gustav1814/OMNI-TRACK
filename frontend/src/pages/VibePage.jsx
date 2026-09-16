/**
 * OmniTrack AI — Store Vibe (live)
 * Polls /api/vibe/current + /api/vibe/trend, also listens to WS vibe_update.
 */

import React, { useState } from 'react';
import { motion } from 'framer-motion';
import {
    Smile, Zap, Hand, Users, TrendingUp, TrendingDown, Minus, Info,
} from 'lucide-react';
import {
    AreaChart, Area, ResponsiveContainer, XAxis, YAxis, Tooltip, CartesianGrid,
} from 'recharts';
import { vibeAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import useWebSocket from '../hooks/useWebSocket';

// Mirrors StoreVibeEngine.VIBE_LABELS in the backend.
const BANDS = [
    { min: 0, max: 20, label: 'Quiet', color: 'var(--status-idle)' },
    { min: 20, max: 40, label: 'Calm', color: 'var(--accent-primary)' },
    { min: 40, max: 60, label: 'Steady', color: 'var(--accent-secondary)' },
    { min: 60, max: 80, label: 'Energetic', color: 'var(--status-ok)' },
    { min: 80, max: 101, label: 'Buzzing', color: 'var(--status-warn)' },
];

// Weights are equal in StoreVibeEngine; shown so the score is explainable.
const COMPONENTS = [
    { key: 'sentiment_score', label: 'Sentiment', icon: Smile, weight: 25, hint: 'Facial emotion across detected shoppers' },
    { key: 'energy_score', label: 'Energy', icon: Zap, weight: 25, hint: 'Movement and activity level in frame' },
    { key: 'engagement_score', label: 'Engagement', icon: Hand, weight: 25, hint: 'Dwell time and shelf interactions' },
    { key: 'foot_traffic_score', label: 'Traffic', icon: Users, weight: 25, hint: 'Occupancy against store capacity' },
];

const bandFor = (score) => BANDS.find((b) => score >= b.min && score < b.max) || BANDS[2];

export default function VibePage() {
    const { data: current } = useLivePoll(() => vibeAPI.current(), { intervalMs: 5000 });
    const { data: trend } = useLivePoll(() => vibeAPI.trend(24), { intervalMs: 60000 });

    const [live, setLive] = useState(null);
    useWebSocket('/ws/live', {
        onType: { vibe_update: (d) => setLive(d) },
    });

    const vibe = live ?? current ?? null;
    const score = Math.max(0, Math.min(100, Number(vibe?.overall_score ?? 0)));
    const band = bandFor(score);
    const label = vibe?.vibe_label || vibe?.label || band.label;
    const hasData = vibe != null && Number(vibe?.overall_score ?? 0) > 0;

    const trendData = Array.isArray(trend) ? trend.slice(0, 48).map((v, i) => ({
        t: typeof v.hour === 'string' ? v.hour.slice(11, 16) : `T-${i}`,
        score: Number(v.score ?? v.overall) || 0,
    })).reverse() : [];

    // Direction over the recent window, so the number has context.
    const delta = trendData.length >= 2
        ? trendData[trendData.length - 1].score - trendData[0].score
        : null;

    const breakdown = vibe?.breakdown || {};
    const componentValues = COMPONENTS.map((c) => ({
        ...c,
        value: Number(vibe?.[c.key] ?? breakdown?.[c.key] ?? 0),
    }));
    const weakest = hasData
        ? componentValues.reduce((a, b) => (b.value < a.value ? b : a))
        : null;

    return (
        <div className="page-scroll vibe-page">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Store Vibe</h1>
                    <p className="page-subtitle">
                        A single 0–100 read on how the store feels, from four equally weighted signals.
                    </p>
                </div>
                <div className={`vibe-live-chip ${hasData ? 'is-live' : ''}`}>
                    <i className="vibe-live-dot" />
                    {hasData ? 'Live' : 'Awaiting data'}
                </div>
            </div>

            <div className="vibe-grid">
                <div className="card vibe-score-card">
                    <Gauge score={score} label={label} band={band} hasData={hasData} />

                    <div className="vibe-delta">
                        {delta == null ? (
                            <span className="vibe-delta-idle">
                                <Minus size={13} /> No trend yet
                            </span>
                        ) : (
                            <span className={delta >= 0 ? 'vibe-delta-up' : 'vibe-delta-down'}>
                                {delta >= 0 ? <TrendingUp size={13} /> : <TrendingDown size={13} />}
                                {delta >= 0 ? '+' : ''}{delta.toFixed(1)} over 24h
                            </span>
                        )}
                    </div>

                    <div className="vibe-scale">
                        {BANDS.map((b) => (
                            <div
                                key={b.label}
                                className={`vibe-scale-band ${b.label === band.label ? 'is-active' : ''}`}
                                style={{ '--band-color': b.color }}
                            >
                                <span className="vibe-scale-bar" />
                                <span className="vibe-scale-label">{b.label}</span>
                            </div>
                        ))}
                    </div>
                </div>

                <div className="card vibe-components-card">
                    <div className="card-header">
                        <div>
                            <h3 className="card-title">What drives the score</h3>
                            <div className="card-subtitle">Four signals, each weighted 25%</div>
                        </div>
                    </div>

                    <div className="vibe-components">
                        {componentValues.map((c) => {
                            const Icon = c.icon;
                            return (
                                <div className="vibe-component" key={c.key}>
                                    <div className="vibe-component-head">
                                        <span className="vibe-component-icon"><Icon size={15} /></span>
                                        <div className="vibe-component-text">
                                            <span className="vibe-component-label">{c.label}</span>
                                            <span className="vibe-component-hint">{c.hint}</span>
                                        </div>
                                        <span className="vibe-component-value">{c.value.toFixed(1)}</span>
                                    </div>
                                    <div className="vibe-component-track">
                                        <span
                                            className="vibe-component-fill"
                                            style={{ width: `${Math.max(0, Math.min(100, c.value))}%` }}
                                        />
                                    </div>
                                </div>
                            );
                        })}
                    </div>

                    <div className="vibe-insight">
                        <Info size={14} />
                        {hasData ? (
                            <span>
                                <strong>{weakest.label}</strong> is holding the score down at{' '}
                                {weakest.value.toFixed(1)} — {weakest.hint.toLowerCase()}.
                            </span>
                        ) : (
                            <span>
                                Start a session on Video Feeds. Each signal fills in as the
                                pipeline processes frames.
                            </span>
                        )}
                    </div>
                </div>
            </div>

            <div className="card vibe-trend-card">
                <div className="card-header">
                    <div>
                        <h3 className="card-title">24-hour trend</h3>
                        <div className="card-subtitle">
                            {trendData.length ? `${trendData.length} samples` : 'Samples are written as the pipeline runs'}
                        </div>
                    </div>
                </div>
                {trendData.length === 0 ? (
                    <div className="vibe-trend-empty">
                        <div className="chart-empty-icon"><TrendingUp size={22} /></div>
                        <p className="chart-empty-title">No samples yet</p>
                        <p className="chart-empty-copy">
                            The vibe score is recorded continuously while a session runs.
                            Come back after a few minutes of processing.
                        </p>
                    </div>
                ) : (
                    <div className="vibe-trend-chart">
                        <ResponsiveContainer>
                            <AreaChart data={trendData} margin={{ top: 8, right: 8, bottom: 0, left: -18 }}>
                                <defs>
                                    <linearGradient id="vibeA" x1="0" y1="0" x2="0" y2="1">
                                        <stop offset="0%" stopColor="var(--accent-primary)" stopOpacity={0.45} />
                                        <stop offset="100%" stopColor="var(--accent-primary)" stopOpacity={0} />
                                    </linearGradient>
                                </defs>
                                <CartesianGrid stroke="var(--border)" vertical={false} />
                                <XAxis dataKey="t" stroke="var(--text-muted)" fontSize={11} tickLine={false} axisLine={false} minTickGap={24} />
                                <YAxis stroke="var(--text-muted)" fontSize={11} domain={[0, 100]} tickLine={false} axisLine={false} width={44} />
                                <Tooltip
                                    contentStyle={{
                                        background: 'var(--bg-card)',
                                        border: '1px solid var(--border)',
                                        borderRadius: 10,
                                        color: 'var(--text-primary)',
                                        fontSize: 12,
                                    }}
                                    labelStyle={{ color: 'var(--text-muted)' }}
                                />
                                <Area
                                    type="monotone"
                                    dataKey="score"
                                    stroke="var(--accent-primary)"
                                    strokeWidth={2}
                                    fill="url(#vibeA)"
                                />
                            </AreaChart>
                        </ResponsiveContainer>
                    </div>
                )}
            </div>
        </div>
    );
}

function Gauge({ score, label, band, hasData }) {
    const R = 76;
    const circ = 2 * Math.PI * R;
    // Leave a gap at the bottom so the arc reads as a dial, not a ring.
    const sweep = 0.75;
    const arcLength = circ * sweep;
    const filled = arcLength * (score / 100);

    return (
        <motion.div
            className="vibe-gauge"
            initial={{ scale: 0.97, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={{ duration: 0.35 }}
        >
            <svg viewBox="0 0 200 200" className="vibe-gauge-svg" role="img"
                aria-label={`Store vibe score ${Math.round(score)} out of 100, ${label}`}>
                <circle
                    cx="100" cy="100" r={R}
                    className="vibe-gauge-track"
                    strokeWidth="13" fill="none"
                    strokeDasharray={`${arcLength} ${circ}`}
                    strokeLinecap="round"
                    transform="rotate(135 100 100)"
                />
                <circle
                    cx="100" cy="100" r={R}
                    strokeWidth="13" fill="none"
                    stroke={band.color}
                    strokeLinecap="round"
                    strokeDasharray={`${filled} ${circ}`}
                    transform="rotate(135 100 100)"
                    className="vibe-gauge-arc"
                />
            </svg>
            <div className="vibe-gauge-center">
                <span className={`vibe-gauge-score ${hasData ? '' : 'is-empty'}`}>
                    {hasData ? Math.round(score) : '0'}
                </span>
                <span className="vibe-gauge-max">out of 100</span>
                <span className="vibe-gauge-label" style={{ color: band.color }}>{label}</span>
            </div>
        </motion.div>
    );
}
