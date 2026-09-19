/**
 * OmniTrack AI — Queue Insights
 *
 * History from Postgres, current depth polled from the running pipeline. The
 * page previously read only the live snapshot, so it showed zeros whenever no
 * job was running — which, since no lane could ever be configured, was always.
 *
 * `time in lane` is entering the box to leaving it: wait and service together.
 * One box cannot separate them, so nothing here calls it service time.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
    Area, AreaChart, Bar, BarChart, CartesianGrid, Cell,
    ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { Clock, RotateCcw, ShoppingCart, TrendingUp, Users } from 'lucide-react';
import { queueAPI } from '../services/api';

const EMPTY = { camera_id: '', lane_id: '', start: '', end: '', bucket: 'minute' };
const LIVE_POLL_MS = 3000;
const BAR_COLORS = ['#6172ff', '#a855f7', '#f97316', '#10b981', '#f43f5e', '#06b6d4'];

/** Queue depth read as a colour, so a backed-up lane is obvious at a glance. */
function depthTone(n) {
    if (n >= 8) return 'crit';
    if (n >= 5) return 'high';
    if (n >= 2) return 'mid';
    return 'low';
}

function fmtDuration(seconds) {
    const s = Math.round(seconds || 0);
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    return `${m}m ${String(s % 60).padStart(2, '0')}s`;
}

function fmtClock(iso) {
    if (!iso) return '—';
    return new Date(iso).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

export default function CheckoutPage() {
    const [draft, setDraft] = useState(EMPTY);
    const [filters, setFilters] = useState(EMPTY);
    const [facets, setFacets] = useState(null);
    const [data, setData] = useState(null);
    const [live, setLive] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);

    useEffect(() => {
        queueAPI.facets().then((r) => setFacets(r.data)).catch(() => {});
    }, []);

    const load = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const p = { bucket: filters.bucket };
            if (filters.camera_id) p.camera_id = Number(filters.camera_id);
            if (filters.lane_id) p.lane_id = filters.lane_id;
            if (filters.start) p.start = filters.start;
            if (filters.end) p.end = filters.end;
            const r = await queueAPI.overview(p);
            setData(r.data);
        } catch (e) {
            setError(e?.response?.data?.detail || e.message);
            setData(null);
        } finally {
            setLoading(false);
        }
    }, [filters]);

    useEffect(() => { load(); }, [load]);

    // The live strip is the only part that needs polling; history does not move.
    useEffect(() => {
        let alive = true;
        const tick = () => queueAPI.live()
            .then((r) => alive && setLive(r.data))
            .catch(() => {});
        tick();
        const id = setInterval(tick, LIVE_POLL_MS);
        return () => { alive = false; clearInterval(id); };
    }, []);

    const dirty = useMemo(
        () => Object.keys(EMPTY).some((k) => draft[k] !== EMPTY[k]),
        [draft],
    );

    const t = data?.totals;
    const lanes = data?.lanes ?? [];
    const timeline = (data?.timeline ?? []).map((r) => ({ ...r, label: fmtClock(r.at) }));
    const dist = (data?.distribution ?? []).map((d) => ({
        ...d, label: d.to_s >= 300 ? '5m+' : `${d.from_s / 60 || 0}-${d.to_s / 60}m`,
    }));
    const liveLanes = live?.lanes ?? [];
    const hasHistory = Boolean(t && t.served > 0);

    return (
        <div className="page-scroll ff-page qi-page">
            <header className="ff-head">
                <div>
                    <span className="ff-eyebrow"><ShoppingCart size={12} aria-hidden /> Insights</span>
                    <h2 className="ff-title">Queue Insights</h2>
                    <p className="ff-sub">
                        How long the tills are, how long people spend in them, and which
                        lane is carrying the load.
                    </p>
                </div>
            </header>

            {/* ── live strip: only when lanes are actually configured ── */}
            {liveLanes.length > 0 && (
                <section className="qi-live">
                    <header className="qi-live__head">
                        <span className="qi-live__dot" aria-hidden />
                        Live now
                    </header>
                    <div className="qi-live__lanes">
                        {liveLanes.map((l) => (
                            <div key={l.lane_id} className={`qi-lane qi-lane--${depthTone(l.queue_length)}`}>
                                <span className="qi-lane__name">{l.lane_name}</span>
                                <span className="qi-lane__count">{l.queue_length}</span>
                                <span className="qi-lane__meta">
                                    {l.queue_length === 1 ? 'person' : 'people'}
                                    {l.wait_estimate_s > 0 && ` · ~${fmtDuration(l.wait_estimate_s)} wait`}
                                </span>
                            </div>
                        ))}
                    </div>
                </section>
            )}

            {/* ── filters ─────────────────────────────────────────── */}
            <section className="ff-filters">
                <label className="ff-field">
                    <span>Camera</span>
                    <select
                        className="select" value={draft.camera_id}
                        onChange={(e) => setDraft({ ...draft, camera_id: e.target.value })}
                    >
                        <option value="">All cameras</option>
                        {(facets?.cameras ?? []).map((c) => (
                            <option key={c} value={c}>Camera {c}</option>
                        ))}
                    </select>
                </label>
                <label className="ff-field">
                    <span>Lane</span>
                    <select
                        className="select" value={draft.lane_id}
                        onChange={(e) => setDraft({ ...draft, lane_id: e.target.value })}
                    >
                        <option value="">All lanes</option>
                        {(facets?.lanes ?? []).map((l) => (
                            <option key={l.lane_id} value={l.lane_id}>{l.lane_name}</option>
                        ))}
                    </select>
                </label>
                <label className="ff-field ff-field--time">
                    <span>From</span>
                    <input
                        className="input" type="datetime-local" step="1" value={draft.start}
                        onChange={(e) => setDraft({ ...draft, start: e.target.value })}
                    />
                </label>
                <label className="ff-field ff-field--time">
                    <span>To</span>
                    <input
                        className="input" type="datetime-local" step="1" value={draft.end}
                        onChange={(e) => setDraft({ ...draft, end: e.target.value })}
                    />
                </label>
                <label className="ff-field">
                    <span>Group by</span>
                    <select
                        className="select" value={draft.bucket}
                        onChange={(e) => setDraft({ ...draft, bucket: e.target.value })}
                    >
                        <option value="minute">Minute</option>
                        <option value="hour">Hour</option>
                        <option value="day">Day</option>
                    </select>
                </label>
                <div className="ff-filters__actions">
                    <button type="button" className="ff-btn ff-btn--primary" onClick={() => setFilters(draft)}>
                        Apply
                    </button>
                    {dirty && (
                        <button
                            type="button" className="ff-btn"
                            onClick={() => { setDraft(EMPTY); setFilters(EMPTY); }}
                        >
                            <RotateCcw size={13} aria-hidden /> Reset
                        </button>
                    )}
                </div>
                {facets?.earliest && (
                    <p className="ff-range">
                        Data available {new Date(facets.earliest).toLocaleString()} — {new Date(facets.latest).toLocaleString()}
                    </p>
                )}
            </section>

            {error && <div className="alert-banner danger">{error}</div>}

            {loading && !data ? (
                <div className="ff-empty"><p>Loading queue history…</p></div>
            ) : !hasHistory ? (
                <div className="ff-empty">
                    <span className="ff-empty__glyph"><ShoppingCart size={26} aria-hidden /></span>
                    <h3>No checkout data yet</h3>
                    <p>
                        Register a job with the <b>Checkout queue</b> activity and draw a box
                        over each till. Every person who enters and leaves a box is recorded here.
                    </p>
                </div>
            ) : (
                <>
                    <div className="ff-stats">
                        <Stat
                            icon={Users} accent="indigo" label="Customers served"
                            value={t.served.toLocaleString()}
                            note={`${t.lanes} lane${t.lanes === 1 ? '' : 's'}`}
                        />
                        <Stat
                            icon={Clock} accent="amber" label="Median time in lane"
                            value={fmtDuration(t.median_time_in_lane_s)}
                            note={`avg ${fmtDuration(t.avg_time_in_lane_s)}`}
                        />
                        <Stat
                            icon={TrendingUp} accent="rose" label="Peak queue"
                            value={t.peak_queue}
                            note="deepest recorded"
                        />
                        <Stat
                            icon={ShoppingCart} accent="cyan" label="Busiest lane"
                            value={t.busiest_lane ?? '—'}
                            note={`${t.busiest_lane_served.toLocaleString()} served`}
                        />
                    </div>

                    <section className="ff-card">
                        <header className="ff-card__head">
                            <h3>Queue depth over time</h3>
                            <span>Average across lanes per {data.bucket}</span>
                        </header>
                        <div className="ff-chart">
                            <ResponsiveContainer width="100%" height={250}>
                                <AreaChart data={timeline} margin={{ top: 8, right: 12, left: -18, bottom: 0 }}>
                                    <defs>
                                        <linearGradient id="qiFill" x1="0" y1="0" x2="0" y2="1">
                                            <stop offset="0%" stopColor="#f97316" stopOpacity={0.5} />
                                            <stop offset="100%" stopColor="#f97316" stopOpacity={0.04} />
                                        </linearGradient>
                                    </defs>
                                    <CartesianGrid stroke="rgba(128,128,128,0.16)" vertical={false} />
                                    <XAxis dataKey="label" stroke="currentColor" fontSize={11} tickLine={false} />
                                    <YAxis stroke="currentColor" fontSize={11} tickLine={false} allowDecimals={false} />
                                    <Tooltip contentStyle={{
                                        background: 'var(--bg-card)', border: '1px solid var(--border)',
                                        borderRadius: 10, fontSize: 12,
                                    }} />
                                    <Area
                                        type="monotone" dataKey="avg_queue" name="Avg queue"
                                        stroke="#f97316" strokeWidth={2} fill="url(#qiFill)"
                                    />
                                </AreaChart>
                            </ResponsiveContainer>
                        </div>
                    </section>

                    <section className="ff-card">
                        <header className="ff-card__head">
                            <h3>By lane</h3>
                            <span>Median is the fairer figure — one abandoned trolley skews an average</span>
                        </header>
                        <table className="ff-table">
                            <thead>
                                <tr>
                                    <th>Lane</th><th>Camera</th><th>Served</th>
                                    <th>Median</th><th>Average</th><th>Longest</th>
                                    <th>Peak queue</th><th>Per hour</th>
                                </tr>
                            </thead>
                            <tbody>
                                {lanes.map((l) => (
                                    <tr key={l.lane_id}>
                                        <td className="ff-table__strong">{l.lane_name}</td>
                                        <td className="ff-table__dim">{l.camera_id}</td>
                                        <td>{l.served.toLocaleString()}</td>
                                        <td>{fmtDuration(l.median_time_in_lane_s)}</td>
                                        <td className="ff-table__dim">{fmtDuration(l.avg_time_in_lane_s)}</td>
                                        <td className="ff-table__dim">{fmtDuration(l.max_time_in_lane_s)}</td>
                                        <td>{l.peak_queue}</td>
                                        <td className="ff-table__dim">{l.throughput_per_hour}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </section>

                    {dist.length > 0 && (
                        <section className="ff-card">
                            <header className="ff-card__head">
                                <h3>How long people spent in a lane</h3>
                                <span>Shows a steadily slow lane apart from an occasionally stuck one</span>
                            </header>
                            <div className="ff-chart">
                                <ResponsiveContainer width="100%" height={210}>
                                    <BarChart data={dist} margin={{ top: 8, right: 12, left: -18, bottom: 0 }}>
                                        <CartesianGrid stroke="rgba(128,128,128,0.16)" vertical={false} />
                                        <XAxis dataKey="label" stroke="currentColor" fontSize={11} tickLine={false} />
                                        <YAxis stroke="currentColor" fontSize={11} tickLine={false} allowDecimals={false} />
                                        <Tooltip contentStyle={{
                                            background: 'var(--bg-card)', border: '1px solid var(--border)',
                                            borderRadius: 10, fontSize: 12,
                                        }} />
                                        <Bar dataKey="count" name="Customers" radius={[6, 6, 0, 0]}>
                                            {dist.map((d, i) => (
                                                <Cell key={d.label} fill={BAR_COLORS[i % BAR_COLORS.length]} />
                                            ))}
                                        </Bar>
                                    </BarChart>
                                </ResponsiveContainer>
                            </div>
                        </section>
                    )}

                    <p className="ff-note qi-footnote">
                        Time in lane is measured from entering the box to leaving it, so it
                        covers waiting and being served together — a single region cannot
                        tell them apart.
                    </p>
                </>
            )}
        </div>
    );
}

function Stat({ icon: Icon, label, value, note, accent }) {
    return (
        <div className={`ff-stat ff-stat--${accent}`}>
            <span className="ff-stat__icon"><Icon size={16} aria-hidden /></span>
            <span className="ff-stat__label">{label}</span>
            <span className="ff-stat__value">{value}</span>
            {note && <span className="ff-stat__note">{note}</span>}
        </div>
    );
}
