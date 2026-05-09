/**
 * OmniTrack AI — Crowd Density (live)
 * Polls /api/crowd/status and listens for WS crowd_alert events.
 */

import React, { useState } from 'react';
import { motion } from 'framer-motion';
import { UsersRound, AlertTriangle } from 'lucide-react';
import {
    BarChart, Bar, ResponsiveContainer, XAxis, YAxis, Tooltip, CartesianGrid, Cell,
} from 'recharts';
import { crowdAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import useWebSocket from '../hooks/useWebSocket';

const LEVEL_COLOR = {
    low: '#10b981',
    medium: '#fbbf24',
    high: '#f97316',
    critical: '#f43f5e',
};

export default function CrowdPage() {
    const { data, error } = useLivePoll(() => crowdAPI.status(), { intervalMs: 15000 });
    const [alerts, setAlerts] = useState([]);
    useWebSocket('/ws/live', {
        onType: {
            crowd_alert: (d) => setAlerts((prev) => [{ ...d, ts: Date.now() }, ...prev].slice(0, 20)),
        },
    });

    const zones = Array.isArray(data) ? data : [];
    const chartData = zones.map((z) => ({ name: z.zone, count: z.person_count, level: z.classification }));

    return (
        <div className="page-scroll">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Crowd Density</h1>
                    <p className="page-subtitle">Live person counts + classification per zone</p>
                </div>
            </div>

            {error && <div className="alert-banner danger">Unable to reach crowd analytics.</div>}
            {alerts.length > 0 && (
                <div className="alert-banner danger">
                    <AlertTriangle size={14} style={{ verticalAlign: -2 }} />{' '}
                    <strong>{alerts[0].zone}</strong> is {alerts[0].density_level} ({alerts[0].person_count} people)
                </div>
            )}

            <div className="stats-grid">
                <Stat label="Zones Monitored" value={zones.length} accent="indigo" />
                <Stat label="People (all zones)" value={zones.reduce((a, z) => a + (z.person_count || 0), 0)} accent="cyan" />
                <Stat
                    label="High / Critical"
                    value={zones.filter((z) => ['high', 'critical'].includes(z.classification)).length}
                    accent="rose"
                />
                <Stat
                    label="Avg Density"
                    value={zones.length ? (zones.reduce((a, z) => a + (z.density || 0), 0) / zones.length).toFixed(2) : 0}
                    suffix=" /m²"
                    accent="amber"
                />
            </div>

            <div className="card">
                <div className="card-header">
                    <h3 className="card-title">Zone Density</h3>
                    <div className="card-subtitle">Color-coded by classification</div>
                </div>
                {chartData.length === 0 ? (
                    <div className="page-empty-hint">
                        No crowd data — configure zones and start the pipeline.
                    </div>
                ) : (
                    <div style={{ height: 320 }}>
                        <ResponsiveContainer>
                            <BarChart data={chartData}>
                                <CartesianGrid stroke="rgba(255,255,255,0.05)" />
                                <XAxis dataKey="name" stroke="#71717a" fontSize={11} />
                                <YAxis stroke="#71717a" fontSize={11} />
                                <Tooltip contentStyle={{ background: '#111', border: '1px solid #222' }} />
                                <Bar dataKey="count" radius={[6, 6, 0, 0]}>
                                    {chartData.map((d, i) => (
                                        <Cell key={i} fill={LEVEL_COLOR[d.level] || '#6366f1'} />
                                    ))}
                                </Bar>
                            </BarChart>
                        </ResponsiveContainer>
                    </div>
                )}
            </div>

            <div className="card" style={{ marginTop: 18 }}>
                <div className="card-header">
                    <h3 className="card-title">Zones</h3>
                </div>
                <div style={{ display: 'grid', gap: 6 }}>
                    {zones.map((z) => (
                        <div
                            key={`${z.camera_id}-${z.zone}`}
                            style={{
                                display: 'grid',
                                gridTemplateColumns: '1fr 80px 100px 110px 90px',
                                gap: 10, alignItems: 'center',
                                padding: '10px 12px',
                                background: 'var(--bg-glass)',
                                border: '1px solid var(--border)', borderRadius: 10,
                            }}
                        >
                            <div>
                                <div style={{ fontWeight: 600 }}>{z.zone}</div>
                                <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>cam {z.camera_id}</div>
                            </div>
                            <span style={{ fontSize: 13 }}>{z.person_count} ppl</span>
                            <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>{(z.density || 0).toFixed(2)} /m²</span>
                            <span className={`pill pill-${z.classification === 'critical' || z.classification === 'high' ? 'danger' : z.classification === 'medium' ? 'warn' : 'success'}`}>
                                {z.classification}
                            </span>
                            <span style={{ fontSize: 11, color: 'var(--text-muted)', textAlign: 'right' }}>
                                thr {z.threshold}
                            </span>
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
}

function Stat({ label, value, suffix = '', accent = 'indigo' }) {
    return (
        <motion.div className="stat-card" whileHover={{ y: -2 }}>
            <div className={`stat-icon stat-icon-${accent}`}><UsersRound size={18} /></div>
            <div className="stat-label">{label}</div>
            <div className="stat-value">{value}{suffix}</div>
        </motion.div>
    );
}
