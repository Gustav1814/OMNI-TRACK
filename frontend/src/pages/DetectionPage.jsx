/**
 * OmniTrack AI — Detection Page
 */

import React from 'react';
import { Camera, Play, Square, Wifi } from 'lucide-react';

const cameras = [
    { id: 1, name: 'Entrance Main', status: 'active', fps: 28.5, detections: 12, zone: 'Entrance' },
    { id: 2, name: 'Aisle A', status: 'active', fps: 30.0, detections: 8, zone: 'Main Floor' },
    { id: 3, name: 'Electronics', status: 'active', fps: 29.2, detections: 15, zone: 'Electronics' },
    { id: 4, name: 'Checkout Area', status: 'active', fps: 30.0, detections: 22, zone: 'Checkout' },
    { id: 5, name: 'Parking Lot', status: 'inactive', fps: 0, detections: 0, zone: 'Exterior' },
    { id: 6, name: 'Storage Room', status: 'active', fps: 25.8, detections: 3, zone: 'Back Office' },
];

export default function DetectionPage() {
    return (
        <div>
            <div className="page-header">
                <h2 className="page-title">Live Detection Feed</h2>
                <p className="page-description">Real-time YOLOv8 person detection across all cameras</p>
            </div>

            <div className="stats-grid" style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}>
                {[
                    { label: 'Active Feeds', value: '5', color: '#10b981' },
                    { label: 'Total Detections', value: '60', color: '#6366f1' },
                    { label: 'Avg FPS', value: '28.7', color: '#06b6d4' },
                    { label: 'Model', value: 'YOLOv8n', color: '#f59e0b' },
                ].map((s, i) => (
                    <div key={i} className="stat-card animate-in">
                        <span className="stat-card-label">{s.label}</span>
                        <div className="stat-card-value" style={{ color: s.color }}>{s.value}</div>
                    </div>
                ))}
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(350px, 1fr))', gap: 16 }}>
                {cameras.map((cam) => (
                    <div key={cam.id} className="card animate-in">
                        <div className="card-header">
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                <Camera size={16} />
                                <span className="card-title">{cam.name}</span>
                            </div>
                            <span className={`badge ${cam.status === 'active' ? 'badge-success' : 'badge-neutral'}`}>
                                {cam.status}
                            </span>
                        </div>
                        <div className="card-body">
                            {/* Camera feed placeholder */}
                            <div style={{
                                height: 200, borderRadius: 8,
                                background: cam.status === 'active'
                                    ? 'linear-gradient(135deg, #0f172a 0%, #1e293b 100%)'
                                    : '#0f172a',
                                display: 'flex', alignItems: 'center', justifyContent: 'center',
                                border: '1px solid var(--border)', marginBottom: 12,
                                position: 'relative', overflow: 'hidden',
                            }}>
                                {cam.status === 'active' ? (
                                    <>
                                        <div style={{ position: 'absolute', top: 8, left: 8, fontSize: 10, color: '#f43f5e', display: 'flex', alignItems: 'center', gap: 4, fontWeight: 600 }}>
                                            <span style={{ width: 6, height: 6, background: '#f43f5e', borderRadius: '50%' }} /> REC
                                        </div>
                                        <div style={{ position: 'absolute', top: 8, right: 8, fontSize: 10, color: 'var(--text-muted)' }}>{cam.fps} FPS</div>
                                        <Wifi size={32} style={{ color: 'var(--text-muted)', opacity: 0.3 }} />
                                        {/* Detection bbox overlay mockups */}
                                        {Array.from({ length: Math.min(cam.detections, 3) }).map((_, i) => (
                                            <div key={i} style={{
                                                position: 'absolute',
                                                left: `${20 + i * 25}%`, top: '25%',
                                                width: 50, height: 90,
                                                border: '2px solid #10b981',
                                                borderRadius: 4,
                                                opacity: 0.7,
                                            }}>
                                                <span style={{ position: 'absolute', top: -14, left: 0, fontSize: 9, background: '#10b981', color: '#fff', padding: '1px 4px', borderRadius: 2 }}>
                                                    P{i + 1} {(0.85 + (i * 0.04)).toFixed(0)}%
                                                </span>
                                            </div>
                                        ))}
                                    </>
                                ) : (
                                    <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>Camera Offline</span>
                                )}
                            </div>

                            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: 'var(--text-secondary)' }}>
                                <span>Zone: {cam.zone}</span>
                                <span>{cam.detections} persons detected</span>
                            </div>
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
}
