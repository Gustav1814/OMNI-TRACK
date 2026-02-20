/**
 * OmniTrack AI — Crowd Density Page
 */
import React from 'react';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell } from 'recharts';
import { UsersRound, AlertTriangle, TrendingUp } from 'lucide-react';

const zoneData = [
    { zone: 'Entrance', count: 12, max: 30, level: 'medium' },
    { zone: 'Main Floor', count: 45, max: 60, level: 'high' },
    { zone: 'Food Court', count: 8, max: 40, level: 'low' },
    { zone: 'Electronics', count: 22, max: 35, level: 'medium' },
    { zone: 'Checkout', count: 35, max: 40, level: 'high' },
    { zone: 'Parking', count: 5, max: 50, level: 'low' },
];

const levelColors = { low: '#10b981', medium: '#f59e0b', high: '#f43f5e', critical: '#dc2626' };

const hourlyData = Array.from({ length: 13 }, (_, i) => ({
    hour: `${i + 9}:00`,
    count: Math.floor(20 + Math.random() * 40 + (i > 3 && i < 8 ? 25 : 0)),
}));

const tooltipStyle = { contentStyle: { background: '#1e293b', border: '1px solid rgba(148,163,184,0.15)', borderRadius: '8px', fontSize: '12px', color: '#f1f5f9' } };

export default function CrowdPage() {
    const totalOccupancy = zoneData.reduce((s, z) => s + z.count, 0);
    return (
        <div>
            <div className="page-header">
                <h2 className="page-title">Crowd Density Monitoring</h2>
                <p className="page-description">Real-time zone-based occupancy and density classification</p>
            </div>

            <div className="stats-grid" style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}>
                {[
                    { label: 'Total Occupancy', value: totalOccupancy, color: '#6366f1' },
                    { label: 'Zones Monitored', value: '6', color: '#06b6d4' },
                    { label: 'High Density Zones', value: zoneData.filter(z => z.level === 'high').length, color: '#f43f5e' },
                    { label: 'Avg Density', value: `${Math.round(totalOccupancy / zoneData.length)}`, color: '#10b981' },
                ].map((s, i) => (
                    <div key={i} className="stat-card animate-in">
                        <span className="stat-card-label">{s.label}</span>
                        <div className="stat-card-value" style={{ color: s.color }}>{s.value}</div>
                    </div>
                ))}
            </div>

            <div className="two-col">
                <div className="card animate-in">
                    <div className="card-header"><span className="card-title">Zone Density Map</span></div>
                    <div className="card-body">
                        {zoneData.map((z, i) => (
                            <div key={i} style={{ marginBottom: 16 }}>
                                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4, fontSize: 13 }}>
                                    <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{z.zone}</span>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                        <span style={{ color: 'var(--text-muted)' }}>{z.count} / {z.max}</span>
                                        <span className={`badge ${z.level === 'high' ? 'badge-danger' : z.level === 'medium' ? 'badge-warning' : 'badge-success'}`}>
                                            {z.level}
                                        </span>
                                    </div>
                                </div>
                                <div className="progress-bar" style={{ height: 8 }}>
                                    <div className="progress-bar-fill" style={{
                                        width: `${(z.count / z.max) * 100}%`,
                                        background: levelColors[z.level],
                                    }} />
                                </div>
                            </div>
                        ))}
                    </div>
                </div>

                <div className="chart-card animate-in">
                    <div className="chart-card-title">Hourly Trend</div>
                    <ResponsiveContainer width="100%" height={300}>
                        <BarChart data={hourlyData}>
                            <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.08)" />
                            <XAxis dataKey="hour" tick={{ fontSize: 11, fill: '#64748b' }} />
                            <YAxis tick={{ fontSize: 11, fill: '#64748b' }} />
                            <Tooltip {...tooltipStyle} />
                            <Bar dataKey="count" fill="#6366f1" radius={[4, 4, 0, 0]} barSize={20} />
                        </BarChart>
                    </ResponsiveContainer>
                </div>
            </div>
        </div>
    );
}
