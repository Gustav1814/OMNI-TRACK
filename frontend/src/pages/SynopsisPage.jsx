/**
 * OmniTrack AI — Video Synopsis Page
 */
import React from 'react';
import { Video, Clock, Zap, Download } from 'lucide-react';

const synopses = [
    { id: 1, camera: 'Entrance Main', date: '2026-02-20', original: '6h 00m', condensed: '36m', ratio: '10x', status: 'completed', tubes: 142 },
    { id: 2, camera: 'Electronics', date: '2026-02-20', original: '8h 00m', condensed: '32m', ratio: '15x', status: 'completed', tubes: 98 },
    { id: 3, camera: 'Main Floor', date: '2026-02-19', original: '12h 00m', condensed: '1h 12m', ratio: '10x', status: 'completed', tubes: 310 },
    { id: 4, camera: 'Checkout Area', date: '2026-02-21', original: '4h 00m', condensed: '—', ratio: '—', status: 'processing', tubes: 0 },
];

export default function SynopsisPage() {
    return (
        <div>
            <div className="page-header">
                <h2 className="page-title">Video Synopsis</h2>
                <p className="page-description">Condense hours of footage into compact, reviewable summaries</p>
            </div>
            <div className="stats-grid" style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}>
                {[
                    { label: 'Total Synopses', value: '3', color: '#6366f1' },
                    { label: 'Avg Compression', value: '11.7x', color: '#06b6d4' },
                    { label: 'Activity Tubes', value: '550', color: '#10b981' },
                    { label: 'Processing', value: '1', color: '#f59e0b' },
                ].map((s, i) => (
                    <div key={i} className="stat-card animate-in">
                        <span className="stat-card-label">{s.label}</span>
                        <div className="stat-card-value" style={{ color: s.color }}>{s.value}</div>
                    </div>
                ))}
            </div>
            <div className="card animate-in">
                <div className="card-header">
                    <span className="card-title">Synopsis Library</span>
                    <button className="btn btn-primary"><Zap size={14} /> Generate New</button>
                </div>
                <div className="card-body">
                    <table className="data-table">
                        <thead><tr><th>Camera</th><th>Date</th><th>Original</th><th>Condensed</th><th>Ratio</th><th>Tubes</th><th>Status</th><th>Action</th></tr></thead>
                        <tbody>
                            {synopses.map((s) => (
                                <tr key={s.id}>
                                    <td style={{ fontWeight: 600 }}>{s.camera}</td>
                                    <td>{s.date}</td>
                                    <td>{s.original}</td>
                                    <td>{s.condensed}</td>
                                    <td><span className="badge badge-info">{s.ratio}</span></td>
                                    <td>{s.tubes}</td>
                                    <td><span className={`badge ${s.status === 'completed' ? 'badge-success' : 'badge-warning'}`}>{s.status}</span></td>
                                    <td>{s.status === 'completed' && <button className="btn btn-outline" style={{ padding: '4px 10px', fontSize: 11 }}><Download size={12} /> Download</button>}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    );
}
