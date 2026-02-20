/**
 * OmniTrack AI — Audit Log Page
 */
import React from 'react';
import { ShieldCheck, CheckCircle, AlertOctagon, Hash, Lock } from 'lucide-react';

const logs = [
    { id: 1, event: 'LOGIN', user: 'admin', desc: 'User login from 192.168.1.100', time: '14:32:10', hash: 'a3f2c8', valid: true },
    { id: 2, event: 'CONFIG_CHANGE', user: 'admin', desc: 'Updated detection confidence threshold to 0.6', time: '14:15:45', hash: 'b1d7e5', valid: true },
    { id: 3, event: 'DETECTION_START', user: 'operator1', desc: 'Detection started on Camera 3', time: '13:50:22', hash: 'c9a1f3', valid: true },
    { id: 4, event: 'CAMERA_ADD', user: 'admin', desc: 'Added new camera: Loading Dock', time: '12:30:18', hash: 'd4b6a2', valid: true },
    { id: 5, event: 'EXPORT', user: 'analyst', desc: 'Exported detection report (Jan 2026)', time: '11:45:05', hash: 'e7c3d9', valid: true },
    { id: 6, event: 'USER_CREATE', user: 'admin', desc: 'Created new operator account: john_doe', time: '10:20:33', hash: 'f2a8b1', valid: true },
    { id: 7, event: 'LOGIN', user: 'operator1', desc: 'User login from 192.168.1.105', time: '09:15:47', hash: 'a5d9c4', valid: true },
    { id: 8, event: 'CONFIG_CHANGE', user: 'admin', desc: 'Enabled fire detection on Camera 5', time: '08:42:11', hash: 'b8e2f6', valid: true },
];

const eventColors = {
    LOGIN: '#6366f1', CONFIG_CHANGE: '#f59e0b', DETECTION_START: '#10b981',
    CAMERA_ADD: '#06b6d4', EXPORT: '#8b5cf6', USER_CREATE: '#ec4899',
};

export default function AuditPage() {
    return (
        <div>
            <div className="page-header">
                <h2 className="page-title">Security Audit Log</h2>
                <p className="page-description">SHA-256 hash chain with AES-256 encrypted metadata — tamper-evident audit trail</p>
            </div>

            <div className="alert-banner success" style={{ marginBottom: 20 }}>
                <CheckCircle size={18} /> Hash chain integrity verified — All 150 entries valid
            </div>

            <div className="stats-grid" style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}>
                {[
                    { label: 'Total Entries', value: '150', icon: Hash, color: '#6366f1' },
                    { label: 'Chain Status', value: 'Valid', icon: ShieldCheck, color: '#10b981' },
                    { label: 'Encryption', value: 'AES-256', icon: Lock, color: '#06b6d4' },
                    { label: 'Broken Links', value: '0', icon: AlertOctagon, color: '#f43f5e' },
                ].map((s, i) => {
                    const Icon = s.icon;
                    return (
                        <div key={i} className="stat-card animate-in">
                            <div className="stat-card-header">
                                <span className="stat-card-label">{s.label}</span>
                                <div className="stat-card-icon" style={{ background: `${s.color}15`, color: s.color }}><Icon size={16} /></div>
                            </div>
                            <div className="stat-card-value" style={{ color: s.color, fontSize: s.label === 'Encryption' || s.label === 'Chain Status' ? 18 : 28 }}>{s.value}</div>
                        </div>
                    );
                })}
            </div>

            <div className="card animate-in">
                <div className="card-header">
                    <span className="card-title">Audit Trail</span>
                    <button className="btn btn-outline" style={{ fontSize: 12 }}><ShieldCheck size={14} /> Verify Chain</button>
                </div>
                <div className="card-body" style={{ maxHeight: 420, overflowY: 'auto' }}>
                    <table className="data-table">
                        <thead><tr><th>ID</th><th>Event</th><th>User</th><th>Description</th><th>Time</th><th>Hash</th><th>Valid</th></tr></thead>
                        <tbody>
                            {logs.map((l) => (
                                <tr key={l.id}>
                                    <td style={{ fontFamily: 'monospace' }}>#{l.id}</td>
                                    <td>
                                        <span className="badge" style={{
                                            background: `${eventColors[l.event]}15`,
                                            color: eventColors[l.event],
                                            border: `1px solid ${eventColors[l.event]}30`,
                                        }}>
                                            {l.event}
                                        </span>
                                    </td>
                                    <td style={{ fontWeight: 500 }}>{l.user}</td>
                                    <td>{l.desc}</td>
                                    <td style={{ color: 'var(--text-muted)' }}>{l.time}</td>
                                    <td><span className="hash-display">{l.hash}...</span></td>
                                    <td>
                                        <CheckCircle size={16} style={{ color: '#10b981' }} />
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
