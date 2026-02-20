/**
 * OmniTrack AI — Peak Hours Page
 */
import React from 'react';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell } from 'recharts';
import { Clock, TrendingUp, Users } from 'lucide-react';

const hourlyData = Array.from({ length: 13 }, (_, i) => {
    const hour = i + 9;
    const count = Math.floor(20 + Math.random() * 30 + (hour >= 12 && hour <= 15 ? 50 : 0) + (hour >= 17 && hour <= 19 ? 35 : 0));
    return {
        hour: `${hour}:00`,
        visitors: count,
        isPeak: count > 70,
        busyZone: ['Main Floor', 'Electronics', 'Food Court', 'Checkout'][Math.floor(Math.random() * 4)],
    };
});

const peakHour = hourlyData.reduce((max, h) => h.visitors > max.visitors ? h : max, hourlyData[0]);
const totalVisitors = hourlyData.reduce((s, h) => s + h.visitors, 0);

const tooltipStyle = { contentStyle: { background: '#1e293b', border: '1px solid rgba(148,163,184,0.15)', borderRadius: '8px', fontSize: '12px', color: '#f1f5f9' } };

export default function PeakHoursPage() {
    return (
        <div>
            <div className="page-header">
                <h2 className="page-title">Peak Hours Analysis</h2>
                <p className="page-description">Identify busiest shopping windows for staffing & inventory optimization</p>
            </div>

            <div className="stats-grid" style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}>
                {[
                    { label: 'Peak Hour', value: peakHour.hour, icon: Clock, color: '#f43f5e' },
                    { label: 'Peak Count', value: peakHour.visitors, icon: TrendingUp, color: '#6366f1' },
                    { label: 'Total Visitors', value: totalVisitors.toLocaleString(), icon: Users, color: '#10b981' },
                    { label: 'Avg / Hour', value: Math.round(totalVisitors / 13), icon: TrendingUp, color: '#06b6d4' },
                ].map((s, i) => {
                    const Icon = s.icon;
                    return (
                        <div key={i} className="stat-card animate-in">
                            <div className="stat-card-header">
                                <span className="stat-card-label">{s.label}</span>
                                <div className="stat-card-icon" style={{ background: `${s.color}15`, color: s.color }}><Icon size={16} /></div>
                            </div>
                            <div className="stat-card-value" style={{ color: s.color }}>{s.value}</div>
                        </div>
                    );
                })}
            </div>

            <div className="chart-card animate-in">
                <div className="chart-card-title">Hourly Visitor Traffic</div>
                <ResponsiveContainer width="100%" height={360}>
                    <BarChart data={hourlyData}>
                        <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.08)" />
                        <XAxis dataKey="hour" tick={{ fontSize: 11, fill: '#64748b' }} />
                        <YAxis tick={{ fontSize: 11, fill: '#64748b' }} />
                        <Tooltip {...tooltipStyle} />
                        <Bar dataKey="visitors" radius={[4, 4, 0, 0]} barSize={28}>
                            {hourlyData.map((h, i) => (
                                <Cell key={i} fill={h.isPeak ? '#f43f5e' : '#6366f1'} />
                            ))}
                        </Bar>
                    </BarChart>
                </ResponsiveContainer>
                <div style={{ display: 'flex', justifyContent: 'center', gap: 20, marginTop: 12 }}>
                    <span style={{ fontSize: 12, color: 'var(--text-secondary)', display: 'flex', alignItems: 'center', gap: 6 }}>
                        <span style={{ width: 12, height: 12, borderRadius: 3, background: '#f43f5e', display: 'inline-block' }} /> Peak Hours
                    </span>
                    <span style={{ fontSize: 12, color: 'var(--text-secondary)', display: 'flex', alignItems: 'center', gap: 6 }}>
                        <span style={{ width: 12, height: 12, borderRadius: 3, background: '#6366f1', display: 'inline-block' }} /> Normal
                    </span>
                </div>
            </div>

            <div className="card animate-in" style={{ marginTop: 20 }}>
                <div className="card-header"><span className="card-title">Hourly Breakdown</span></div>
                <div className="card-body">
                    <table className="data-table">
                        <thead><tr><th>Hour</th><th>Visitors</th><th>Status</th><th>Busiest Zone</th></tr></thead>
                        <tbody>
                            {hourlyData.map((h, i) => (
                                <tr key={i}>
                                    <td style={{ fontWeight: 600 }}>{h.hour}</td>
                                    <td>{h.visitors}</td>
                                    <td><span className={`badge ${h.isPeak ? 'badge-danger' : 'badge-success'}`}>{h.isPeak ? 'Peak' : 'Normal'}</span></td>
                                    <td>{h.busyZone}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    );
}
