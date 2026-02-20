/**
 * OmniTrack AI — Re-Identification Page
 */

import React from 'react';
import { Users, Search, MapPin, Clock } from 'lucide-react';

const journeySteps = [
    { zone: 'Entrance', camera: 'Cam 1', time: '14:02:15', duration: '2m', status: 'completed' },
    { zone: 'Main Floor', camera: 'Cam 2', time: '14:04:22', duration: '5m', status: 'completed' },
    { zone: 'Electronics', camera: 'Cam 3', time: '14:09:45', duration: '12m', status: 'completed' },
    { zone: 'Aisle B', camera: 'Cam 2', time: '14:21:30', duration: '3m', status: 'completed' },
    { zone: 'Checkout', camera: 'Cam 4', time: '14:24:55', duration: '5m', status: 'active' },
];

const activePersons = Array.from({ length: 8 }, (_, i) => ({
    id: `PERSON-${String(i + 1).padStart(4, '0')}`,
    camera: `Cam ${(i % 4) + 1}`,
    confidence: (0.88 + (i % 3) * 0.04).toFixed(2),
    zone: ['Entrance', 'Main Floor', 'Electronics', 'Food Court', 'Checkout', 'Clothing', 'Aisle A', 'Sports'][i],
    duration: `${Math.floor(Math.random() * 45 + 5)}m`,
}));

export default function ReIDPage() {
    return (
        <div>
            <div className="page-header">
                <h2 className="page-title">Person Re-Identification</h2>
                <p className="page-description">Cross-camera identity matching using 512-d Torchreid embeddings</p>
            </div>

            <div className="stats-grid" style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}>
                {[
                    { label: 'Active Tracks', value: '8', color: '#6366f1' },
                    { label: 'Gallery Size', value: '1,247', color: '#06b6d4' },
                    { label: 'Cross-Camera Matches', value: '34', color: '#10b981' },
                    { label: 'Model', value: 'OSNet x1.0', color: '#f59e0b' },
                ].map((s, i) => (
                    <div key={i} className="stat-card animate-in">
                        <span className="stat-card-label">{s.label}</span>
                        <div className="stat-card-value" style={{ color: s.color }}>{s.value}</div>
                    </div>
                ))}
            </div>

            <div className="two-col">
                {/* Journey Timeline */}
                <div className="card animate-in">
                    <div className="card-header">
                        <span className="card-title">Customer Journey — PERSON-0003</span>
                        <span className="badge badge-info">Tracking</span>
                    </div>
                    <div className="card-body">
                        {journeySteps.map((step, i) => (
                            <div key={i} style={{
                                display: 'flex', alignItems: 'flex-start', gap: 12, marginBottom: 16,
                                paddingLeft: 20, position: 'relative',
                            }}>
                                <div style={{
                                    position: 'absolute', left: 0, top: 4,
                                    width: 10, height: 10, borderRadius: '50%',
                                    background: step.status === 'active' ? '#10b981' : '#6366f1',
                                    boxShadow: step.status === 'active' ? '0 0 8px rgba(16,185,129,0.5)' : 'none',
                                }} />
                                {i < journeySteps.length - 1 && (
                                    <div style={{
                                        position: 'absolute', left: 4, top: 16, width: 2, height: 'calc(100% + 4px)',
                                        background: 'var(--border)',
                                    }} />
                                )}
                                <div style={{ flex: 1 }}>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 2 }}>
                                        <span style={{ fontWeight: 600, fontSize: 13, color: 'var(--text-primary)' }}>{step.zone}</span>
                                        <span className={`badge ${step.status === 'active' ? 'badge-success' : 'badge-neutral'}`} style={{ fontSize: 10 }}>{step.status}</span>
                                    </div>
                                    <div style={{ fontSize: 12, color: 'var(--text-muted)', display: 'flex', gap: 12 }}>
                                        <span>{step.camera}</span>
                                        <span>{step.time}</span>
                                        <span>{step.duration}</span>
                                    </div>
                                </div>
                            </div>
                        ))}
                    </div>
                </div>

                {/* Active Persons */}
                <div className="card animate-in">
                    <div className="card-header">
                        <span className="card-title">Active Persons in Store</span>
                        <span className="badge badge-info">{activePersons.length} tracked</span>
                    </div>
                    <div className="card-body" style={{ maxHeight: 380, overflowY: 'auto' }}>
                        <table className="data-table">
                            <thead>
                                <tr>
                                    <th>ID</th>
                                    <th>Zone</th>
                                    <th>Camera</th>
                                    <th>Confidence</th>
                                    <th>Duration</th>
                                </tr>
                            </thead>
                            <tbody>
                                {activePersons.map((p) => (
                                    <tr key={p.id}>
                                        <td style={{ fontFamily: 'monospace', fontSize: 12, color: 'var(--accent-primary)' }}>{p.id}</td>
                                        <td>{p.zone}</td>
                                        <td>{p.camera}</td>
                                        <td><span className="badge badge-success">{p.confidence}</span></td>
                                        <td>{p.duration}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>
    );
}
