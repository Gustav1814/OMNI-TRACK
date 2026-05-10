/**
 * OmniTrack AI — Fire & Smoke (live)
 * Polls /api/fire/alerts and /api/fire/status; highlights active WS alerts.
 */

import React, { useState } from 'react';
import { motion } from 'framer-motion';
import { Flame, AlertTriangle, ShieldAlert } from 'lucide-react';
import { fireAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import useWebSocket from '../hooks/useWebSocket';

export default function FirePage() {
    const { data: alerts } = useLivePoll(() => fireAPI.alerts(), { intervalMs: 5000 });
    const { data: status } = useLivePoll(() => fireAPI.status(), { intervalMs: 10000 });

    const [live, setLive] = useState([]);
    useWebSocket('/ws/live', {
        onType: {
            fire_alert: (d) => setLive((prev) => [{ ...d, ts: Date.now() }, ...prev].slice(0, 20)),
        },
    });

    const list = Array.isArray(alerts) ? alerts : [];
    const recent = list.slice(0, 20);
    const critical = live[0] || null;

    return (
        <div className="page-scroll">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Fire & Smoke Detection</h1>
                    <p className="page-subtitle">
                        {status?.model_loaded ? 'Custom-trained YOLO model active' : 'Model not loaded — running in safe mode'}
                        {status?.is_fire_specific === false && ' · generic model: alerts suppressed'}
                    </p>
                </div>
            </div>

            {critical && (
                <div className="fire-banner">
                    <div className="fire-banner-icon"><AlertTriangle size={22} /></div>
                    <div style={{ flex: 1 }}>
                        <div style={{ fontWeight: 700 }}>
                            ACTIVE {critical.alert_type?.toUpperCase() || 'FIRE'} ALERT
                        </div>
                        <div style={{ fontSize: 12, opacity: 0.85 }}>
                            Camera {critical.camera_id} · {critical.zone || 'unknown'} ·
                            {' '}confidence {Math.round((critical.confidence || 0) * 100)}%
                        </div>
                    </div>
                    <button className="btn btn-secondary btn-xs" onClick={() => setLive([])}>Dismiss</button>
                </div>
            )}

            <div className="stats-grid">
                <Stat icon={Flame} label="Alerts Today" value={list.length} accent={list.length ? 'rose' : 'indigo'} />
                <Stat icon={ShieldAlert} label="Model Loaded" value={status?.model_loaded ? 'yes' : 'no'} accent="amber" />
                <Stat icon={ShieldAlert} label="Fire-Specific" value={status?.is_fire_specific ? 'yes' : 'no'} accent="cyan" />
                <Stat icon={Flame} label="Live (last 5 min)" value={live.length} accent="gold" />
            </div>

            <div className="card">
                <div className="card-header">
                    <h3 className="card-title">Recent Alerts</h3>
                    <div className="card-subtitle">
                        {recent.length
                            ? `${recent.length} of ${list.length} alerts`
                            : 'No alerts — safe.'}
                    </div>
                </div>

                {recent.length === 0 ? (
                    <div className="page-empty-hint page-empty-hint--left">
                        No fire/smoke alerts in the recent history.
                    </div>
                ) : (
                    <div style={{ display: 'grid', gap: 8 }}>
                        {recent.map((a, i) => (
                            <motion.div
                                key={i}
                                className="card"
                                style={{
                                    padding: 12,
                                    borderColor: 'rgba(244,63,94,0.3)',
                                    background: 'rgba(244,63,94,0.06)',
                                }}
                                whileHover={{ x: 3 }}
                            >
                                <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                                    <Flame size={18} color="#f43f5e" />
                                    <strong>{String(a.alert_type || 'fire').toUpperCase()}</strong>
                                    <span className="pill pill-danger">Cam {a.camera_id}</span>
                                    <span className="pill">{a.zone || '—'}</span>
                                    <span className="pill pill-warn">
                                        {Math.round((a.confidence || 0) * 100)}%
                                    </span>
                                    <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--text-muted)' }}>
                                        {a.timestamp ? new Date(a.timestamp).toLocaleString() : ''}
                                    </span>
                                </div>
                            </motion.div>
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
}

function Stat({ icon: Icon, label, value, accent = 'indigo' }) {
    return (
        <motion.div className="stat-card" whileHover={{ y: -2 }}>
            <div className={`stat-icon stat-icon-${accent}`}><Icon size={18} /></div>
            <div className="stat-label">{label}</div>
            <div className="stat-value">{value}</div>
        </motion.div>
    );
}
