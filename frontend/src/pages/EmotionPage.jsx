/**
 * OmniTrack AI — Emotion Recognition (live)
 * Polls /api/emotion/current + /api/emotion/store-sentiment.
 */

import React from 'react';
import { motion } from 'framer-motion';
import { SmilePlus, Heart, Meh, Frown } from 'lucide-react';
import {
    PieChart, Pie, Cell, ResponsiveContainer, Tooltip, Legend,
} from 'recharts';
import { emotionAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';

const EMOTION_COLOR = {
    happy: '#10b981',
    neutral: '#64748b',
    surprise: '#fbbf24',
    sad: '#3b82f6',
    angry: '#f43f5e',
    fear: '#a855f7',
    disgust: '#84cc16',
};

export default function EmotionPage() {
    const { data: current } = useLivePoll(() => emotionAPI.current(), { intervalMs: 4000 });
    const { data: sentiment } = useLivePoll(() => emotionAPI.sentiment(), { intervalMs: 10000 });

    const zones = Array.isArray(current) ? current : [];
    const sentimentScore = Number(sentiment?.sentiment_score ?? 0);

    const totalDist = zones.reduce((acc, z) => {
        const dist = z.emotion_distribution || {};
        Object.entries(dist).forEach(([k, v]) => { acc[k] = (acc[k] || 0) + v; });
        return acc;
    }, {});
    const totalSamples = zones.reduce((a, z) => a + (z.sample_count || 0), 0) || 1;
    const pieData = Object.entries(totalDist).map(([name, value]) => ({
        name,
        value: (value / totalSamples) * 100,
    }));

    const label = sentimentScore > 0.3 ? 'Positive'
        : sentimentScore < -0.3 ? 'Negative' : 'Neutral';
    const Icon = sentimentScore > 0.3 ? Heart : sentimentScore < -0.3 ? Frown : Meh;

    return (
        <div className="page-scroll">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Emotion Recognition</h1>
                    <p className="page-subtitle">DeepFace + FER · aggregated sentiment per zone</p>
                </div>
            </div>

            <div className="stats-grid">
                <Stat
                    icon={Icon}
                    label="Store Sentiment"
                    value={label}
                    accent={sentimentScore > 0 ? 'emerald' : sentimentScore < 0 ? 'rose' : 'amber'}
                />
                <Stat icon={SmilePlus} label="Sentiment Score" value={sentimentScore.toFixed(2)} accent="indigo" />
                <Stat icon={SmilePlus} label="Zones Sampled" value={zones.length} accent="cyan" />
                <Stat icon={SmilePlus} label="Total Samples" value={totalSamples === 1 ? 0 : totalSamples} accent="gold" />
            </div>

            <div className="two-col">
                <div className="card">
                    <div className="card-header">
                        <h3 className="card-title">Emotion Mix (all zones)</h3>
                        <div className="card-subtitle">% of aggregated samples</div>
                    </div>
                    {pieData.length === 0 ? (
                        <div className="page-empty-hint">
                            No emotion samples yet.
                        </div>
                    ) : (
                        <div style={{ height: 300 }}>
                            <ResponsiveContainer>
                                <PieChart>
                                    <Pie
                                        data={pieData} dataKey="value" nameKey="name"
                                        cx="50%" cy="50%" outerRadius={90} innerRadius={50}
                                        paddingAngle={2}
                                    >
                                        {pieData.map((entry) => (
                                            <Cell key={entry.name} fill={EMOTION_COLOR[entry.name] || '#64748b'} />
                                        ))}
                                    </Pie>
                                    <Tooltip contentStyle={{ background: '#111', border: '1px solid #222' }} />
                                    <Legend wrapperStyle={{ fontSize: 12 }} />
                                </PieChart>
                            </ResponsiveContainer>
                        </div>
                    )}
                </div>

                <div className="card">
                    <div className="card-header">
                        <h3 className="card-title">Per-Zone</h3>
                    </div>
                    <div style={{ display: 'grid', gap: 6, maxHeight: 300, overflow: 'auto' }}>
                        {zones.length === 0 && (
                            <div style={{ color: 'var(--text-muted)', padding: 12 }}>No zones.</div>
                        )}
                        {zones.map((z) => (
                            <div key={z.zone} style={{
                                padding: '10px 12px',
                                background: 'var(--bg-glass)',
                                border: '1px solid var(--border)', borderRadius: 10,
                            }}>
                                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                                    <strong>{z.zone}</strong>
                                    <span
                                        className="pill"
                                        style={{ color: EMOTION_COLOR[z.dominant_emotion] || '#64748b' }}
                                    >
                                        {z.dominant_emotion}
                                    </span>
                                </div>
                                <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                                    {z.sample_count} samples · sentiment {Number(z.sentiment_score || 0).toFixed(2)}
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            </div>
        </div>
    );
}

function Stat({ icon: Icon, label, value, accent = 'indigo' }) {
    return (
        <motion.div className="stat-card" whileHover={{ y: -2 }}>
            <div className={`stat-icon stat-icon-${accent}`}><Icon size={18} /></div>
            <div className="stat-label">{label}</div>
            <div className="stat-value">{value}</div>
        </motion.div>
    );
}
