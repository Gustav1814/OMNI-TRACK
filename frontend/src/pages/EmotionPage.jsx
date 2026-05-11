/**
 * OmniTrack AI — Emotion Recognition Analytics (Enhanced)
 * Real-time sentiment analysis and emotion tracking per zone
 */

import React from 'react';
import { motion } from 'framer-motion';
import { SmilePlus, Heart, Meh, Frown, Sparkles, Users, Zap } from 'lucide-react';
import {
    PieChart, Pie, Cell, ResponsiveContainer, Tooltip, Legend,
} from 'recharts';
import { emotionAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import GlassCard, { StatCard, PageHeader } from '../components/GlassCard';

const EMOTION_COLOR = {
    happy: '#10b981',
    neutral: '#64748b',
    surprise: '#fbbf24',
    sad: '#3b82f6',
    angry: '#f43f5e',
    fear: '#a855f7',
    disgust: '#84cc16',
};

const EMOTION_BG = {
    happy: 'rgba(16, 185, 129, 0.15)',
    neutral: 'rgba(100, 116, 139, 0.15)',
    surprise: 'rgba(251, 191, 36, 0.15)',
    sad: 'rgba(59, 130, 246, 0.15)',
    angry: 'rgba(244, 63, 94, 0.15)',
    fear: 'rgba(168, 85, 247, 0.15)',
    disgust: 'rgba(132, 204, 22, 0.15)',
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
        color: EMOTION_COLOR[name] || '#64748b',
    }));

    const dominantEmotion = pieData.length > 0 
        ? pieData.reduce((a, b) => a.value > b.value ? a : b).name 
        : 'neutral';

    const sentimentLabel = sentimentScore > 0.3 ? 'Positive'
        : sentimentScore < -0.3 ? 'Negative' : 'Neutral';
    const SentimentIcon = sentimentScore > 0.3 ? Heart : sentimentScore < -0.3 ? Frown : Meh;
    const sentimentAccent = sentimentScore > 0.3 ? 'rose' : sentimentScore < -0 ? 'rose' : 'amber';

    return (
        <div className="page-scroll">
            <PageHeader
                title="Emotion Recognition"
                subtitle="DeepFace AI-powered sentiment analysis across all monitoring zones"
            />

            {/* Stats Grid */}
            <div className="stats-grid-3d">
                <StatCard
                    icon={SentimentIcon}
                    label="Store Sentiment"
                    value={sentimentLabel}
                    accent={sentimentAccent}
                    tag="Overall"
                    progress={Math.abs(sentimentScore) * 100}
                />
                <StatCard
                    icon={SmilePlus}
                    label="Sentiment Score"
                    value={sentimentScore.toFixed(2)}
                    accent="indigo"
                    tag="-1 to +1"
                />
                <StatCard
                    icon={Users}
                    label="Zones Sampled"
                    value={zones.length}
                    accent="cyan"
                    tag="Active"
                />
                <StatCard
                    icon={Sparkles}
                    label="Dominant Emotion"
                    value={dominantEmotion.charAt(0).toUpperCase() + dominantEmotion.slice(1)}
                    accent={EMOTION_COLOR[dominantEmotion] ? 
                        (dominantEmotion === 'happy' ? 'emerald' : 
                         dominantEmotion === 'angry' ? 'rose' : 
                         dominantEmotion === 'surprise' ? 'amber' : 'indigo') : 'indigo'}
                    tag={`${Math.round(pieData.find(p => p.name === dominantEmotion)?.value || 0)}%`}
                />
            </div>

            {/* Main Content */}
            <div className="cards-grid-3d cards-grid-3d-2col">
                {/* Emotion Mix Chart */}
                <GlassCard accent="violet" glow className="bento-span-6">
                    <div className="card-header-3d">
                        <div>
                            <h3 className="card-title-3d">Emotion Distribution</h3>
                            <p className="card-subtitle-3d">
                                {pieData.length 
                                    ? 'Aggregated emotion samples across all zones'
                                    : 'No emotion data collected yet'
                                }
                            </p>
                        </div>
                        <span className="pill-3d pill-3d-info">
                            {totalSamples === 1 ? 0 : totalSamples} Samples
                        </span>
                    </div>

                    {pieData.length === 0 ? (
                        <div className="empty-state-3d">
                            <div className="empty-state-icon">
                                <Zap size={28} />
                            </div>
                            <div className="empty-state-title">No Data Available</div>
                            <div className="empty-state-desc">
                                Start the emotion detection pipeline to begin collecting sentiment data from camera feeds.
                            </div>
                        </div>
                    ) : (
                        <div style={{ height: 320 }}>
                            <ResponsiveContainer width="100%" height="100%">
                                <PieChart>
                                    <Pie
                                        data={pieData} 
                                        dataKey="value" 
                                        nameKey="name"
                                        cx="50%" 
                                        cy="50%" 
                                        outerRadius={100} 
                                        innerRadius={55}
                                        paddingAngle={3}
                                        animationBegin={100}
                                        animationDuration={800}
                                    >
                                        {pieData.map((entry) => (
                                            <Cell 
                                                key={entry.name} 
                                                fill={EMOTION_COLOR[entry.name] || '#64748b'}
                                                stroke="rgba(255,255,255,0.1)"
                                                strokeWidth={2}
                                            />
                                        ))}
                                    </Pie>
                                    <Tooltip 
                                        contentStyle={{ 
                                            background: 'var(--bg-card)', 
                                            border: '1px solid var(--border)',
                                            borderRadius: '12px',
                                            boxShadow: 'var(--shadow-lg)',
                                            padding: '12px'
                                        }}
                                        itemStyle={{ color: 'var(--text-primary)' }}
                                        formatter={(value) => [`${value.toFixed(1)}%`, 'Percentage']}
                                    />
                                    <Legend 
                                        verticalAlign="bottom" 
                                        height={36}
                                        wrapperStyle={{ 
                                            fontSize: 12, 
                                            color: 'var(--text-secondary)',
                                            paddingTop: '10px'
                                        }}
                                    />
                                </PieChart>
                            </ResponsiveContainer>
                        </div>
                    )}
                </GlassCard>

                {/* Zone Details */}
                <GlassCard accent="cyan" className="bento-span-6">
                    <div className="card-header-3d">
                        <div>
                            <h3 className="card-title-3d">Per-Zone Analysis</h3>
                            <p className="card-subtitle-3d">Sentiment metrics by monitoring zone</p>
                        </div>
                        <span className="pill-3d pill-3d-info">
                            {zones.length} Zones
                        </span>
                    </div>

                    <div className="emotion-zones-list">
                        {zones.length === 0 ? (
                            <div className="empty-state-3d" style={{ padding: '2rem' }}>
                                <div className="empty-state-icon">
                                    <Users size={24} />
                                </div>
                                <div className="empty-state-title">Waiting for Data</div>
                                <div className="empty-state-desc">
                                    No zones reporting emotion data yet.
                                </div>
                            </div>
                        ) : (
                            zones.map((z, i) => (
                                <motion.div
                                    key={z.zone}
                                    className="emotion-zone-row"
                                    initial={{ opacity: 0, x: -20 }}
                                    animate={{ opacity: 1, x: 0 }}
                                    transition={{ delay: i * 0.05 }}
                                    whileHover={{ x: 4, scale: 1.01 }}
                                    style={{
                                        background: EMOTION_BG[z.dominant_emotion] || EMOTION_BG.neutral,
                                        borderColor: EMOTION_COLOR[z.dominant_emotion] || EMOTION_COLOR.neutral,
                                    }}
                                >
                                    <div className="emotion-zone-info">
                                        <div className="emotion-zone-name">{z.zone}</div>
                                        <div className="emotion-zone-meta">
                                            {z.sample_count} samples analyzed
                                        </div>
                                    </div>
                                    <div className="emotion-zone-stats">
                                        <div className="emotion-dominant">
                                            <span 
                                                className="emotion-dot"
                                                style={{ background: EMOTION_COLOR[z.dominant_emotion] || '#64748b' }}
                                            />
                                            <span className="emotion-name">{z.dominant_emotion}</span>
                                        </div>
                                        <div className="emotion-score">
                                            {Number(z.sentiment_score || 0).toFixed(2)}
                                        </div>
                                    </div>
                                </motion.div>
                            ))
                        )}
                    </div>
                </GlassCard>
            </div>
        </div>
    );
}

// Styles injection
const emotionStyles = `
.emotion-zones-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
    max-height: 350px;
    overflow-y: auto;
}

.emotion-zone-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 14px 16px;
    border: 1px solid;
    border-radius: 12px;
    transition: all 0.2s ease;
}

.emotion-zone-name {
    font-weight: 600;
    font-size: 0.95rem;
    color: var(--text-primary);
}

.emotion-zone-meta {
    font-size: 0.8rem;
    color: var(--text-muted);
    margin-top: 2px;
}

.emotion-zone-stats {
    display: flex;
    align-items: center;
    gap: 16px;
}

.emotion-dominant {
    display: flex;
    align-items: center;
    gap: 8px;
}

.emotion-dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
}

.emotion-name {
    font-size: 0.85rem;
    font-weight: 500;
    color: var(--text-secondary);
    text-transform: capitalize;
}

.emotion-score {
    font-size: 0.9rem;
    font-weight: 700;
    color: var(--text-primary);
    font-variant-numeric: tabular-nums;
    min-width: 50px;
    text-align: right;
}
`;

const styleSheet = document.createElement('style');
styleSheet.textContent = emotionStyles;
document.head.appendChild(styleSheet);
