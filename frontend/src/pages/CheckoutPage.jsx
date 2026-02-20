/**
 * OmniTrack AI — Checkout Analytics Page
 */
import React from 'react';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, LineChart, Line, Legend } from 'recharts';
import { ShoppingCart, Clock, TrendingUp, Users } from 'lucide-react';

const lanes = [
    { id: 'Lane 1', queue: 3, serviceTime: 142, throughput: 38, wait: 285, status: 'active' },
    { id: 'Lane 2', queue: 5, serviceTime: 180, throughput: 32, wait: 420, status: 'active' },
    { id: 'Lane 3', queue: 7, serviceTime: 156, throughput: 28, wait: 588, status: 'busy' },
    { id: 'Lane 4', queue: 2, serviceTime: 128, throughput: 42, wait: 168, status: 'active' },
    { id: 'Lane 5', queue: 0, serviceTime: 0, throughput: 0, wait: 0, status: 'closed' },
    { id: 'Lane 6', queue: 4, serviceTime: 165, throughput: 35, wait: 330, status: 'active' },
];

const throughputTrend = Array.from({ length: 13 }, (_, i) => ({
    hour: `${i + 9}:00`,
    throughput: Math.floor(20 + Math.random() * 25 + (i > 4 && i < 9 ? 15 : 0)),
    waitTime: Math.floor(2 + Math.random() * 6 + (i > 4 && i < 9 ? 3 : 0)),
}));

const tooltipStyle = { contentStyle: { background: '#1e293b', border: '1px solid rgba(148,163,184,0.15)', borderRadius: '8px', fontSize: '12px', color: '#f1f5f9' } };
const formatTime = (s) => `${Math.floor(s / 60)}m ${s % 60}s`;

export default function CheckoutPage() {
    return (
        <div>
            <div className="page-header">
                <h2 className="page-title">Checkout Analytics</h2>
                <p className="page-description">Queue monitoring, service time analysis & throughput optimization</p>
            </div>

            <div className="stats-grid" style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}>
                {[
                    { label: 'Active Lanes', value: '5 / 6', icon: ShoppingCart, color: '#6366f1' },
                    { label: 'Avg Wait', value: '2m 58s', icon: Clock, color: '#f59e0b' },
                    { label: 'Served Today', value: '387', icon: Users, color: '#10b981' },
                    { label: 'Avg Throughput', value: '35/hr', icon: TrendingUp, color: '#06b6d4' },
                ].map((s, i) => {
                    const Icon = s.icon;
                    return (
                        <div key={i} className="stat-card animate-in">
                            <div className="stat-card-header">
                                <span className="stat-card-label">{s.label}</span>
                                <div className="stat-card-icon" style={{ background: `${s.color}15`, color: s.color }}><Icon size={16} /></div>
                            </div>
                            <div className="stat-card-value">{s.value}</div>
                        </div>
                    );
                })}
            </div>

            <div className="two-col">
                <div className="card animate-in">
                    <div className="card-header"><span className="card-title">Lane Status</span></div>
                    <div className="card-body">
                        <table className="data-table">
                            <thead><tr><th>Lane</th><th>Queue</th><th>Avg Service</th><th>Throughput</th><th>Est. Wait</th><th>Status</th></tr></thead>
                            <tbody>
                                {lanes.map((l) => (
                                    <tr key={l.id}>
                                        <td style={{ fontWeight: 600 }}>{l.id}</td>
                                        <td>{l.queue} people</td>
                                        <td>{l.serviceTime ? formatTime(l.serviceTime) : '—'}</td>
                                        <td>{l.throughput ? `${l.throughput}/hr` : '—'}</td>
                                        <td>{l.wait ? formatTime(l.wait) : '—'}</td>
                                        <td>
                                            <span className={`badge ${l.status === 'active' ? 'badge-success' : l.status === 'busy' ? 'badge-warning' : 'badge-neutral'}`}>
                                                {l.status}
                                            </span>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>

                <div className="chart-card animate-in">
                    <div className="chart-card-title">Throughput & Wait Time Trend</div>
                    <ResponsiveContainer width="100%" height={300}>
                        <LineChart data={throughputTrend}>
                            <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.08)" />
                            <XAxis dataKey="hour" tick={{ fontSize: 11, fill: '#64748b' }} />
                            <YAxis yAxisId="left" tick={{ fontSize: 11, fill: '#64748b' }} />
                            <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11, fill: '#64748b' }} />
                            <Tooltip {...tooltipStyle} />
                            <Legend wrapperStyle={{ fontSize: 11 }} />
                            <Line yAxisId="left" type="monotone" dataKey="throughput" stroke="#6366f1" strokeWidth={2} dot={false} name="Throughput/hr" />
                            <Line yAxisId="right" type="monotone" dataKey="waitTime" stroke="#f59e0b" strokeWidth={2} dot={false} name="Wait (min)" />
                        </LineChart>
                    </ResponsiveContainer>
                </div>
            </div>
        </div>
    );
}
