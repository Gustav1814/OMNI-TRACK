/**
 * OmniTrack AI — Re-Identification (live)
 * ────────────────────────────────────────
 * • Gallery size + Re-ID model version from /api/pipeline/status.
 * • Active persons (most recent global IDs) from /api/reid/active.
 * • Per-person journey lookup via /api/reid/journey/{id}.
 * • Live cross-camera match feed via WebSocket (reid_match).
 */

import React, { useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import { Users, Search, Route, ArrowRight } from 'lucide-react';
import { reidAPI, pipelineAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import useWebSocket from '../hooks/useWebSocket';

export default function ReIDPage() {
    const { data: pipe } = useLivePoll(() => pipelineAPI.status(), { intervalMs: 5000 });
    const { data: active } = useLivePoll(() => reidAPI.active(), { intervalMs: 4000 });

    const [query, setQuery] = useState('');
    const [journey, setJourney] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);

    // Cross-camera match WS feed
    const [matches, setMatches] = useState([]);
    useWebSocket('/ws/live', {
        onType: {
            reid_match: (d) => setMatches((prev) => [{ ...d, ts: Date.now() }, ...prev].slice(0, 40)),
        },
    });

    const reidModule = pipe?.ai_modules?.reid || {};
    const gallerySize = reidModule.gallery_size ?? 0;
    const uniqueIds = reidModule.unique_identities ?? 0;
    const modelVersion = reidModule.model || 'osnet_x1_0';
    const threshold = reidModule.threshold ?? 0.6;

    const lookup = async (e) => {
        e?.preventDefault?.();
        if (!query.trim()) return;
        setLoading(true); setError(null); setJourney(null);
        try {
            const res = await reidAPI.journey(query.trim());
            setJourney(res.data);
        } catch (err) {
            setError(err?.response?.data?.detail || err.message);
        } finally { setLoading(false); }
    };

    const activeList = Array.isArray(active) ? active : [];

    return (
        <div className="page-scroll">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Re-Identification</h1>
                    <p className="page-subtitle">Torchreid {modelVersion} · cosine similarity ≥ {threshold}</p>
                </div>
            </div>

            <div className="stats-grid">
                <Stat icon={Users} label="Global Identities" value={uniqueIds} accent="indigo" />
                <Stat icon={Users} label="Gallery Embeddings" value={gallerySize} accent="cyan" />
                <Stat icon={Route} label="Live Matches (recent)" value={matches.length} accent="emerald" />
                <Stat icon={Users} label="Active Now" value={activeList.length} accent="gold" />
            </div>

            <div className="two-col">
                <div className="card">
                    <div className="card-header">
                        <h3 className="card-title">Person Journey Lookup</h3>
                        <div className="card-subtitle">Enter a global ID (e.g. PERSON-0004)</div>
                    </div>
                    <form onSubmit={lookup} style={{ display: 'flex', gap: 10 }}>
                        <input
                            className="form-input"
                            placeholder="PERSON-0004"
                            value={query}
                            onChange={(e) => setQuery(e.target.value)}
                        />
                        <button className="btn btn-primary" disabled={loading}>
                            <Search size={14} /> {loading ? 'Searching…' : 'Look up'}
                        </button>
                    </form>

                    {error && <div className="alert-banner danger" style={{ marginTop: 10 }}>{error}</div>}
                    {journey && (
                        <div style={{ marginTop: 16 }}>
                            <div style={{ display: 'flex', gap: 10, marginBottom: 10 }}>
                                <span className="pill pill-info">{journey.global_id}</span>
                                <span className="pill">
                                    {Math.round((journey.total_duration || 0) / 60)} min total
                                </span>
                                <span className="pill">{journey.zones_visited} zones</span>
                            </div>
                            <ol style={{ paddingLeft: 20, display: 'grid', gap: 6 }}>
                                {(journey.journey_data || []).map((leg, i) => (
                                    <li key={i} style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
                                        <strong>Cam {leg.camera_id}</strong> · {leg.zone}
                                        <ArrowRight size={12} style={{ margin: '0 6px', verticalAlign: -1 }} />
                                        {Math.round((leg.duration || leg.dwell_time || 0))}s
                                        <span style={{ marginLeft: 8, color: 'var(--text-muted)' }}>
                                            {leg.timestamp && new Date(leg.timestamp).toLocaleTimeString()}
                                        </span>
                                    </li>
                                ))}
                            </ol>
                        </div>
                    )}
                </div>

                <div className="card">
                    <div className="card-header">
                        <h3 className="card-title">Active Persons</h3>
                        <div className="card-subtitle">Most recent Re-ID activity</div>
                    </div>
                    <div style={{ display: 'grid', gap: 6, maxHeight: 320, overflow: 'auto' }}>
                        {activeList.length === 0 && (
                            <div style={{ color: 'var(--text-muted)', padding: 12 }}>No active persons.</div>
                        )}
                        {activeList.map((p, i) => (
                            <button
                                key={`${p.global_id}-${i}`}
                                className="nav-item"
                                style={{ justifyContent: 'space-between', padding: '8px 12px', cursor: 'pointer' }}
                                onClick={() => { setQuery(p.global_id); lookup(); }}
                            >
                                <span style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                                    <span className="pill pill-info">{p.global_id}</span>
                                    <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                                        cam {p.camera_id}
                                    </span>
                                </span>
                                <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                                    {Math.round((p.confidence || 0) * 100)}%
                                </span>
                            </button>
                        ))}
                    </div>
                </div>
            </div>

            <div className="card" style={{ marginTop: 18 }}>
                <div className="card-header">
                    <h3 className="card-title">Live Cross-Camera Matches</h3>
                    <div className="card-subtitle">Streamed from the pipeline when a known person re-appears</div>
                </div>
                {matches.length === 0 ? (
                    <div style={{ padding: 16, color: 'var(--text-muted)' }}>
                        Waiting for Re-ID events…
                    </div>
                ) : (
                    <ul className="event-list">
                        {matches.map((m, i) => (
                            <li key={i}>
                                <span className="pill pill-info">{m.global_id}</span>
                                <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                                    cam {m.previous_camera} <ArrowRight size={12} style={{ verticalAlign: -1 }} /> cam {m.current_camera}
                                </span>
                                <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                                    {new Date(m.ts).toLocaleTimeString()}
                                </span>
                            </li>
                        ))}
                    </ul>
                )}
            </div>
        </div>
    );
}

function Stat({ icon: Icon, label, value, accent }) {
    return (
        <motion.div className="stat-card" whileHover={{ y: -2 }}>
            <div className={`stat-icon stat-icon-${accent}`}><Icon size={18} /></div>
            <div className="stat-label">{label}</div>
            <div className="stat-value">{value}</div>
        </motion.div>
    );
}
