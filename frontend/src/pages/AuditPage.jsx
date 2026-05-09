/**
 * OmniTrack AI — Audit Log (live)
 * Polls /api/audit/logs + /api/audit/verify (SHA-256 chain integrity).
 */

import React, { useState } from 'react';
import { motion } from 'framer-motion';
import { ShieldCheck, ShieldAlert, RefreshCw, Link2 } from 'lucide-react';
import { auditAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';

export default function AuditPage() {
    const [limit, setLimit] = useState(50);
    const { data: logs, refresh: refreshLogs } = useLivePoll(() => auditAPI.logs(limit), { intervalMs: 15000 });
    const { data: chain, refresh: refreshChain } = useLivePoll(() => auditAPI.verify(), { intervalMs: 60000 });

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
                <motion.div className="stat-card" whileHover={{ y: -2 }}>
                    <div className={`stat-icon stat-icon-${valid ? 'emerald' : 'rose'}`}>
                        {valid ? <ShieldCheck size={18} /> : <ShieldAlert size={18} />}
                    </div>
                    <div className="stat-label">Chain Integrity</div>
                    <div className="stat-value">{valid == null ? '—' : valid ? 'VALID' : 'BROKEN'}</div>
                </motion.div>
                <Stat label="Total Entries" value={chain?.total ?? list.length} accent="indigo" />
                <Stat label="Broken At" value={chain?.broken_at ?? '—'} accent={valid === false ? 'rose' : 'cyan'} />
                <Stat label="Shown" value={list.length} accent="amber" />
            </div>

            <div className="card">
                <div className="card-header">
                    <h3 className="card-title">Event Log</h3>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                        <span className="card-subtitle">Limit:</span>
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
                    </div>
                </div>

                {list.length === 0 ? (
                    <div className="page-empty-hint">
                        No audit entries yet.
                    </div>
                ) : (
                    <div style={{ display: 'grid', gap: 6, maxHeight: 560, overflow: 'auto' }}>
                        {list.map((e) => (
                            <div
                                key={e.id}
                                style={{
                                    display: 'grid',
                                    gridTemplateColumns: '60px 160px 1fr auto',
                                    gap: 10, alignItems: 'center',
                                    padding: '10px 12px',
                                    background: 'var(--bg-glass)',
                                    border: '1px solid var(--border)', borderRadius: 10,
                                }}
                            >
                                <span className="pill">#{e.id}</span>
                                <span className={`pill ${pillForEventType(e.event_type)}`}>{e.event_type}</span>
                                <div style={{ overflow: 'hidden' }}>
                                    <div style={{ fontSize: 13, fontWeight: 500 }}>
                                        {e.description || '—'}
                                    </div>
                                    <div style={{
                                        display: 'flex', gap: 8,
                                        fontSize: 11, color: 'var(--text-muted)',
                                        marginTop: 2, overflow: 'hidden',
                                    }}>
                                        <Link2 size={11} />
                                        <code title={e.current_hash}
                                            style={{ whiteSpace: 'nowrap', textOverflow: 'ellipsis', overflow: 'hidden' }}>
                                            {e.current_hash?.slice(0, 16)}…
                                        </code>
                                        {e.previous_hash && (
                                            <>
                                                <span>←</span>
                                                <code title={e.previous_hash}
                                                    style={{ whiteSpace: 'nowrap', textOverflow: 'ellipsis', overflow: 'hidden' }}>
                                                    {e.previous_hash.slice(0, 16)}…
                                                </code>
                                            </>
                                        )}
                                    </div>
                                </div>
                                <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                                    {e.timestamp && new Date(e.timestamp).toLocaleString()}
                                </span>
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
}

function Stat({ label, value, accent = 'indigo' }) {
    return (
        <motion.div className="stat-card" whileHover={{ y: -2 }}>
            <div className={`stat-icon stat-icon-${accent}`}><ShieldCheck size={18} /></div>
            <div className="stat-label">{label}</div>
            <div className="stat-value">{value}</div>
        </motion.div>
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
