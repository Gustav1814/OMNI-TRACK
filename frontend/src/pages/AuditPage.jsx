/**
 * OmniTrack AI — Audit Log (live)
 * Polls /api/audit/logs + /api/audit/verify (SHA-256 chain integrity).
 */

import React, { useState } from 'react';
import { ShieldCheck, ShieldAlert, RefreshCw, Link2 } from 'lucide-react';
import { auditAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import StatCard from '../components/ui/StatCard';
import ContentCard from '../components/ui/ContentCard';
import RecordCard from '../components/ui/RecordCard';

export default function AuditPage() {
    const [limit, setLimit] = useState(50);
    const { data: logs, refresh: refreshLogs } = useLivePoll(() => auditAPI.logs(limit), { intervalMs: 6000 });
    const { data: chain, refresh: refreshChain } = useLivePoll(() => auditAPI.verify(), { intervalMs: 10000 });

    const list = Array.isArray(logs) ? logs : [];
    const valid = chain?.valid;

    return (
        <div className="page-scroll">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Audit Log</h1>
                    <p className="page-subtitle">
                        Tamper-evident SHA-256 hash chain · AES-256 encrypted metadata
                    </p>
                </div>
                <button className="btn btn-secondary btn-xs" onClick={() => { refreshLogs(); refreshChain(); }}>
                    <RefreshCw size={12} /> Refresh
                </button>
            </div>

            <div className="stats-grid">
                <StatCard
                    icon={valid ? ShieldCheck : ShieldAlert}
                    label="Chain Integrity"
                    value={valid == null ? '—' : valid ? 'VALID' : 'BROKEN'}
                    accent={valid ? 'emerald' : 'rose'}
                />
                <StatCard icon={Link2} label="Total Entries" value={chain?.total ?? list.length} accent="teal" />
                <StatCard icon={ShieldAlert} label="Broken At" value={chain?.broken_at ?? '—'} accent={valid === false ? 'rose' : 'cyan'} />
                <StatCard icon={ShieldCheck} label="Shown" value={list.length} accent="coral" />
            </div>

            <ContentCard
                title="Event Log"
                accent="teal"
                actions={
                    <>
                        <span className="ui-content-card-sub">Limit:</span>
                        <select
                            className="form-select"
                            style={{ width: 100, padding: '4px 10px' }}
                            value={limit}
                            onChange={(e) => setLimit(Number(e.target.value))}
                        >
                            {[25, 50, 100, 200, 500].map((n) => (
                                <option key={n} value={n}>{n}</option>
                            ))}
                        </select>
                    </>
                }
            >
                {list.length === 0 ? (
                    <div className="page-empty-hint">
                        No audit entries yet.
                    </div>
                ) : (
                    <div className="ui-feed-list" style={{ maxHeight: 560, overflow: 'auto' }}>
                        {list.map((e) => (
                            <RecordCard
                                key={e.id}
                                icon={Link2}
                                accent={valid === false ? 'rose' : 'teal'}
                                title={e.description || '—'}
                                meta={
                                    <>
                                        <span className="pill">#{e.id}</span>
                                        <span className={`pill ${pillForEventType(e.event_type)}`}>{e.event_type}</span>
                                        <code title={e.current_hash} style={{ fontSize: 11 }}>
                                            {e.current_hash?.slice(0, 16)}…
                                        </code>
                                        <span style={{ marginLeft: 'auto' }}>
                                            {e.timestamp && new Date(e.timestamp).toLocaleString()}
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


function pillForEventType(type) {
    switch ((type || '').toUpperCase()) {
        case 'LOGIN': return 'pill-success';
        case 'LOGOUT': return 'pill-info';
        case 'FIRE_ALERT': return 'pill-danger';
        case 'PIPELINE_START':
        case 'PIPELINE_STOP': return 'pill-warn';
        default: return '';
    }
}
