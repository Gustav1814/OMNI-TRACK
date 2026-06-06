/**
 * OmniTrack AI — Fire & Smoke (live)
 * Polls /api/fire/alerts and /api/fire/status; highlights active WS alerts.
 */

import React, { useState } from 'react';
import { Flame, AlertTriangle, ShieldAlert } from 'lucide-react';
import { fireAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import StatCard from '../components/ui/StatCard';
import ContentCard from '../components/ui/ContentCard';
import RecordCard from '../components/ui/RecordCard';
import useWebSocket from '../hooks/useWebSocket';

import PageHeader from '../components/PageHeader';

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
            <PageHeader
                kicker="Safety & compliance"
                title="Safety"
                highlight="Monitoring"
                subtitle="Continuous watch for smoke and fire events across every store location, with instant leadership alerts."
            />

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
                <StatCard icon={Flame} label="Alerts Today" value={status?.total_today ?? list.length} accent={(status?.total_today ?? list.length) ? 'rose' : 'teal'} />
                <StatCard icon={ShieldAlert} label="Active Alerts" value={status?.active_alerts ?? 0} accent="emerald" />
                <StatCard icon={ShieldAlert} label="System Status" value={status?.system_status || 'unknown'} accent="cyan" />
                <StatCard icon={Flame} label="Cameras Covered" value={status?.cameras_covered ?? 0} accent="coral" />
            </div>

            <ContentCard
                title="Recent Alerts"
                subtitle={recent.length ? `${recent.length} of ${list.length} alerts` : 'No alerts — safe.'}
                accent="rose"
            >
                {recent.length === 0 ? (
                    <div className="page-empty-hint page-empty-hint--left">
                        No fire/smoke alerts in the recent history.
                    </div>
                ) : (
                    <div className="ui-feed-list">
                        {recent.map((a, i) => (
                            <RecordCard
                                key={i}
                                icon={Flame}
                                accent="rose"
                                title={String(a.alert_type || 'fire').toUpperCase()}
                                meta={
                                    <>
                                        <span className="pill pill-danger">Cam {a.camera_id}</span>
                                        <span className="pill">{a.zone || '—'}</span>
                                        <span className="pill pill-warn">
                                            {Math.round((a.confidence || 0) * 100)}%
                                        </span>
                                        <span style={{ marginLeft: 'auto' }}>
                                            {a.timestamp ? new Date(a.timestamp).toLocaleString() : ''}
                                        </span>
                                    </>
                                }
                            />
                        ))}
                    </div>
                )}
            </ContentCard>
        </div>
    );
}
