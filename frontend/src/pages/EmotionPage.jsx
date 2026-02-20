/**
 * OmniTrack AI — Emotion Recognition Page
 */
import React from 'react';
import {
    BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
    PieChart, Pie, Cell, RadarChart, Radar, PolarGrid, PolarAngleAxis, PolarRadiusAxis,
} from 'recharts';
import { SmilePlus, Frown, Meh } from 'lucide-react';

const zoneEmotions = [
    { zone: 'Entrance', happy: 42, neutral: 30, sad: 8, surprise: 12, angry: 8, sentiment: 0.62 },
    { zone: 'Main Floor', happy: 35, neutral: 38, sad: 10, surprise: 10, angry: 7, sentiment: 0.48 },
    { zone: 'Electronics', happy: 48, neutral: 25, sad: 5, surprise: 18, angry: 4, sentiment: 0.72 },
    { zone: 'Food Court', happy: 52, neutral: 28, sad: 6, surprise: 8, angry: 6, sentiment: 0.68 },
    { zone: 'Checkout', happy: 28, neutral: 35, sad: 15, surprise: 8, angry: 14, sentiment: 0.22 },
];

const pieData = [
    { name: 'Happy', value: 42, color: '#10b981' },
    { name: 'Neutral', value: 31, color: '#64748b' },
    { name: 'Surprise', value: 11, color: '#f59e0b' },
    { name: 'Sad', value: 9, color: '#3b82f6' },
    { name: 'Angry', value: 7, color: '#f43f5e' },
];

const radarData = [
    { emotion: 'Happy', A: 72 },
    { emotion: 'Neutral', A: 48 },
    { emotion: 'Surprise', A: 35 },
    { emotion: 'Sad', A: 18 },
    { emotion: 'Angry', A: 12 },
    { emotion: 'Fear', A: 8 },
];

const tooltipStyle = { contentStyle: { background: '#1e293b', border: '1px solid rgba(148,163,184,0.15)', borderRadius: '8px', fontSize: '12px', color: '#f1f5f9' } };

export default function EmotionPage() {
    return (
        <div>
            <div className="page-header">
                <h2 className="page-title">Emotion Recognition</h2>
                <p className="page-description">DeepFace/FER-based facial emotion & sentiment analysis across zones</p>
            </div>

            <div className="stats-grid" style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}>
                {[
                    { label: 'Overall Sentiment', value: '+0.54', color: '#10b981' },
                    { label: 'Dominant Emotion', value: 'Happy', color: '#6366f1' },
                    { label: 'Faces Analyzed', value: '342', color: '#06b6d4' },
                    { label: 'Zones Covered', value: '5', color: '#f59e0b' },
                ].map((s, i) => (
                    <div key={i} className="stat-card animate-in">
                        <span className="stat-card-label">{s.label}</span>
                        <div className="stat-card-value" style={{ color: s.color, fontSize: s.label === 'Dominant Emotion' ? 20 : 28 }}>{s.value}</div>
                    </div>
                ))}
            </div>

            <div className="two-col">
                <div className="chart-card animate-in">
                    <div className="chart-card-title">Emotion Distribution</div>
                    <ResponsiveContainer width="100%" height={260}>
                        <PieChart>
                            <Pie data={pieData} cx="50%" cy="50%" innerRadius={60} outerRadius={90} paddingAngle={3} dataKey="value">
                                {pieData.map((e, i) => <Cell key={i} fill={e.color} />)}
                            </Pie>
                            <Tooltip {...tooltipStyle} />
                        </PieChart>
                    </ResponsiveContainer>
                    <div style={{ display: 'flex', justifyContent: 'center', gap: 14, flexWrap: 'wrap', marginTop: 8 }}>
                        {pieData.map((e, i) => (
                            <span key={i} style={{ fontSize: 11, color: 'var(--text-secondary)', display: 'flex', alignItems: 'center', gap: 4 }}>
                                <span style={{ width: 8, height: 8, borderRadius: 2, background: e.color, display: 'inline-block' }} /> {e.name} {e.value}%
                            </span>
                        ))}
                    </div>
                </div>

                <div className="chart-card animate-in">
                    <div className="chart-card-title">Emotion Radar</div>
                    <ResponsiveContainer width="100%" height={300}>
                        <RadarChart data={radarData}>
                            <PolarGrid stroke="rgba(148,163,184,0.15)" />
                            <PolarAngleAxis dataKey="emotion" tick={{ fontSize: 11, fill: '#94a3b8' }} />
                            <PolarRadiusAxis tick={{ fontSize: 10, fill: '#64748b' }} />
                            <Radar name="Intensity" dataKey="A" stroke="#6366f1" fill="#6366f1" fillOpacity={0.2} strokeWidth={2} />
                        </RadarChart>
                    </ResponsiveContainer>
                </div>
            </div>

            <div className="card animate-in">
                <div className="card-header"><span className="card-title">Zone-Level Sentiment</span></div>
                <div className="card-body">
                    <table className="data-table">
                        <thead><tr><th>Zone</th><th>Happy</th><th>Neutral</th><th>Sad</th><th>Surprise</th><th>Angry</th><th>Sentiment</th></tr></thead>
                        <tbody>
                            {zoneEmotions.map((z) => (
                                <tr key={z.zone}>
                                    <td style={{ fontWeight: 600 }}>{z.zone}</td>
                                    <td style={{ color: '#10b981' }}>{z.happy}%</td>
                                    <td style={{ color: '#64748b' }}>{z.neutral}%</td>
                                    <td style={{ color: '#3b82f6' }}>{z.sad}%</td>
                                    <td style={{ color: '#f59e0b' }}>{z.surprise}%</td>
                                    <td style={{ color: '#f43f5e' }}>{z.angry}%</td>
                                    <td>
                                        <span className={`badge ${z.sentiment > 0.5 ? 'badge-success' : z.sentiment > 0.2 ? 'badge-warning' : 'badge-danger'}`}>
                                            {z.sentiment > 0 ? '+' : ''}{z.sentiment.toFixed(2)}
                                        </span>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    );
}
