/**
 * OmniTrack AI — Store Vibe (live)
 * Polls /api/vibe/current + /api/vibe/trend, also listens to WS vibe_update.
 */

import React, { useState } from 'react';
import { motion } from 'framer-motion';
import { Activity, Heart } from 'lucide-react';
import {
    AreaChart, Area, ResponsiveContainer, XAxis, YAxis, Tooltip, CartesianGrid,
} from 'recharts';
import { vibeAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import useWebSocket from '../hooks/useWebSocket';

export default function VibePage() {
    const { data: current } = useLivePoll(() => vibeAPI.current(), { intervalMs: 15000 });
    const { data: trend } = useLivePoll(() => vibeAPI.trend(24), { intervalMs: 60000 });

    const [live, setLive] = useState(null);
    useWebSocket('/ws/live', {
        onType: { vibe_update: (d) => setLive(d) },
    });

    const vibe = live ?? current ?? null;
    const score = Math.max(0, Math.min(100, Number(vibe?.overall_score ?? 0)));
    const label = vibe?.vibe_label || vibe?.label || '—';

    const trendData = Array.isArray(trend) ? trend.slice(0, 48).map((v, i) => ({
        t: typeof v.hour === 'string' ? v.hour.slice(11, 16) : `T-${i}`,
        score: Number(v.score) || 0,
    })).reverse() : [];

    const breakdown = vibe?.breakdown || {};
    const comps = [
        { key: 'sentiment_score', label: 'Sentiment', color: '#f472b6' },
        { key: 'energy_score', label: 'Energy', color: '#fb923c' },
        { key: 'engagement_score', label: 'Engagement', color: '#6366f1' },
        { key: 'foot_traffic_score', label: 'Traffic', color: '#22d3ee' },
    ];

    return (
        <div className="page-scroll">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Store Vibe Score</h1>
                    <p className="page-subtitle">Composite signal — sentiment × energy × engagement × traffic</p>
                </div>
            </div>

            <div className="two-col">
                <div className="card card-gauge-embed">
                    <Gauge score={score} label={label} />
                </div>

                <div className="card">
                    <div className="card-header">
                        <h3 className="card-title">Components</h3>
                        <div className="card-subtitle">Each contributes 0–100</div>
                    </div>
                    <div style={{ display: 'grid', gap: 10 }}>
                        {comps.map((c) => {
                            const val = Number(vibe?.[c.key] ?? breakdown?.[c.key] ?? 0);
                            return (
                                <div key={c.key}>
                                    <div style={{
                                        display: 'flex', justifyContent: 'space-between',
                                        fontSize: 12, marginBottom: 4, color: 'var(--text-secondary)',
                                    }}>
                                        <span>{c.label}</span>
                                        <span>{val.toFixed(1)}</span>
                                    </div>
                                    <div className="progress-bar">
                                        <div
                                            className="progress-bar-fill"
                                            style={{
                                                width: `${Math.max(0, Math.min(100, val))}%`,
                                                background: c.color,
                                            }}
                                        />
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                </div>
            </div>

            <div className="card" style={{ marginTop: 18 }}>
                <div className="card-header">
                    <h3 className="card-title">24h Trend</h3>
                    <div className="card-subtitle">
                        {trendData.length ? `${trendData.length} data points` : 'Waiting for pipeline data'}
                    </div>
                </div>
                {trendData.length === 0 ? (
                    <div className="page-empty-hint">
                        Nothing yet — vibe samples are written as the pipeline runs.
                    </div>
                ) : (
                    <div style={{ height: 280 }}>
                        <ResponsiveContainer>
                            <AreaChart data={trendData}>
                                <defs>
                                    <linearGradient id="vibeA" x1="0" y1="0" x2="0" y2="1">
                                        <stop offset="0%" stopColor="#6366f1" stopOpacity={0.6} />
                                        <stop offset="100%" stopColor="#6366f1" stopOpacity={0} />
                                    </linearGradient>
                                </defs>
                                <CartesianGrid stroke="rgba(255,255,255,0.05)" />
                                <XAxis dataKey="t" stroke="#71717a" fontSize={10} />
                                <YAxis stroke="#71717a" fontSize={10} domain={[0, 100]} />
                                <Tooltip contentStyle={{ background: '#111', border: '1px solid #222' }} />
                                <Area type="monotone" dataKey="score" stroke="#818cf8" strokeWidth={2} fill="url(#vibeA)" />
                            </AreaChart>
                        </ResponsiveContainer>
                    </div>
                )}
            </div>
        </div>
    );
}

function Gauge({ score, label }) {
    const circ = 2 * Math.PI * 70;
    const offset = circ * (1 - score / 100);
    const color = score >= 75 ? '#10b981'
        : score >= 50 ? '#6366f1'
            : score >= 25 ? '#fbbf24' : '#f43f5e';

    return (
        <motion.div initial={{ scale: 0.95, opacity: 0 }} animate={{ scale: 1, opacity: 1 }}>
            <svg width="200" height="200" viewBox="0 0 200 200">
                <circle cx="100" cy="100" r="70" stroke="rgba(255,255,255,0.08)"
                    strokeWidth="12" fill="none" />
                <circle
                    cx="100" cy="100" r="70" stroke={color} strokeWidth="12" fill="none"
                    strokeLinecap="round" strokeDasharray={circ} strokeDashoffset={offset}
                    transform="rotate(-90 100 100)"
                    style={{ transition: 'stroke-dashoffset 600ms ease' }}
                />
                <text x="100" y="95" textAnchor="middle" fontSize="42" fontWeight="700" fill="#fff">
                    {Math.round(score)}
                </text>
                <text x="100" y="120" textAnchor="middle" fontSize="12" fill="#a1a1aa">
                    /100
                </text>
            </svg>
            <div style={{ textAlign: 'center', marginTop: 10, fontWeight: 700, fontSize: 18 }}>
                <Activity size={14} style={{ verticalAlign: -2, marginRight: 6 }} />
                {label}
            </div>
        </motion.div>
    );
}
