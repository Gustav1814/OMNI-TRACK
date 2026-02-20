/**
 * OmniTrack AI — Dashboard Overview Page
 */

import React from 'react';
import {
    BarChart, Bar, LineChart, Line, PieChart, Pie, Cell,
    XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Area, AreaChart
} from 'recharts';
import { Camera, Users, Scan, Flame, ShoppingCart, Activity, TrendingUp, Clock } from 'lucide-react';

const stats = [
    { label: 'Active Cameras', value: '6 / 8', icon: Camera, color: '#6366f1', change: '+1 today' },
    { label: 'Current Occupancy', value: '87', icon: Users, color: '#06b6d4', change: '+12%' },
    { label: 'Detections Today', value: '3,428', icon: Scan, color: '#10b981', change: '+8%' },
    { label: 'Fire Alerts', value: '0', icon: Flame, color: '#f43f5e', change: 'All clear' },
    { label: 'Avg Wait Time', value: '2m 45s', icon: ShoppingCart, color: '#f59e0b', change: '-15%' },
    { label: 'Peak Hour', value: '2 PM', icon: Clock, color: '#8b5cf6', change: '142 visitors' },
];

const trafficData = Array.from({ length: 13 }, (_, i) => ({
    hour: `${i + 9}:00`,
    visitors: Math.floor(30 + Math.random() * 80 + (i > 3 && i < 9 ? 40 : 0)),
    avgDwell: Math.floor(200 + Math.random() * 400),
}));

const zoneData = [
    { name: 'Electronics', value: 28 },
    { name: 'Groceries', value: 22 },
    { name: 'Clothing', value: 18 },
    { name: 'Home', value: 15 },
    { name: 'Other', value: 17 },
];
const COLORS = ['#6366f1', '#06b6d4', '#10b981', '#f59e0b', '#8b5cf6'];

const emotionData = [
    { emotion: 'Happy', count: 42, fill: '#10b981' },
    { emotion: 'Neutral', count: 35, fill: '#64748b' },
    { emotion: 'Surprised', count: 12, fill: '#f59e0b' },
    { emotion: 'Sad', count: 6, fill: '#3b82f6' },
    { emotion: 'Angry', count: 5, fill: '#f43f5e' },
];

const chartTooltipStyle = {
    contentStyle: {
        background: '#1e293b',
        border: '1px solid rgba(148,163,184,0.15)',
        borderRadius: '8px',
        fontSize: '12px',
        color: '#f1f5f9',
    },
};

export default function DashboardPage() {
    return (
        <div>
            {/* Stat Cards */}
            <div className="stats-grid">
                {stats.map((s, i) => {
                    const Icon = s.icon;
                    return (
                        <div key={i} className="stat-card animate-in">
                            <div className="stat-card-header">
                                <span className="stat-card-label">{s.label}</span>
                                <div className="stat-card-icon" style={{ background: `${s.color}15`, color: s.color }}>
                                    <Icon size={18} />
                                </div>
                            </div>
                            <div className="stat-card-value">{s.value}</div>
                            <div className="stat-card-change positive">{s.change}</div>
                        </div>
                    );
                })}
            </div>

            {/* Vibe Score + Traffic Chart */}
            <div className="two-col">
                <div className="chart-card animate-in">
                    <div className="chart-card-title">Store Vibe Score</div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 30, padding: '10px 0' }}>
                        <div className="vibe-meter">
                            <div className="vibe-meter-ring">
                                <div className="vibe-meter-inner">
                                    <div className="vibe-score-value">72</div>
                                    <div className="vibe-score-label">Energetic</div>
                                </div>
                            </div>
                        </div>
                        <div style={{ flex: 1 }}>
                            {[
                                { label: 'Sentiment', value: 68, color: '#6366f1' },
                                { label: 'Energy', value: 75, color: '#06b6d4' },
                                { label: 'Engagement', value: 62, color: '#10b981' },
                                { label: 'Foot Traffic', value: 80, color: '#f59e0b' },
                            ].map((m) => (
                                <div key={m.label} style={{ marginBottom: 12 }}>
                                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 4 }}>
                                        <span style={{ color: 'var(--text-secondary)' }}>{m.label}</span>
                                        <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{m.value}%</span>
                                    </div>
                                    <div className="progress-bar">
                                        <div className="progress-bar-fill" style={{ width: `${m.value}%`, background: m.color }} />
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>
                </div>

                <div className="chart-card animate-in">
                    <div className="chart-card-title">Foot Traffic Today</div>
                    <ResponsiveContainer width="100%" height={240}>
                        <AreaChart data={trafficData}>
                            <defs>
                                <linearGradient id="colorVisitors" x1="0" y1="0" x2="0" y2="1">
                                    <stop offset="5%" stopColor="#6366f1" stopOpacity={0.3} />
                                    <stop offset="95%" stopColor="#6366f1" stopOpacity={0} />
                                </linearGradient>
                            </defs>
                            <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.08)" />
                            <XAxis dataKey="hour" tick={{ fontSize: 11, fill: '#64748b' }} />
                            <YAxis tick={{ fontSize: 11, fill: '#64748b' }} />
                            <Tooltip {...chartTooltipStyle} />
                            <Area type="monotone" dataKey="visitors" stroke="#6366f1" fillOpacity={1} fill="url(#colorVisitors)" strokeWidth={2} />
                        </AreaChart>
                    </ResponsiveContainer>
                </div>
            </div>

            {/* Zone Distribution + Emotions */}
            <div className="two-col">
                <div className="chart-card animate-in">
                    <div className="chart-card-title">Zone Distribution</div>
                    <ResponsiveContainer width="100%" height={240}>
                        <PieChart>
                            <Pie data={zoneData} cx="50%" cy="50%" innerRadius={65} outerRadius={95} paddingAngle={3} dataKey="value">
                                {zoneData.map((_, i) => <Cell key={i} fill={COLORS[i]} />)}
                            </Pie>
                            <Tooltip {...chartTooltipStyle} />
                        </PieChart>
                    </ResponsiveContainer>
                    <div style={{ display: 'flex', justifyContent: 'center', gap: 16, flexWrap: 'wrap' }}>
                        {zoneData.map((z, i) => (
                            <span key={i} style={{ fontSize: 11, color: 'var(--text-secondary)', display: 'flex', alignItems: 'center', gap: 4 }}>
                                <span style={{ width: 8, height: 8, borderRadius: 2, background: COLORS[i], display: 'inline-block' }} /> {z.name}
                            </span>
                        ))}
                    </div>
                </div>

                <div className="chart-card animate-in">
                    <div className="chart-card-title">Customer Emotions</div>
                    <ResponsiveContainer width="100%" height={260}>
                        <BarChart data={emotionData} layout="vertical">
                            <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.08)" />
                            <XAxis type="number" tick={{ fontSize: 11, fill: '#64748b' }} />
                            <YAxis dataKey="emotion" type="category" tick={{ fontSize: 12, fill: '#94a3b8' }} width={80} />
                            <Tooltip {...chartTooltipStyle} />
                            <Bar dataKey="count" radius={[0, 4, 4, 0]} barSize={18}>
                                {emotionData.map((e, i) => <Cell key={i} fill={e.fill} />)}
                            </Bar>
                        </BarChart>
                    </ResponsiveContainer>
                </div>
            </div>
        </div>
    );
}
