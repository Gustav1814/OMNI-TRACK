/**
 * OmniTrack AI — Crowd Density Analytics (Enhanced)
 * Real-time footfall tracking and zone density monitoring
 */

import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { UsersRound, AlertTriangle, MapPin, Activity, Users } from 'lucide-react';
import {
    BarChart, Bar, ResponsiveContainer, XAxis, YAxis, Tooltip, CartesianGrid, Cell, LineChart, Line,
} from 'recharts';
import { crowdAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import useWebSocket from '../hooks/useWebSocket';
import GlassCard, { StatCard, PageHeader, InfoRow } from '../components/GlassCard';

const LEVEL_COLOR = {
    low: '#10b981',
    medium: '#fbbf24',
    high: '#f97316',
    critical: '#f43f5e',
};

const LEVEL_BG = {
    low: 'rgba(16, 185, 129, 0.1)',
    medium: 'rgba(251, 191, 36, 0.1)',
    high: 'rgba(249, 115, 22, 0.1)',
    critical: 'rgba(244, 63, 94, 0.1)',
};

export default function CrowdPage() {
    const { data, error } = useLivePoll(() => crowdAPI.status(), { intervalMs: 3000 });
    const [alerts, setAlerts] = useState([]);
    useWebSocket('/ws/live', {
        onType: {
            crowd_alert: (d) => setAlerts((prev) => [{ ...d, ts: Date.now() }, ...prev].slice(0, 20)),
        },
    });

    const zones = Array.isArray(data) ? data : [];
    const chartData = zones.map((z) => ({ 
        name: z.zone, 
        count: z.person_count, 
        level: z.classification,
        density: z.density || 0 
    }));

    const totalPeople = zones.reduce((a, z) => a + (z.person_count || 0), 0);
    const avgDensity = zones.length ? (zones.reduce((a, z) => a + (z.density || 0), 0) / zones.length).toFixed(2) : 0;
    const criticalZones = zones.filter((z) => ['high', 'critical'].includes(z.classification)).length;

    return (
        <div className="page-scroll">
            <PageHeader
                title="Crowd Density Analytics"
                subtitle="Real-time footfall tracking and zone density monitoring"
            />

            <AnimatePresence>
                {error && (
                    <motion.div
                        className="alert-banner-3d alert-banner-3d-danger"
                        initial={{ opacity: 0, y: -20 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: -20 }}
                    >
                        <AlertTriangle size={20} />
                        <span>Unable to reach crowd analytics. Check connection.</span>
                    </motion.div>
                )}
                {alerts.length > 0 && (
                    <motion.div
                        className="alert-banner-3d alert-banner-3d-warning"
                        initial={{ opacity: 0, y: -20 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: -20 }}
                    >
                        <AlertTriangle size={20} />
                        <span>
                            <strong>{alerts[0].zone}</strong> is {alerts[0].density_level} 
                            ({alerts[0].person_count} people detected)
                        </span>
                    </motion.div>
                )}
            </AnimatePresence>

            {/* Stats Grid */}
            <div className="stats-grid-3d">
                <StatCard
                    icon={MapPin}
                    label="Zones Monitored"
                    value={zones.length}
                    accent="indigo"
                    tag="Active"
                    progress={zones.length ? 100 : 0}
                />
                <StatCard
                    icon={Users}
                    label="Total People"
                    value={totalPeople}
                    accent="cyan"
                    tag="Live"
                />
                <StatCard
                    icon={AlertTriangle}
                    label="High / Critical Zones"
                    value={criticalZones}
                    accent={criticalZones > 0 ? 'rose' : 'emerald'}
                    tag={criticalZones > 0 ? 'Alert' : 'Safe'}
                    trend={criticalZones > 0 ? 100 : 0}
                />
                <StatCard
                    icon={Activity}
                    label="Avg Density"
                    value={avgDensity}
                    suffix=" /m²"
                    accent="amber"
                    tag="Per Zone"
                    progress={Math.min(100, (avgDensity / 2) * 100)}
                />
            </div>

            {/* Main Content */}
            <div className="cards-grid-3d cards-grid-3d-2col">
                {/* Zone Density Chart */}
                <GlassCard accent="cyan" glow className="bento-span-6">
                    <div className="card-header-3d">
                        <div>
                            <h3 className="card-title-3d">Zone Density</h3>
                            <p className="card-subtitle-3d">Person count per monitored zone</p>
                        </div>
                        <div className="legend-mini">
                            {Object.entries(LEVEL_COLOR).map(([level, color]) => (
                                <span key={level} className="legend-item">
                                    <span className="legend-dot" style={{ background: color }} />
                                    {level}
                                </span>
                            ))}
                        </div>
                    </div>

                    {chartData.length === 0 ? (
                        <div className="empty-state-3d">
                            <div className="empty-state-icon">
                                <MapPin size={28} />
                            </div>
                            <div className="empty-state-title">No Zones Configured</div>
                            <div className="empty-state-desc">
                                Configure camera zones and start the pipeline to see crowd density data.
                            </div>
                        </div>
                    ) : (
                        <div style={{ height: 300 }}>
                            <ResponsiveContainer width="100%" height="100%">
                                <BarChart data={chartData} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
                                    <CartesianGrid stroke="rgba(148,163,184,0.1)" vertical={false} />
                                    <XAxis 
                                        dataKey="name" 
                                        stroke="#6b7280" 
                                        fontSize={11} 
                                        tickLine={false}
                                        axisLine={false}
                                    />
                                    <YAxis 
                                        stroke="#6b7280" 
                                        fontSize={11} 
                                        tickLine={false}
                                        axisLine={false}
                                    />
                                    <Tooltip 
                                        contentStyle={{ 
                                            background: 'var(--bg-card)', 
                                            border: '1px solid var(--border)',
                                            borderRadius: '12px',
                                            boxShadow: 'var(--shadow-lg)'
                                        }}
                                        labelStyle={{ color: 'var(--text-primary)' }}
                                        itemStyle={{ color: 'var(--text-secondary)' }}
                                    />
                                    <Bar dataKey="count" radius={[8, 8, 0, 0]}>
                                        {chartData.map((d, i) => (
                                            <Cell key={i} fill={LEVEL_COLOR[d.level] || '#6366f1'} />
                                        ))}
                                    </Bar>
                                </BarChart>
                            </ResponsiveContainer>
                        </div>
                    )}
                </GlassCard>

                {/* Zones List */}
                <GlassCard accent="indigo" className="bento-span-6">
                    <div className="card-header-3d">
                        <div>
                            <h3 className="card-title-3d">Zone Status</h3>
                            <p className="card-subtitle-3d">Detailed metrics per monitoring zone</p>
                        </div>
                        <span className="pill-3d pill-3d-info">
                            {zones.length} Active
                        </span>
                    </div>

                    {zones.length === 0 ? (
                        <div className="empty-state-3d">
                            <div className="empty-state-icon">
                                <UsersRound size={28} />
                            </div>
                            <div className="empty-state-title">Waiting for Data</div>
                            <div className="empty-state-desc">
                                Start the processing pipeline to begin receiving zone analytics.
                            </div>
                        </div>
                    ) : (
                        <div className="zones-list-3d">
                            {zones.map((z, i) => (
                                <motion.div
                                    key={`${z.camera_id}-${z.zone}`}
                                    className="zone-row-3d"
                                    initial={{ opacity: 0, x: -20 }}
                                    animate={{ opacity: 1, x: 0 }}
                                    transition={{ delay: i * 0.05 }}
                                    whileHover={{ x: 4, scale: 1.01 }}
                                    style={{
                                        background: LEVEL_BG[z.classification] || LEVEL_BG.low,
                                        borderColor: LEVEL_COLOR[z.classification] || LEVEL_COLOR.low,
                                    }}
                                >
                                    <div className="zone-info">
                                        <div className="zone-name">{z.zone}</div>
                                        <div className="zone-meta">Camera {z.camera_id}</div>
                                    </div>
                                    <div className="zone-stats">
                                        <div className="zone-stat">
                                            <span className="zone-stat-value">{z.person_count}</span>
                                            <span className="zone-stat-label">people</span>
                                        </div>
                                        <div className="zone-stat">
                                            <span className="zone-stat-value">{(z.density || 0).toFixed(2)}</span>
                                            <span className="zone-stat-label">/m²</span>
                                        </div>
                                    </div>
                                    <div className="zone-badge">
                                        <span className={`pill-3d pill-3d-${
                                            z.classification === 'critical' || z.classification === 'high' ? 'danger' : 
                                            z.classification === 'medium' ? 'warning' : 'success'
                                        }`}>
                                            {z.classification}
                                        </span>
                                    </div>
                                    <div className="zone-threshold">
                                        thr: {z.threshold}
                                    </div>
                                </motion.div>
                            ))}
                        </div>
                    )}
                </GlassCard>
            </div>
        </div>
    );
}

// Styles injection
const crowdStyles = `
.legend-mini {
    display: flex;
    gap: 12px;
    flex-wrap: wrap;
}

.legend-item {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 0.7rem;
    color: var(--text-muted);
    text-transform: capitalize;
}

.legend-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
}

.zones-list-3d {
    display: flex;
    flex-direction: column;
    gap: 8px;
    max-height: 400px;
    overflow-y: auto;
}

.zone-row-3d {
    display: grid;
    grid-template-columns: 1fr auto auto auto;
    gap: 16px;
    align-items: center;
    padding: 14px 16px;
    border: 1px solid;
    border-radius: 12px;
    transition: all 0.2s ease;
}

.zone-info {
    min-width: 0;
}

.zone-name {
    font-weight: 600;
    font-size: 0.9rem;
    color: var(--text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

.zone-meta {
    font-size: 0.75rem;
    color: var(--text-muted);
    margin-top: 2px;
}

.zone-stats {
    display: flex;
    gap: 16px;
}

.zone-stat {
    text-align: center;
}

.zone-stat-value {
    display: block;
    font-weight: 700;
    font-size: 0.95rem;
    color: var(--text-primary);
}

.zone-stat-label {
    font-size: 0.7rem;
    color: var(--text-muted);
}

.zone-threshold {
    font-size: 0.75rem;
    color: var(--text-muted);
    font-variant-numeric: tabular-nums;
}

@media (max-width: 640px) {
    .zone-row-3d {
        grid-template-columns: 1fr auto;
        gap: 12px;
    }
    .zone-stats, .zone-threshold {
        display: none;
    }
}
`;

const styleSheet = document.createElement('style');
styleSheet.textContent = crowdStyles;
document.head.appendChild(styleSheet);
