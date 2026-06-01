/**
 * OmniTrack AI — Re-Identification (live)
 */

import React, { useState } from 'react';
import { Users, Search, Route, ArrowRight } from 'lucide-react';
import { reidAPI, pipelineAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import StatCard from '../components/ui/StatCard';
import ContentCard from '../components/ui/ContentCard';
import RecordCard from '../components/ui/RecordCard';
import useWebSocket from '../hooks/useWebSocket';

export default function ReIDPage() {
    const { data: pipe } = useLivePoll(() => pipelineAPI.status(), { intervalMs: 5000 });
    const { data: active } = useLivePoll(() => reidAPI.active(), { intervalMs: 4000 });

    const [query, setQuery] = useState('');
    const [journey, setJourney] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);

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
                <StatCard icon={Users} label="Global Identities" value={uniqueIds} accent="teal" />
                <StatCard icon={Users} label="Gallery Embeddings" value={gallerySize} accent="cyan" />
                <StatCard icon={Route} label="Live Matches (recent)" value={matches.length} accent="emerald" />
                <StatCard icon={Users} label="Active Now" value={activeList.length} accent="coral" />
            </div>

            <div className="two-col">
                <ContentCard
                    title="Person Journey Lookup"
                    subtitle="Enter a global ID (e.g. PERSON-0004)"
                    accent="teal"
                >
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
                </ContentCard>

                <ContentCard title="Active Persons" subtitle="Most recent Re-ID activity" accent="sky">
                    {activeList.length === 0 ? (
                        <div className="page-empty-hint page-empty-hint--left">No active persons.</div>
                    ) : (
                        <div className="ui-feed-list" style={{ maxHeight: 320, overflow: 'auto' }}>
                            {activeList.map((p, i) => (
                                <button
                                    key={`${p.global_id}-${i}`}
                                    type="button"
                                    className="ui-lane-row"
                                    style={{ gridTemplateColumns: '1fr auto', cursor: 'pointer', textAlign: 'left', width: '100%' }}
                                    onClick={() => { setQuery(p.global_id); lookup(); }}
                                >
                                    <span style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                                        <span className="pill pill-info">{p.global_id}</span>
                                        <span className="ui-lane-row-sub">cam {p.camera_id}</span>
                                    </span>
                                    <span className="ui-lane-row-sub">
                                        {Math.round((p.confidence || 0) * 100)}%
                                    </span>
                                </button>
                            ))}
                        </div>
                    )}
                </ContentCard>
            </div>

            <ContentCard
                title="Live Cross-Camera Matches"
                subtitle="Streamed from the pipeline when a known person re-appears"
                accent="emerald"
            >
                {matches.length === 0 ? (
                    <div className="page-empty-hint">Waiting for Re-ID events…</div>
                ) : (
                    <div className="ui-feed-list">
                        {matches.map((m, i) => (
                            <RecordCard
                                key={i}
                                icon={Route}
                                accent="emerald"
                                title={m.global_id}
                                meta={
                                    <>
                                        <span style={{ fontSize: 12 }}>
                                            cam {m.previous_camera} <ArrowRight size={12} style={{ verticalAlign: -1 }} /> cam {m.current_camera}
                                        </span>
                                        <span style={{ marginLeft: 'auto', fontSize: 11 }}>
                                            {new Date(m.ts).toLocaleTimeString()}
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
