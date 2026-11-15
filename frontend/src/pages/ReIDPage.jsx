/**
 * OmniTrack AI — Re-Identification (live)
 *
 * Everything shown here comes from the Re-ID coordinator (/api/reid/*):
 * identities in memory and in the database, thresholds, the quality gate,
 * camera topology (transit-time gating) and expected arrivals (handoff).
 */

import React, { useCallback, useState } from 'react';
import { Search, ArrowRight, Trash2, Plus, ScanSearch } from 'lucide-react';
import api, { reidAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import useWebSocket from '../hooks/useWebSocket';
import { Metric } from '../components/ui';
import PersonThumb from '../components/ops/PersonThumb';

const fmtTime = (epoch) => (epoch ? new Date(epoch * 1000).toLocaleTimeString() : '—');
const pct = (v) => (v === null || v === undefined ? '—' : `${Math.round(v * 100)}%`);
const cam = (id) => (id === null || id === undefined || id === '' ? '—' : String(id).padStart(2, '0'));
const errText = (err) => err?.response?.data?.detail
    ? (typeof err.response.data.detail === 'string' ? err.response.data.detail : JSON.stringify(err.response.data.detail))
    : err.message;

/** Similarity meter: 0..1 → width; no value → empty track with "—". */
function Confidence({ value, label = 'Similarity' }) {
    const has = value !== null && value !== undefined && Number(value) > 0;
    const v = has ? Math.max(0, Math.min(1, Number(value))) : 0;
    return (
        <div className="ops-conf" aria-label={`${label} ${has ? pct(v) : 'not available'}`}>
            <div className="meter-track"><div className="meter-fill" style={{ '--value': `${v * 100}%` }} /></div>
            <span className="ops-conf-value">{has ? pct(v) : '—'}</span>
        </div>
    );
}

function Empty({ title, children }) {
    return (
        <div className="empty-state ops-fill">
            <div className="empty-state-title">{title}</div>
            {children ? <p>{children}</p> : null}
        </div>
    );
}

export default function ReIDPage() {
    const { data: status } = useLivePoll(() => api.get('/reid/status'), { intervalMs: 5000 });
    const { data: gallery } = useLivePoll(() => api.get('/reid/gallery', { params: { limit: 50 } }), { intervalMs: 8000 });
    const { data: active } = useLivePoll(() => reidAPI.active(), { intervalMs: 4000 });
    const { data: expected } = useLivePoll(() => api.get('/reid/expected'), { intervalMs: 5000 });
    const { data: topology, refresh: refreshTopology } = useLivePoll(() => api.get('/reid/topology'), { intervalMs: 30000 });

    const [query, setQuery] = useState('');
    const [journey, setJourney] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    const [similar, setSimilar] = useState(null);   // null = not searched; [] = searched, none found
    const [simLoading, setSimLoading] = useState(false);
    const [simError, setSimError] = useState(null);

    const [matches, setMatches] = useState([]);
    useWebSocket('/ws/live', {
        onType: {
            reid_match: (d) => setMatches((prev) => [{ ...d, ts: Date.now() }, ...prev].slice(0, 40)),
        },
    });

    const lookup = useCallback(async (gid) => {
        const id = String(gid || '').trim();
        if (!id) return;
        setQuery(id);
        setLoading(true); setError(null); setJourney(null);
        setSimilar(null); setSimError(null);
        try {
            const res = await reidAPI.journey(id);
            setJourney(res.data);
        } catch (err) {
            setError(errText(err));
        } finally { setLoading(false); }
    }, []);

    const findSimilar = useCallback(async (gid) => {
        setSimLoading(true); setSimError(null);
        try {
            const res = await reidAPI.search({ global_id: gid, top_k: 8 });
            setSimilar(Array.isArray(res.data) ? res.data : []);
        } catch (err) {
            setSimError(errText(err));
        } finally { setSimLoading(false); }
    }, []);

    const st = status?.status === 'ok' ? status : null;
    const model = st?.model || {};
    const activeList = Array.isArray(active) ? active : [];
    const identities = gallery?.identities || [];
    const expectedList = expected?.expected || [];
    const gate = st?.quality_gate || {};
    const gateTotal = Object.values(gate).reduce((a, b) => a + Number(b || 0), 0);
    const journeyLegs = journey?.journey_data || [];
    const persisted = gallery?.persisted_identities;
    const heroValue = persisted ?? st?.total_identities ?? null;
    const heroLabel = persisted != null ? 'Identities stored' : 'Identities in memory';
    const camSet = new Set();
    let multiCam = 0;
    identities.forEach((p) => {
        const cs = (p.cameras || []).length ? p.cameras : [p.last_camera_id];
        cs.filter((c) => c != null).forEach((c) => camSet.add(String(c)));
        if (new Set(cs.filter((c) => c != null).map(String)).size > 1) multiCam += 1;
    });
    const camerasSeen = camSet.size;

    return (
        <div className="page-scroll">
            <header className="page-header">
                <div>
                    <h1 className="page-title">Re-identification</h1>
                    <div className="osd-strip ops-head-osd" aria-label="Re-ID status">
                        <span>
                            <span className={`live-dot ${st ? 'is-ok' : status ? 'is-warn' : ''}`} aria-hidden />
                            <strong>{st ? 'Running' : status ? 'Not running' : 'Connecting'}</strong>
                        </span>
                        <span>Model <strong>{model.model || (model.status === 'not_loaded' ? 'Loads with first feed' : model.status ? model.status.replace(/_/g, ' ') : '—')}</strong></span>
                        {gallery?.model_version ? <span>Version <strong>{gallery.model_version}</strong></span> : null}
                        <span>Match ≥ <strong>{st?.t_match ?? '—'}</strong></span>
                        <span>Update ≥ <strong>{st?.t_update ?? '—'}</strong></span>
                    </div>
                </div>
            </header>

            <section className="ops-top" aria-label="Re-ID summary">
                {heroValue != null ? (
                    <div className="panel panel--feature vf ops-feature">
                        <div className="panel-body ops-feature-body">
                            <span className="osd">{heroLabel}</span>
                            <Metric size="hero" value={heroValue} />
                            <div className="osd-strip ops-feature-osd">
                                <span>In memory <strong>{st?.total_identities ?? identities.length}</strong></span>
                                {camerasSeen ? <span>Cameras <strong>{camerasSeen}</strong></span> : null}
                            </div>
                        </div>
                    </div>
                ) : (
                    <div className="panel ops-feature">
                        <div className="panel-body ops-feature-body">
                            <Empty title={status ? 'No identities yet' : 'Reading the gallery'}>
                                {status ? 'People appear once a camera tracks them long enough to embed.' : 'Loading Re-ID status.'}
                            </Empty>
                        </div>
                    </div>
                )}
                <div className="ops-top-kpis">
                    <div className="kpi-strip">
                        <Metric label="On camera now" value={Array.isArray(active) ? activeList.length : null} sub="Tracks in the last 10 s" />
                        <Metric label="Expected arrivals" value={expected ? expectedList.length : null} sub="Due on a linked camera" />
                        <Metric label="Seen on 2+ cameras" value={gallery ? multiCam : null} sub="Matched across feeds" />
                    </div>
                </div>
            </section>

            <div className="ops-split">
                <section className="panel ops-panel" aria-labelledby="reid-ids">
                    <div className="panel-head">
                        <div>
                            <h2 className="panel-title" id="reid-ids">Identities</h2>
                            <p className="panel-sub">In-memory gallery, most recent first. Select one to trace its journey.</p>
                        </div>
                        <span className="count-badge">{identities.length}</span>
                    </div>
                    <div className="panel-body ops-col-body">
                        {identities.length === 0 ? (
                            <Empty title="No identities yet">People appear here once a camera tracks them long enough to embed.</Empty>
                        ) : (
                            <ol className="rank-list ops-scroll">
                                {identities.map((p, i) => {
                                    const cams = (p.cameras || []).length ? p.cameras : [p.last_camera_id];
                                    return (
                                        <li key={p.global_id}>
                                            <button
                                                type="button"
                                                className={`rank-row ops-rank-btn ${journey?.global_id === p.global_id ? 'is-selected' : ''}`}
                                                onClick={() => lookup(p.global_id)}
                                                aria-label={`Trace ${p.global_id}`}
                                            >
                                                <span className="rank-index">{i + 1}</span>
                                                <PersonThumb gid={p.global_id} />
                                                <span className="ops-rank-main">
                                                    <span className="rank-name mono">{p.global_id}</span>
                                                    <span className="rank-sub">
                                                        Cam {cams.map(cam).join(', ')} · {p.views ?? 0} views{p.restored ? ' · restored' : ''}
                                                    </span>
                                                </span>
                                                <span className="rank-value ops-rank-time">{fmtTime(p.last_seen)}</span>
                                            </button>
                                        </li>
                                    );
                                })}
                            </ol>
                        )}
                    </div>
                </section>

                <section className="panel ops-panel" aria-labelledby="reid-journey">
                    <div className="panel-head">
                        <div>
                            <h2 className="panel-title" id="reid-journey">Journey</h2>
                            <p className="panel-sub">Camera and zone legs for one global ID.</p>
                        </div>
                    </div>
                    <div className="panel-body ops-col-body">
                        <form className="ops-inline-form" onSubmit={(e) => { e.preventDefault(); lookup(query); }}>
                            <label className="sr-only" htmlFor="reid-query">Global ID</label>
                            <input
                                id="reid-query"
                                className="form-input mono"
                                placeholder="PERSON-00001"
                                value={query}
                                onChange={(e) => setQuery(e.target.value)}
                            />
                            <button className="btn btn-primary" disabled={loading || !query.trim()}>
                                <Search size={16} strokeWidth={1.75} /> {loading ? 'Searching…' : 'Look up'}
                            </button>
                        </form>

                        {error && <div className="alert-banner danger">{error}</div>}

                        {journey ? (
                            <div className="ops-journey">
                                <div className="ops-person-head">
                                    <PersonThumb gid={journey.global_id} size="lg" />
                                    <div className="ops-person-head-main">
                                        <div className="osd-strip">
                                            <span><strong>{journey.global_id}</strong></span>
                                            <span>Total <strong>{Math.round((journey.total_duration || 0) / 60)} min</strong></span>
                                            <span>Zones <strong>{journey.zones_visited ?? '—'}</strong></span>
                                        </div>
                                        <button type="button" className="btn btn-secondary btn-sm" onClick={() => findSimilar(journey.global_id)} disabled={simLoading}>
                                            <ScanSearch size={14} strokeWidth={1.75} /> {simLoading ? 'Searching…' : 'Find similar people'}
                                        </button>
                                    </div>
                                </div>
                                {simError && <div className="alert-banner danger">{simError}</div>}
                                {similar ? (
                                    <div className="ops-similar">
                                        <h3 className="ops-similar-title">
                                            {similar.length ? 'Looks similar: other IDs that may be the same person' : 'Nobody else looks similar enough to be this person.'}
                                        </h3>
                                        {similar.length ? (
                                            <ol className="ops-similar-list">
                                                {similar.map((m) => (
                                                    <li key={m.global_id}>
                                                        <button type="button" className="ops-similar-row" onClick={() => lookup(m.global_id)} aria-label={`Trace ${m.global_id}`}>
                                                            <PersonThumb gid={m.global_id} />
                                                            <span className="ops-rank-main">
                                                                <span className="rank-name mono">{m.global_id}</span>
                                                                <span className="rank-sub">Cam {cam(m.camera_id)} · {m.timestamp ? new Date(m.timestamp).toLocaleTimeString() : '—'}</span>
                                                            </span>
                                                            <Confidence value={m.confidence} />
                                                        </button>
                                                    </li>
                                                ))}
                                            </ol>
                                        ) : null}
                                    </div>
                                ) : null}
                                {journeyLegs.length === 0 ? (
                                    <p className="ops-muted ops-note">No journey legs stored yet.</p>
                                ) : (
                                    <ol className="ops-legs">
                                        {journeyLegs.map((leg, i) => (
                                            <li key={i} className="ops-leg">
                                                <span className="ops-leg-cam mono">Cam {cam(leg.camera_id)}</span>
                                                <span className="ops-leg-zone">{leg.zone || '—'}</span>
                                                <span className="ops-leg-dur num">{Math.round(leg.duration || leg.dwell_time || 0)} s</span>
                                                <span className="ops-leg-time">
                                                    {leg.in_progress
                                                        ? <span className="pill pill-info">In view</span>
                                                        : (leg.timestamp ? new Date(leg.timestamp).toLocaleTimeString() : '')}
                                                </span>
                                            </li>
                                        ))}
                                    </ol>
                                )}
                            </div>
                        ) : !error ? (
                            <Empty title="No identity selected">Pick one from the list or enter a global ID.</Empty>
                        ) : null}
                    </div>
                </section>
            </div>

            <div className="ops-split">
                <section className="panel ops-panel" aria-labelledby="reid-matches">
                    <div className="panel-head">
                        <div>
                            <h2 className="panel-title" id="reid-matches">Camera transitions</h2>
                            <p className="panel-sub">Streamed live when a known person appears on another camera.</p>
                        </div>
                        <span className="count-badge">{matches.length}</span>
                    </div>
                    <div className="panel-body ops-col-body">
                        {matches.length === 0 ? (
                            <Empty title="Waiting for cross-camera matches">Matches stream in while two or more feeds are running.</Empty>
                        ) : (
                            <ol className="ops-transitions ops-scroll">
                                {matches.map((m, i) => (
                                    <li key={`${m.ts}-${i}`} className="ops-transition">
                                        <span className="ops-transition-id mono">{m.global_id}</span>
                                        <span className="ops-transition-path">
                                            <span className="ops-cam-tag">Cam {cam(m.previous_camera)}</span>
                                            <ArrowRight size={14} strokeWidth={1.75} aria-label="to" />
                                            <span className="ops-cam-tag">Cam {cam(m.current_camera)}</span>
                                            {m.reason && m.reason !== 'match' ? <span className="pill pill-neutral">{m.reason}</span> : null}
                                        </span>
                                        <Confidence value={m.similarity} />
                                        <span className="ops-transition-time">{new Date(m.ts).toLocaleTimeString()}</span>
                                    </li>
                                ))}
                            </ol>
                        )}
                    </div>
                </section>

                <section className="panel ops-panel" aria-labelledby="reid-active">
                    <div className="panel-head">
                        <div>
                            <h2 className="panel-title" id="reid-active">On camera now</h2>
                            <p className="panel-sub">Similarity at assignment</p>
                        </div>
                        <span className="count-badge">{activeList.length}</span>
                    </div>
                    <div className="panel-body ops-col-body">
                        {activeList.length === 0 ? (
                            <Empty title="Nobody on camera">Live tracks from the last 10 seconds show here.</Empty>
                        ) : (
                            <ul className="ops-active ops-scroll">
                                {activeList.map((p, i) => (
                                    <li key={`${p.global_id}-${p.camera_id}-${i}`}>
                                        <button type="button" className="ops-active-row" onClick={() => lookup(p.global_id)}>
                                            <PersonThumb gid={p.global_id} />
                                            <span className="ops-active-id">
                                                <span className="mono">{p.global_id}</span>
                                                <span className="ops-muted">Cam {cam(p.camera_id)}</span>
                                            </span>
                                            {p.confidence
                                                ? <Confidence value={p.confidence} />
                                                : <span className="pill pill-accent ops-new">New</span>}
                                        </button>
                                    </li>
                                ))}
                            </ul>
                        )}
                    </div>
                </section>
            </div>

            <div className="two-col ops-even">
                <section className="panel ops-panel" aria-labelledby="reid-expected">
                    <div className="panel-head">
                        <div>
                            <h2 className="panel-title" id="reid-expected">Expected arrivals</h2>
                            <p className="panel-sub">People who left a camera and are due on a linked one.</p>
                        </div>
                        <span className="count-badge">{expectedList.length}</span>
                    </div>
                    <div className="panel-body ops-col-body">
                        {expectedList.length === 0 ? (
                            <Empty title={topology?.active_links?.length ? 'Nobody in transit' : 'No camera links yet'}>
                                {topology?.active_links?.length ? null : 'Add links in Camera topology below.'}
                            </Empty>
                        ) : (
                            <div className="table-scroll">
                                <table className="data-table">
                                    <thead>
                                        <tr><th>Global ID</th><th>Route</th><th className="num">Window</th></tr>
                                    </thead>
                                    <tbody>
                                        {expectedList.map((e, i) => (
                                            <tr key={`${e.global_id}-${i}`}>
                                                <td className="mono">{e.global_id}</td>
                                                <td>Cam {cam(e.from_camera)} → Cam {cam(e.to_camera)}</td>
                                                <td className="num">{fmtTime(e.earliest)}–{fmtTime(e.latest)}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        )}
                    </div>
                </section>

                <section className="panel ops-panel" aria-labelledby="reid-health">
                    <div className="panel-head">
                        <div>
                            <h2 className="panel-title" id="reid-health">Matcher health</h2>
                            <p className="panel-sub">Coordinator counters and the crop quality gate.</p>
                        </div>
                    </div>
                    <div className="panel-body ops-col-body">
                        {!st ? (
                            <Empty title="Re-ID is not running">Counters appear once the pipeline starts.</Empty>
                        ) : (
                            <ul className="ops-status-list">
                                <Row label="Tracklets (pending)" value={`${st.tracklets} (${st.tracklets_pending})`} />
                                <Row label="New identities" value={st.created} />
                                <Row label="Matched existing" value={st.assigned_existing} />
                                <Row label="Fused (overlap)" value={`${st.fused} · fusion ${st.fusion}`} />
                                <Row label="Views added / rejected" value={`${st.views_added} / ${st.updates_rejected}`} />
                                <Row label="Topology links" value={st.topology_links} />
                                <Row label="Calibrated cameras" value={(st.calibrated_cameras || []).join(', ') || 'None'} />
                                <Row
                                    label="Crop gate"
                                    value={gateTotal
                                        ? Object.entries(gate).map(([k, v]) => `${k} ${v}`).join(' · ')
                                        : 'No crops yet'}
                                />
                            </ul>
                        )}
                    </div>
                </section>
            </div>

            <TopologyEditor topology={topology} onSaved={refreshTopology} />
        </div>
    );
}

function TopologyEditor({ topology, onSaved }) {
    const stored = topology?.links || [];
    const [draft, setDraft] = useState(null);
    const [form, setForm] = useState({ from: '', to: '', min_s: '0', max_s: '', overlapping: false, bidirectional: true });
    const [msg, setMsg] = useState(null);
    const [saving, setSaving] = useState(false);

    const links = draft ?? stored.map((l) => ({
        from: l.from, to: l.to,
        min_s: l.min_s, max_s: l.max_s, overlapping: !!l.overlapping, bidirectional: false,
    }));

    const add = (e) => {
        e.preventDefault();
        const from = Number(form.from); const to = Number(form.to);
        const minS = Number(form.min_s || 0);
        const maxS = Number(form.max_s || topology?.default_max_transit_s || 300);
        if (!from || !to || from === to) { setMsg({ kind: 'danger', text: 'Pick two different camera IDs.' }); return; }
        if (maxS < minS) { setMsg({ kind: 'danger', text: 'Max transit must be ≥ min transit.' }); return; }
        setDraft([...links, { from, to, min_s: minS, max_s: maxS, overlapping: form.overlapping, bidirectional: form.bidirectional }]);
        setMsg(null);
    };

    const save = async () => {
        setSaving(true); setMsg(null);
        try {
            const res = await api.put('/reid/topology', { links });
            setDraft(null);
            setMsg({ kind: 'success', text: `Saved. ${res.data.directed_links} directed links active.` });
            onSaved?.();
        } catch (err) {
            setMsg({ kind: 'danger', text: errText(err) });
        } finally { setSaving(false); }
    };

    return (
        <section className="panel ops-panel" aria-labelledby="reid-topology">
            <div className="panel-head">
                <div>
                    <h2 className="panel-title" id="reid-topology">Camera topology</h2>
                    <p className="panel-sub">
                        Allowed transit times between cameras. Without links, any pair is allowed within {topology?.default_max_transit_s ?? 300} s.
                    </p>
                </div>
                {draft !== null ? <span className="pill pill-warn">Unsaved changes</span> : null}
            </div>
            <div className="panel-body ops-topo">
                {links.length === 0 ? (
                    <p className="ops-muted ops-note">No links configured.</p>
                ) : (
                    <ul className="ops-links">
                        {links.map((l, i) => (
                            <li key={i} className="ops-link">
                                <span className="ops-transition-path">
                                    <span className="ops-cam-tag">Cam {cam(l.from)}</span>
                                    <span aria-label={l.bidirectional ? 'both ways' : 'to'}>{l.bidirectional ? '↔' : '→'}</span>
                                    <span className="ops-cam-tag">Cam {cam(l.to)}</span>
                                </span>
                                <span className="ops-link-meta num">{l.min_s}–{l.max_s} s</span>
                                <span className="ops-link-meta">{l.overlapping ? 'Overlapping views' : ''}</span>
                                <button
                                    type="button"
                                    className="fp-icon-btn"
                                    aria-label={`Remove link cam ${l.from} to cam ${l.to}`}
                                    onClick={() => setDraft(links.filter((_, j) => j !== i))}
                                >
                                    <Trash2 size={14} strokeWidth={1.75} />
                                </button>
                            </li>
                        ))}
                    </ul>
                )}

                <form onSubmit={add} className="ops-topo-form">
                    <div className="form-group">
                        <label className="form-label" htmlFor="topo-from">From cam</label>
                        <input id="topo-from" className="form-input" inputMode="numeric" value={form.from}
                            onChange={(e) => setForm({ ...form, from: e.target.value })} />
                    </div>
                    <div className="form-group">
                        <label className="form-label" htmlFor="topo-to">To cam</label>
                        <input id="topo-to" className="form-input" inputMode="numeric" value={form.to}
                            onChange={(e) => setForm({ ...form, to: e.target.value })} />
                    </div>
                    <div className="form-group">
                        <label className="form-label" htmlFor="topo-min">Min s</label>
                        <input id="topo-min" className="form-input" inputMode="numeric" value={form.min_s}
                            onChange={(e) => setForm({ ...form, min_s: e.target.value })} />
                    </div>
                    <div className="form-group">
                        <label className="form-label" htmlFor="topo-max">Max s</label>
                        <input id="topo-max" className="form-input" inputMode="numeric"
                            placeholder={String(topology?.default_max_transit_s ?? 300)} value={form.max_s}
                            onChange={(e) => setForm({ ...form, max_s: e.target.value })} />
                    </div>
                    <div className="ops-form-checks ops-topo-checks">
                        <label className="ops-check">
                            <input type="checkbox" checked={form.overlapping}
                                onChange={(e) => setForm({ ...form, overlapping: e.target.checked })} /> Overlapping
                        </label>
                        <label className="ops-check">
                            <input type="checkbox" checked={form.bidirectional}
                                onChange={(e) => setForm({ ...form, bidirectional: e.target.checked })} /> Both ways
                        </label>
                    </div>
                    <div className="ops-topo-actions">
                        <button className="btn btn-secondary" type="submit"><Plus size={16} strokeWidth={1.75} /> Add link</button>
                        {draft !== null && (
                            <button className="btn btn-ghost" type="button" onClick={() => setDraft(null)}>Discard</button>
                        )}
                        <button className="btn btn-primary" type="button" disabled={saving || draft === null} onClick={save}>
                            {saving ? 'Saving…' : 'Save topology'}
                        </button>
                    </div>
                </form>
                {msg && <div className={`alert-banner ${msg.kind}`}>{msg.text}</div>}
            </div>
        </section>
    );
}

function Row({ label, value }) {
    return (
        <li className="ops-status-row">
            <span className="ops-status-label">{label}</span>
            <span className="ops-status-value" title={String(value)}>{String(value)}</span>
        </li>
    );
}
