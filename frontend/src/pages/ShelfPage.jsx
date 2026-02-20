/**
 * OmniTrack AI — Shelf Engagement Page
 */
import React from 'react';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell } from 'recharts';
import { ShoppingBag, TrendingUp, Clock, Award } from 'lucide-react';

const engagementData = [
    { zone: 'Electronics', score: 92, visits: 285, dwell: 165, color: '#6366f1' },
    { zone: 'Beauty', score: 87, visits: 210, dwell: 142, color: '#8b5cf6' },
    { zone: 'Groceries', score: 78, visits: 340, dwell: 45, color: '#06b6d4' },
    { zone: 'Sports', score: 72, visits: 156, dwell: 98, color: '#10b981' },
    { zone: 'Home', score: 65, visits: 128, dwell: 76, color: '#f59e0b' },
    { zone: 'Clothing', score: 58, visits: 190, dwell: 55, color: '#f43f5e' },
    { zone: 'Toys', score: 45, visits: 95, dwell: 88, color: '#ec4899' },
    { zone: 'Books', score: 38, visits: 72, dwell: 120, color: '#14b8a6' },
];

const tooltipStyle = { contentStyle: { background: '#1e293b', border: '1px solid rgba(148,163,184,0.15)', borderRadius: '8px', fontSize: '12px', color: '#f1f5f9' } };

export default function ShelfPage() {
    return (
        <div>
            <div className="page-header">
                <h2 className="page-title">Shelf Engagement Analytics</h2>
                <p className="page-description">Zone-based dwell-time tracking and top-selling product area identification</p>
            </div>
            <div className="stats-grid" style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}>
                {[
                    { label: 'Top Zone', value: 'Electronics', icon: Award, color: '#6366f1' },
                    { label: 'Avg Dwell Time', value: '1m 38s', icon: Clock, color: '#06b6d4' },
                    { label: 'Total Visits', value: '1,476', icon: ShoppingBag, color: '#10b981' },
                    { label: 'Engagement Rate', value: '74%', icon: TrendingUp, color: '#f59e0b' },
                ].map((s, i) => {
                    const Icon = s.icon;
                    return (
                        <div key={i} className="stat-card animate-in">
                            <div className="stat-card-header">
                                <span className="stat-card-label">{s.label}</span>
                                <div className="stat-card-icon" style={{ background: `${s.color}15`, color: s.color }}><Icon size={16} /></div>
                            </div>
                            <div className="stat-card-value" style={{ fontSize: s.label === 'Top Zone' ? 20 : 28 }}>{s.value}</div>
                        </div>
                    );
                })}
            </div>
            <div className="two-col">
                <div className="chart-card animate-in">
                    <div className="chart-card-title">Engagement Score by Zone</div>
                    <ResponsiveContainer width="100%" height={300}>
                        <BarChart data={engagementData}>
                            <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.08)" />
                            <XAxis dataKey="zone" tick={{ fontSize: 11, fill: '#64748b' }} />
                            <YAxis tick={{ fontSize: 11, fill: '#64748b' }} />
                            <Tooltip {...tooltipStyle} />
                            <Bar dataKey="score" radius={[4, 4, 0, 0]} barSize={32}>
                                {engagementData.map((e, i) => <Cell key={i} fill={e.color} />)}
                            </Bar>
                        </BarChart>
                    </ResponsiveContainer>
                </div>
                <div className="card animate-in">
                    <div className="card-header"><span className="card-title">Zone Rankings</span></div>
                    <div className="card-body">
                        {engagementData.map((z, i) => (
                            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14 }}>
                                <span style={{ width: 24, height: 24, borderRadius: 6, background: `${z.color}20`, color: z.color, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, fontWeight: 700 }}>
                                    {i + 1}
                                </span>
                                <div style={{ flex: 1 }}>
                                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                                        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>{z.zone}</span>
                                        <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>{z.visits} visits · {Math.floor(z.dwell / 60)}m {z.dwell % 60}s avg</span>
                                    </div>
                                    <div className="progress-bar">
                                        <div className="progress-bar-fill" style={{ width: `${z.score}%`, background: z.color }} />
                                    </div>
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            </div>
        </div>
    );
}
