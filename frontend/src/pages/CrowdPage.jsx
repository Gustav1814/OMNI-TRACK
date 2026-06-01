/**
 * OmniTrack AI — Crowd Density (live)
 * Polls /api/crowd/status and listens for WS crowd_alert events.
 */

import React, { useState } from 'react';
import { UsersRound, AlertTriangle } from 'lucide-react';
import {
    BarChart, Bar, ResponsiveContainer, XAxis, YAxis, Tooltip, CartesianGrid, Cell,
} from 'recharts';
import { crowdAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import StatCard from '../components/ui/StatCard';
import ContentCard from '../components/ui/ContentCard';
import useWebSocket from '../hooks/useWebSocket';
import useGradientColors from '../hooks/useGradientColors';

export default function CrowdPage() {
    const { a } = useGradientColors();
    const LEVEL_COLOR = {
        low: a,
        medium: '#f97316',
        high: '#ea580c',
        critical: '#f43f5e',
    };
    const { data, error } = useLivePoll(() => crowdAPI.status(), { intervalMs: 3000 });
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
                <StatCard icon={UsersRound} label="Zones Monitored" value={zones.length} accent="teal" />
                <StatCard icon={UsersRound} label="People (all zones)" value={zones.reduce((a, z) => a + (z.person_count || 0), 0)} accent="cyan" />
                <StatCard
                    icon={UsersRound}
                    label="High / Critical"
                    value={zones.filter((z) => ['high', 'critical'].includes(z.classification)).length}
                    accent="rose"
                />
                <StatCard
                    icon={UsersRound}
                    label="Avg Density"
                    value={zones.length ? (zones.reduce((a, z) => a + (z.density || 0), 0) / zones.length).toFixed(2) : 0}
                    suffix=" /m²"
                    accent="coral"
                />
            </div>

            <ContentCard title="Zone Density" subtitle="Color-coded by classification" accent="sky">
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
                                        <Cell key={i} fill={LEVEL_COLOR[d.level] || a} />
                                    ))}
                                </Bar>
                            </BarChart>
                        </ResponsiveContainer>
                    </div>
                )}
            </ContentCard>

            <ContentCard title="Zones" accent="teal">
                <div className="ui-feed-list">
                    {zones.map((z) => (
                        <div
                            key={`${z.camera_id}-${z.zone}`}
                            className="ui-lane-row"
                            style={{ gridTemplateColumns: '1fr 80px 100px 110px 90px' }}
                        >
                            <div>
                                <div className="ui-lane-row-title">{z.zone}</div>
                                <div className="ui-lane-row-sub">cam {z.camera_id}</div>
                            </div>
                            <span style={{ fontSize: 13 }}>{z.person_count} ppl</span>
                            <span style={{ fontSize: 12, color: 'var(--exec-muted)' }}>{(z.density || 0).toFixed(2)} /m²</span>
                            <span className={`pill pill-${z.classification === 'critical' || z.classification === 'high' ? 'danger' : z.classification === 'medium' ? 'warn' : 'success'}`}>
                                {z.classification}
                            </span>
                            <span style={{ fontSize: 11, color: 'var(--exec-muted)', textAlign: 'right' }}>
                                thr {z.threshold}
                            </span>
                        </div>
                    ))}
                </div>
            </ContentCard>
        </div>
    );
}
