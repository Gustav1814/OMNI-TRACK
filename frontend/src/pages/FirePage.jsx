/**
 * OmniTrack AI — Fire & Smoke Detection (Enhanced)
 * Real-time fire and smoke alerts with 3D UI
 */

import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Flame, AlertTriangle, ShieldAlert, ShieldCheck, Info } from 'lucide-react';
import { fireAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import useWebSocket from '../hooks/useWebSocket';
import GlassCard, { StatCard, PageHeader, InfoRow } from '../components/GlassCard';

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

    const getModelStatus = () => {
        if (!status?.model_loaded) return { text: 'Not Loaded', accent: 'rose' };
        if (!status?.is_fire_specific) return { text: 'Generic (Suppressed)', accent: 'amber' };
        return { text: 'Active', accent: 'emerald' };
    };

    const modelStatus = getModelStatus();

    return (
        <div className="page-scroll">
            <PageHeader
                title="Fire & Smoke Detection"
                subtitle={status?.model_loaded 
                    ? 'AI-powered fire and smoke detection across all camera feeds'
                    : 'Configure fire detection model to enable real-time alerts'
                }
            />

            <AnimatePresence>
                {critical && (
                    <motion.div
                        className="alert-banner-3d alert-banner-3d-danger"
                        initial={{ opacity: 0, y: -20, scale: 0.95 }}
                        animate={{ opacity: 1, y: 0, scale: 1 }}
                        exit={{ opacity: 0, y: -20, scale: 0.95 }}
                        transition={{ duration: 0.3 }}
                    >
                        <AlertTriangle size={22} />
                        <div style={{ flex: 1 }}>
                            <div style={{ fontWeight: 700, fontSize: '0.95rem' }}>
                                ACTIVE {critical.alert_type?.toUpperCase() || 'FIRE'} ALERT
                            </div>
                            <div style={{ fontSize: '0.85rem', opacity: 0.9, marginTop: 2 }}>
                                Camera {critical.camera_id} · Zone: {critical.zone || 'Unknown'} · 
                                Confidence: {Math.round((critical.confidence || 0) * 100)}%
                            </div>
                        </div>
                        <button 
                            className="btn-3d btn-3d-sm btn-3d-secondary" 
                            onClick={() => setLive([])}
                        >
                            Dismiss
                        </button>
                    </motion.div>
                )}
            </AnimatePresence>

            {/* Stats Grid */}
            <div className="stats-grid-3d">
                <StatCard
                    icon={Flame}
                    label="Alerts Today"
                    value={list.length}
                    accent={list.length ? 'rose' : 'indigo'}
                    trend={live.length > 0 ? 100 : 0}
                    tag={live.length > 0 ? 'Active' : 'Live'}
                />
                <StatCard
                    icon={modelStatus.accent === 'emerald' ? ShieldCheck : ShieldAlert}
                    label="Model Status"
                    value={modelStatus.text}
                    accent={modelStatus.accent}
                    progress={status?.model_loaded ? 100 : 0}
                    tag={status?.confidence_threshold ? `${status.confidence_threshold * 100}%` : 'AI'}
                />
                <StatCard
                    icon={Info}
                    label="Cameras Covered"
                    value={status?.cameras_covered || 0}
                    accent="cyan"
                    tag="Monitored"
                />
                <StatCard
                    icon={Flame}
                    label="Alert History"
                    value={status?.history_size || 0}
                    accent="amber"
                    tag="Stored"
                />
            </div>

            {/* Main Content Grid */}
            <div className="cards-grid-3d cards-grid-3d-2col">
                {/* Recent Alerts */}
                <GlassCard accent="rose" glow className="bento-span-6">
                    <div className="card-header-3d">
                        <div>
                            <h3 className="card-title-3d">Recent Alerts</h3>
                            <p className="card-subtitle-3d">
                                {recent.length 
                                    ? `Showing ${recent.length} recent fire/smoke detection events`
                                    : 'No alerts detected — system operating normally'
                                }
                            </p>
                        </div>
                        <span className="pill-3d pill-3d-info">
                            {list.length} Total
                        </span>
                    </div>
                    
                    {recent.length === 0 ? (
                        <div className="empty-state-3d">
                            <div className="empty-state-icon">
                                <ShieldCheck size={28} />
                            </div>
                            <div className="empty-state-title">All Clear</div>
                            <div className="empty-state-desc">
                                No fire or smoke detected in recent history. The AI model is continuously monitoring all camera feeds.
                            </div>
                        </div>
                    ) : (
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                            {recent.map((a, i) => (
                                <motion.div
                                    key={i}
                                    className="alert-row-3d"
                                    initial={{ opacity: 0, x: -20 }}
                                    animate={{ opacity: 1, x: 0 }}
                                    transition={{ delay: i * 0.05 }}
                                    whileHover={{ x: 4, scale: 1.01 }}
                                >
                                    <div className="alert-row-icon">
                                        <Flame size={16} />
                                    </div>
                                    <div className="alert-row-content">
                                        <div className="alert-row-title">
                                            {String(a.alert_type || 'fire').toUpperCase()}
                                        </div>
                                        <div className="alert-row-meta">
                                            Camera {a.camera_id} · {a.zone || 'Unknown Zone'}
                                        </div>
                                    </div>
                                    <div className="alert-row-badge">
                                        <span className="pill-3d pill-3d-danger">
                                            {Math.round((a.confidence || 0) * 100)}%
                                        </span>
                                    </div>
                                    <div className="alert-row-time">
                                        {a.timestamp ? new Date(a.timestamp).toLocaleTimeString() : ''}
                                    </div>
                                </motion.div>
                            ))}
                        </div>
                    )}
                </GlassCard>

                {/* System Status */}
                <GlassCard accent="indigo" className="bento-span-6">
                    <div className="card-header-3d">
                        <div>
                            <h3 className="card-title-3d">System Status</h3>
                            <p className="card-subtitle-3d">Fire detection model configuration</p>
                        </div>
                    </div>
                    
                    <div className="info-rows-3d">
                        <InfoRow
                            icon={ShieldCheck}
                            label="Model Path"
                            value={status?.model_path || 'Not configured'}
                            accent={status?.model_loaded ? 'emerald' : 'rose'}
                        />
                        <InfoRow
                            icon={Info}
                            label="Fire-Specific Model"
                            value={status?.is_fire_specific ? 'Yes' : 'No'}
                            accent={status?.is_fire_specific ? 'emerald' : 'amber'}
                        />
                        <InfoRow
                            icon={Info}
                            label="Confidence Threshold"
                            value={`${(status?.confidence_threshold || 0.4) * 100}%`}
                            accent="indigo"
                        />
                        <InfoRow
                            icon={Info}
                            label="System Status"
                            value={status?.system_status || 'Unknown'}
                            accent={
                                status?.system_status === 'monitoring' ? 'emerald' :
                                status?.system_status === 'alert' ? 'rose' : 'amber'
                            }
                        />
                    </div>

                    {status?.classes && Object.keys(status.classes).length > 0 && (
                        <div style={{ marginTop: 20 }}>
                            <h4 style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginBottom: 10 }}>
                                Detected Classes
                            </h4>
                            <div className="class-pills">
                                {Object.entries(status.classes).map(([id, name]) => (
                                    <span key={id} className="pill-3d pill-3d-info">
                                        {name}
                                    </span>
                                ))}
                            </div>
                        </div>
                    )}
                </GlassCard>
            </div>
        </div>
    );
}

// Alert Row Styles (inline for this component)
const alertRowStyles = `
.alert-row-3d {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 12px 16px;
    background: linear-gradient(135deg, rgba(244, 63, 94, 0.08), rgba(244, 63, 94, 0.02));
    border: 1px solid rgba(244, 63, 94, 0.15);
    border-radius: 12px;
    transition: all 0.2s ease;
}

.alert-row-icon {
    width: 32px;
    height: 32px;
    border-radius: 8px;
    background: rgba(244, 63, 94, 0.15);
    display: flex;
    align-items: center;
    justify-content: center;
    color: #f43f5e;
}

.alert-row-content {
    flex: 1;
}

.alert-row-title {
    font-weight: 600;
    font-size: 0.9rem;
    color: var(--text-primary);
}

.alert-row-meta {
    font-size: 0.8rem;
    color: var(--text-muted);
    margin-top: 2px;
}

.alert-row-time {
    font-size: 0.75rem;
    color: var(--text-muted);
}

.card-header-3d {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    margin-bottom: 1.5rem;
}

.card-title-3d {
    font-size: 1.125rem;
    font-weight: 700;
    color: var(--text-primary);
    margin: 0 0 4px 0;
}

.card-subtitle-3d {
    font-size: 0.85rem;
    color: var(--text-muted);
    margin: 0;
}

.info-rows-3d {
    display: flex;
    flex-direction: column;
}

.class-pills {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
}

.bento-span-6 {
    grid-column: span 6;
}

@media (max-width: 768px) {
    .bento-span-6 {
        grid-column: span 12;
    }
}
`;

// Inject styles
const styleSheet = document.createElement('style');
styleSheet.textContent = alertRowStyles;
document.head.appendChild(styleSheet);
