/**
 * OmniTrack AI — Fire & Smoke Detection Page
 */
import React from 'react';
import { Flame, Shield, AlertTriangle, CheckCircle } from 'lucide-react';

const alerts = [
    { id: 1, type: 'smoke', confidence: 0.94, camera: 'Warehouse Cam', zone: 'Warehouse', time: '2h ago', status: 'resolved' },
    { id: 2, type: 'fire', confidence: 0.97, camera: 'Kitchen Cam', zone: 'Kitchen', time: '5h ago', status: 'false_alarm' },
    { id: 3, type: 'smoke', confidence: 0.78, camera: 'Loading Dock', zone: 'Loading Dock', time: '1d ago', status: 'resolved' },
    { id: 4, type: 'fire', confidence: 0.89, camera: 'Storage A', zone: 'Storage', time: '2d ago', status: 'resolved' },
];

export default function FirePage() {
    return (
        <div>
            <div className="page-header">
                <h2 className="page-title">Fire & Smoke Detection</h2>
                <p className="page-description">Real-time hazard monitoring with custom YOLOv8 fire/smoke model</p>
            </div>

            <div className="alert-banner success" style={{ marginBottom: 20 }}>
                <CheckCircle size={18} /> All clear — No active fire or smoke alerts
            </div>

            <div className="stats-grid" style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}>
                {[
                    { label: 'Active Alerts', value: '0', icon: Flame, color: '#10b981' },
                    { label: 'Total Today', value: '2', icon: AlertTriangle, color: '#f59e0b' },
                    { label: 'Cameras Monitored', value: '6', icon: Shield, color: '#6366f1' },
                    { label: 'False Alarm Rate', value: '12%', icon: CheckCircle, color: '#06b6d4' },
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

            <div className="card animate-in">
                <div className="card-header">
                    <span className="card-title">Alert History</span>
                    <span className="badge badge-neutral">{alerts.length} total</span>
                </div>
                <div className="card-body">
                    <table className="data-table">
                        <thead><tr><th>Type</th><th>Confidence</th><th>Camera</th><th>Zone</th><th>Time</th><th>Status</th></tr></thead>
                        <tbody>
                            {alerts.map((a) => (
                                <tr key={a.id}>
                                    <td>
                                        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                                            <Flame size={14} style={{ color: a.type === 'fire' ? '#f43f5e' : '#f59e0b' }} />
                                            <span style={{ fontWeight: 600, textTransform: 'capitalize' }}>{a.type}</span>
                                        </span>
                                    </td>
                                    <td><span className="badge badge-warning">{(a.confidence * 100).toFixed(0)}%</span></td>
                                    <td>{a.camera}</td>
                                    <td>{a.zone}</td>
                                    <td style={{ color: 'var(--text-muted)' }}>{a.time}</td>
                                    <td>
                                        <span className={`badge ${a.status === 'resolved' ? 'badge-success' : a.status === 'false_alarm' ? 'badge-neutral' : 'badge-danger'}`}>
                                            {a.status.replace('_', ' ')}
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
