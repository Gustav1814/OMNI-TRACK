/**
 * OmniTrack AI — Store Vibe Page
 */
import React from 'react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, AreaChart, Area } from 'recharts';
import { Activity, Zap, Heart, TrendingUp } from 'lucide-react';

const vibeHistory = Array.from({ length: 24 }, (_, i) => ({
    hour: `${i}:00`,
    score: Math.floor(35 + Math.random() * 40 + (i > 10 && i < 18 ? 15 : 0)),
    sentiment: Math.floor(30 + Math.random() * 35),
    energy: Math.floor(25 + Math.random() * 50),
}));

const zoneVibes = [
    { zone: 'Entrance', score: 68, label: 'Energetic', sentiment: 62, energy: 75, engagement: 58, traffic: 72 },
    { zone: 'Main Floor', score: 72, label: 'Energetic', sentiment: 65, energy: 78, engagement: 70, traffic: 75 },
    { zone: 'Electronics', score: 82, label: 'Buzzing', sentiment: 78, energy: 85, engagement: 80, traffic: 84 },
    { zone: 'Food Court', score: 55, label: 'Steady', sentiment: 52, energy: 45, engagement: 60, traffic: 62 },
    { zone: 'Checkout', score: 45, label: 'Calm', sentiment: 38, energy: 42, engagement: 48, traffic: 52 },
];

const tooltipStyle = { contentStyle: { background: '#1e293b', border: '1px solid rgba(148,163,184,0.15)', borderRadius: '8px', fontSize: '12px', color: '#f1f5f9' } };

export default function VibePage() {
    return (
        <div>
            <div className="page-header">
                <h2 className="page-title">Store Vibe Score</h2>
                <p className="page-description">Composite score from sentiment, crowd energy, shelf engagement & foot traffic</p>
            </div>

            <div className="two-col" style={{ marginBottom: 24 }}>
                {/* Vibe Meter */}
                <div className="chart-card animate-in" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center' }}>
                    <div className="vibe-meter" style={{ width: 200, height: 200 }}>
                        <div className="vibe-meter-ring">
                            <div className="vibe-meter-inner">
                                <div className="vibe-score-value" style={{ fontSize: 48 }}>72</div>
                                <div className="vibe-score-label" style={{ fontSize: 14 }}>Energetic</div>
                            </div>
                        </div>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16, marginTop: 24, width: '100%' }}>
                        {[
                            { label: 'Sentiment', value: 65, icon: Heart, color: '#f43f5e' },
                            { label: 'Energy', value: 75, icon: Zap, color: '#f59e0b' },
                            { label: 'Engagement', value: 62, icon: Activity, color: '#6366f1' },
                            { label: 'Foot Traffic', value: 80, icon: TrendingUp, color: '#10b981' },
                        ].map((m) => {
                            const Icon = m.icon;
                            return (
                                <div key={m.label} style={{ textAlign: 'center' }}>
                                    <Icon size={18} style={{ color: m.color, marginBottom: 4 }} />
                                    <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--text-primary)' }}>{m.value}</div>
                                    <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{m.label}</div>
                                </div>
                            );
                        })}
                    </div>
                </div>

                {/* Trend */}
                <div className="chart-card animate-in">
                    <div className="chart-card-title">24h Vibe Trend</div>
                    <ResponsiveContainer width="100%" height={320}>
                        <AreaChart data={vibeHistory}>
                            <defs>
                                <linearGradient id="vibeGrad" x1="0" y1="0" x2="0" y2="1">
                                    <stop offset="5%" stopColor="#6366f1" stopOpacity={0.3} />
                                    <stop offset="95%" stopColor="#6366f1" stopOpacity={0} />
                                </linearGradient>
                            </defs>
                            <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.08)" />
                            <XAxis dataKey="hour" tick={{ fontSize: 10, fill: '#64748b' }} />
                            <YAxis tick={{ fontSize: 11, fill: '#64748b' }} domain={[0, 100]} />
                            <Tooltip {...tooltipStyle} />
                            <Area type="monotone" dataKey="score" stroke="#6366f1" fill="url(#vibeGrad)" strokeWidth={2} name="Vibe Score" />
                            <Line type="monotone" dataKey="sentiment" stroke="#f43f5e" strokeWidth={1.5} dot={false} name="Sentiment" />
                            <Line type="monotone" dataKey="energy" stroke="#f59e0b" strokeWidth={1.5} dot={false} name="Energy" />
                        </AreaChart>
                    </ResponsiveContainer>
                </div>
            </div>

            {/* Zone Vibes */}
            <div className="card animate-in">
                <div className="card-header"><span className="card-title">Zone Vibe Breakdown</span></div>
                <div className="card-body">
                    <table className="data-table">
                        <thead><tr><th>Zone</th><th>Score</th><th>Label</th><th>Sentiment</th><th>Energy</th><th>Engagement</th><th>Traffic</th></tr></thead>
                        <tbody>
                            {zoneVibes.map((z) => (
                                <tr key={z.zone}>
                                    <td style={{ fontWeight: 600 }}>{z.zone}</td>
                                    <td>
                                        <span style={{ fontSize: 18, fontWeight: 700, background: 'linear-gradient(135deg, #6366f1, #06b6d4)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>
                                            {z.score}
                                        </span>
                                    </td>
                                    <td><span className={`badge ${z.score >= 70 ? 'badge-success' : z.score >= 50 ? 'badge-info' : 'badge-neutral'}`}>{z.label}</span></td>
                                    <td>{z.sentiment}</td>
                                    <td>{z.energy}</td>
                                    <td>{z.engagement}</td>
                                    <td>{z.traffic}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    );
}
