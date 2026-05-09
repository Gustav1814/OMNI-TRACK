/**
 * OmniTrack AI — Re-Identification (live)
 * ────────────────────────────────────────
 * • Gallery size + Re-ID model version from /api/pipeline/status.
 * • Active persons (most recent global IDs) from /api/reid/active.
 * • Per-person journey lookup via /api/reid/journey/{id}.
 * • Live cross-camera match feed via WebSocket (reid_match).
 */

import React, { useEffect, useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import { Users, Search, Route, ArrowRight, Clock, MapPin, Camera as CameraIcon, ImageOff } from 'lucide-react';
import { reidAPI, pipelineAPI, reidSnapshotUrl } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import useWebSocket from '../hooks/useWebSocket';

export default function ReIDPage() {
    const { data: pipe } = useLivePoll(() => pipelineAPI.status(), { intervalMs: 15000 });
    const { data: active } = useLivePoll(() => reidAPI.active(15), { intervalMs: 10000 });
    const { data: recentMatches } = useLivePoll(() => reidAPI.recentMatches(20), { intervalMs: 15000 });

    const [query, setQuery] = useState('');
    const [journey, setJourney] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);

    // Live WS cross-camera events (merged with polled history below)
    const [liveMatches, setLiveMatches] = useState([]);
    useWebSocket('/ws/live', {
        onType: {
            reid_match: (d) => setLiveMatches((prev) => [{ ...d, ts: Date.now() }, ...prev].slice(0, 40)),
        },
    });

    // Merge live WS events + polled history, dedup by (global_id + timestamp)
    const matches = useMemo(() => {
        const seen = new Set();
        const combined = [];
        const toKey = (m) => `${m.global_id}-${m.previous_camera}-${m.current_camera}-${Math.round((m.timestamp || m.ts || 0))}`;
        for (const m of liveMatches) {
            const k = toKey(m);
            if (!seen.has(k)) { seen.add(k); combined.push(m); }
        }
        for (const m of (recentMatches || [])) {
            const k = toKey(m);
            if (!seen.has(k)) { seen.add(k); combined.push(m); }
        }
        return combined.slice(0, 40);
    }, [liveMatches, recentMatches]);

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
                            <div style={{ display: 'flex', gap: 10, marginBottom: 12, flexWrap: 'wrap' }}>
                                <span className="pill pill-info">{journey.global_id}</span>
                                <span className="pill">
                                    {Math.round((journey.total_duration || 0))}s total
                                </span>
                                <span className="pill">{journey.zones_visited} zones</span>
                                {journey.cameras_visited > 0 && (
                                    <span className="pill">{journey.cameras_visited} cameras</span>
                                )}
                            </div>

                            {/* Snapshot gallery — one thumbnail per camera the person was seen on */}
                            {(journey.journey_data || []).some((l) => l.has_snapshot) && (
                                <div
                                    style={{
                                        display: 'grid',
                                        gridTemplateColumns: 'repeat(auto-fill, minmax(110px, 1fr))',
                                        gap: 10,
                                        marginBottom: 14,
                                    }}
                                >
                                    {/* Dedup by camera_id but keep order of first appearance */}
                                    {(() => {
                                        const seen = new Set();
                                        return (journey.journey_data || [])
                                            .filter((l) => {
                                                if (seen.has(l.camera_id)) return false;
                                                seen.add(l.camera_id);
                                                return l.has_snapshot;
                                            })
                                            .map((leg) => (
                                                <SnapshotCard
                                                    key={`snap-${leg.camera_id}`}
                                                    globalId={journey.global_id}
                                                    cameraId={leg.camera_id}
                                                    zone={leg.zone}
                                                    timestamp={leg.timestamp}
                                                />
                                            ));
                                    })()}
                                </div>
                            )}

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
                                        {p.zone && <span style={{ marginLeft: 4, color: 'var(--text-muted)' }}>· {p.zone}</span>}
                                    </span>
                                </span>
                                <span style={{ fontSize: 11, color: 'var(--text-muted)', display: 'flex', gap: 6 }}>
                                    {p.leg_count > 1 && (
                                        <span className="pill" style={{ fontSize: 10, padding: '1px 6px' }}>
                                            {p.leg_count} cams
                                        </span>
                                    )}
                                    {Math.round(p.duration || 0)}s
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
                        Waiting for Re-ID events… (requires 2+ cameras with Re-ID enabled)
                    </div>
                ) : (
                    <ul className="event-list">
                        {matches.map((m, i) => {
                            const ts = m.timestamp
                                ? new Date(m.timestamp * 1000)
                                : new Date(m.ts || Date.now());
                            return (
                                <li key={`${m.global_id}-${i}`} style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
                                    <button
                                        className="pill pill-info"
                                        style={{ cursor: 'pointer', border: 'none' }}
                                        onClick={() => { setQuery(m.global_id); }}
                                        title="Click to look up journey"
                                    >
                                        {m.global_id}
                                    </button>
                                    <span style={{ fontSize: 12, color: 'var(--text-secondary)', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                                        <MapPin size={11} />
                                        cam {m.previous_camera}
                                        {m.previous_zone && <span style={{ color: 'var(--text-muted)' }}>({m.previous_zone})</span>}
                                        <ArrowRight size={12} style={{ margin: '0 2px' }} />
                                        cam {m.current_camera}
                                        {m.current_zone && <span style={{ color: 'var(--text-muted)' }}>({m.current_zone})</span>}
                                    </span>
                                    {m.gap_seconds != null && (
                                        <span style={{ fontSize: 11, color: 'var(--text-muted)', display: 'inline-flex', alignItems: 'center', gap: 3 }}>
                                            <Clock size={10} /> {m.gap_seconds.toFixed(1)}s gap
                                        </span>
                                    )}
                                    <span style={{ fontSize: 11, color: 'var(--text-muted)', marginLeft: 'auto' }}>
                                        {ts.toLocaleTimeString()}
                                    </span>
                                </li>
                            );
                        })}
                    </ul>
                )}
            </div>
        </div>
    );
}

function SnapshotCard({ globalId, cameraId, zone, timestamp }) {
    const [version, setVersion] = useState(0);
    const [error, setError] = useState(false);
    const url = reidSnapshotUrl(globalId, cameraId, version);

    // Refresh every 5 seconds to get updated crop
    useEffect(() => {
        const id = setInterval(() => setVersion((v) => v + 1), 15000);
        return () => clearInterval(id);
    }, []);

    return (
        <div
            style={{
                border: '1px solid var(--border)',
                borderRadius: 10,
                overflow: 'hidden',
                background: 'var(--bg-glass)',
            }}
        >
            <div style={{ position: 'relative', height: 100, background: '#0b0b0b' }}>
                {!error ? (
                    <img
                        src={url}
                        alt={`Person on cam ${cameraId}`}
                        style={{ width: '100%', height: '100%', objectFit: 'contain' }}
                        onError={() => setError(true)}
                    />
                ) : (
                    <div
                        style={{
                            width: '100%',
                            height: '100%',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            color: 'var(--text-muted)',
                            flexDirection: 'column',
                            gap: 4,
                        }}
                    >
                        <ImageOff size={18} />
                        <span style={{ fontSize: 10 }}>No image</span>
                    </div>
                )}
                <span
                    style={{
                        position: 'absolute',
                        top: 4,
                        left: 4,
                        fontSize: 10,
                        padding: '2px 6px',
                        borderRadius: 4,
                        background: 'rgba(0,0,0,0.6)',
                        color: '#fff',
                    }}
                >
                    <CameraIcon size={10} style={{ marginRight: 3, verticalAlign: -1 }} />
                    Cam {cameraId}
                </span>
            </div>
            <div style={{ padding: '6px 8px' }}>
                <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{zone || 'unknown'}</div>
                <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 2 }}>
                    {timestamp ? new Date(timestamp).toLocaleTimeString() : '-'}
                </div>
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
