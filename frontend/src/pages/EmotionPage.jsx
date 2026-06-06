/**
 * OmniTrack AI — Emotion Recognition (live)
 * Polls /api/emotion/current + /api/emotion/store-sentiment.
 */

import React from 'react';
import { SmilePlus, Heart, Meh, Frown } from 'lucide-react';
import {
    PieChart, Pie, Cell, ResponsiveContainer, Tooltip, Legend,
} from 'recharts';
import { emotionAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import StatCard from '../components/ui/StatCard';
import ContentCard from '../components/ui/ContentCard';
import RecordCard from '../components/ui/RecordCard';
import useGradientColors from '../hooks/useGradientColors';

export default function EmotionPage() {
    const { a, b } = useGradientColors();
    const EMOTION_COLOR = {
        happy: a,
        neutral: '#64748b',
        surprise: '#f97316',
        sad: b,
        angry: '#f43f5e',
        fear: b,
        disgust: a,
    };
    const { data: current } = useLivePoll(() => emotionAPI.current(), { intervalMs: 4000 });
    const { data: sentiment } = useLivePoll(() => emotionAPI.sentiment(), { intervalMs: 10000 });

    const zones = Array.isArray(current) ? current : [];
    const sentimentScore = Number(sentiment?.overall_sentiment ?? sentiment?.sentiment_score ?? 0);

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
                    <p className="page-subtitle">OpenCV face sampling · aggregated sentiment per zone</p>
                </div>
            </div>

            <div className="stats-grid">
                <StatCard
                    icon={Icon}
                    label="Store Sentiment"
                    value={label}
                    accent={sentimentScore > 0 ? 'emerald' : sentimentScore < 0 ? 'rose' : 'coral'}
                />
                <StatCard icon={SmilePlus} label="Sentiment Score" value={sentimentScore.toFixed(2)} accent="teal" />
                <StatCard icon={SmilePlus} label="Zones Sampled" value={zones.length} accent="cyan" />
                <StatCard icon={SmilePlus} label="Total Samples" value={totalSamples === 1 ? 0 : totalSamples} accent="sky" />
            </div>

            <div className="two-col">
                <ContentCard title="Emotion Mix (all zones)" subtitle="% of aggregated samples" accent="rose">
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
                </ContentCard>

                <ContentCard title="Per-Zone" accent="teal">
                    {zones.length === 0 ? (
                        <div className="page-empty-hint page-empty-hint--left">No zones.</div>
                    ) : (
                        <div className="ui-feed-list" style={{ maxHeight: 300, overflow: 'auto' }}>
                            {zones.map((z) => (
                                <RecordCard
                                    key={z.zone}
                                    icon={SmilePlus}
                                    accent="teal"
                                    title={z.zone}
                                    meta={
                                        <>
                                            <span
                                                className="pill"
                                                style={{ color: EMOTION_COLOR[z.dominant_emotion] || '#64748b' }}
                                            >
                                                {z.dominant_emotion}
                                            </span>
                                            <span>
                                                {z.sample_count} samples · sentiment {Number(z.sentiment_score || 0).toFixed(2)}
                                            </span>
                                        </>
                                    }
                                />
                            ))}
                        </div>
                    )}
                </ContentCard>
            </div>
        </div>
    );
}
