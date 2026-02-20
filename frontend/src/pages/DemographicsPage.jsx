/**
 * OmniTrack AI — Demographics Page
 */
import React from 'react';
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer, BarChart, Bar, XAxis, YAxis, CartesianGrid } from 'recharts';
import { BarChart3, Users } from 'lucide-react';

const ageData = [
    { group: '18-25', count: 35, color: '#6366f1' },
    { group: '26-35', count: 45, color: '#8b5cf6' },
    { group: '36-45', count: 28, color: '#06b6d4' },
    { group: '46-55', count: 18, color: '#10b981' },
    { group: '56+', count: 12, color: '#f59e0b' },
];

const genderData = [
    { name: 'Male', value: 52, color: '#6366f1' },
    { name: 'Female', value: 48, color: '#ec4899' },
];

const zoneDemo = [
    { zone: 'Electronics', m: 62, f: 38, domAge: '26-35' },
    { zone: 'Beauty', m: 28, f: 72, domAge: '18-25' },
    { zone: 'Groceries', m: 45, f: 55, domAge: '36-45' },
    { zone: 'Sports', m: 70, f: 30, domAge: '18-25' },
    { zone: 'Home & Garden', m: 48, f: 52, domAge: '36-45' },
    { zone: 'Clothing', m: 35, f: 65, domAge: '26-35' },
];

const tooltipStyle = { contentStyle: { background: '#1e293b', border: '1px solid rgba(148,163,184,0.15)', borderRadius: '8px', fontSize: '12px', color: '#f1f5f9' } };

export default function DemographicsPage() {
    return (
        <div>
            <div className="page-header">
                <h2 className="page-title">Customer Demographics</h2>
                <p className="page-description">DeepFace-powered age & gender estimation across all zones</p>
            </div>

            <div className="stats-grid" style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}>
                {[
                    { label: 'Total Analyzed', value: '138', color: '#6366f1' },
                    { label: 'Dominant Age', value: '26-35', color: '#8b5cf6' },
                    { label: 'Male %', value: '52%', color: '#06b6d4' },
                    { label: 'Female %', value: '48%', color: '#ec4899' },
                ].map((s, i) => (
                    <div key={i} className="stat-card animate-in">
                        <span className="stat-card-label">{s.label}</span>
                        <div className="stat-card-value" style={{ color: s.color, fontSize: s.label === 'Dominant Age' ? 22 : 28 }}>{s.value}</div>
                    </div>
                ))}
            </div>

            <div className="two-col">
                <div className="chart-card animate-in">
                    <div className="chart-card-title">Age Distribution</div>
                    <ResponsiveContainer width="100%" height={260}>
                        <BarChart data={ageData}>
                            <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.08)" />
                            <XAxis dataKey="group" tick={{ fontSize: 12, fill: '#94a3b8' }} />
                            <YAxis tick={{ fontSize: 11, fill: '#64748b' }} />
                            <Tooltip {...tooltipStyle} />
                            <Bar dataKey="count" radius={[4, 4, 0, 0]} barSize={36}>
                                {ageData.map((a, i) => <Cell key={i} fill={a.color} />)}
                            </Bar>
                        </BarChart>
                    </ResponsiveContainer>
                </div>

                <div className="chart-card animate-in">
                    <div className="chart-card-title">Gender Distribution</div>
                    <ResponsiveContainer width="100%" height={260}>
                        <PieChart>
                            <Pie data={genderData} cx="50%" cy="50%" innerRadius={65} outerRadius={95} paddingAngle={4} dataKey="value">
                                {genderData.map((g, i) => <Cell key={i} fill={g.color} />)}
                            </Pie>
                            <Tooltip {...tooltipStyle} />
                        </PieChart>
                    </ResponsiveContainer>
                    <div style={{ display: 'flex', justifyContent: 'center', gap: 20, marginTop: 8 }}>
                        {genderData.map((g, i) => (
                            <span key={i} style={{ fontSize: 12, color: 'var(--text-secondary)', display: 'flex', alignItems: 'center', gap: 6 }}>
                                <span style={{ width: 10, height: 10, borderRadius: 3, background: g.color, display: 'inline-block' }} /> {g.name}: {g.value}%
                            </span>
                        ))}
                    </div>
                </div>
            </div>

            <div className="card animate-in" style={{ marginTop: 20 }}>
                <div className="card-header"><span className="card-title">Zone Demographics</span></div>
                <div className="card-body">
                    <table className="data-table">
                        <thead><tr><th>Zone</th><th>Male %</th><th>Female %</th><th>Dominant Age</th><th>Gender Skew</th></tr></thead>
                        <tbody>
                            {zoneDemo.map((z) => (
                                <tr key={z.zone}>
                                    <td style={{ fontWeight: 600 }}>{z.zone}</td>
                                    <td style={{ color: '#6366f1' }}>{z.m}%</td>
                                    <td style={{ color: '#ec4899' }}>{z.f}%</td>
                                    <td><span className="badge badge-info">{z.domAge}</span></td>
                                    <td>
                                        <div className="progress-bar" style={{ width: 120 }}>
                                            <div style={{
                                                height: '100%', borderRadius: 3,
                                                width: `${z.m}%`,
                                                background: 'linear-gradient(90deg, #6366f1, #ec4899)',
                                            }} />
                                        </div>
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
