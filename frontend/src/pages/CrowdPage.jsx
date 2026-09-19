/**
 * OmniTrack AI — Footfall
 *
 * Reads /api/footfall, which is backed entirely by Postgres, so the page shows
 * history and survives a restart. It previously polled the live pipeline
 * snapshot and — whenever no job was running, which is most of the time — fell
 * back to six invented shopping-mall zones with made-up counts.
 *
 * A "person" here is a distinct track_id, not a detection row: someone standing
 * still produces hundreds of detections but is one visitor. Detections that
 * never got a track_id cannot be attributed to anyone, so they are surfaced
 * separately rather than quietly inflating the count or being dropped.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
    Area, AreaChart, Bar, BarChart, CartesianGrid, Cell,
    ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import {
    ArrowLeftRight, Clock, MapPin, RotateCcw, TrendingUp, UsersRound,
} from 'lucide-react';
import { footfallAPI } from '../services/api';

const EMPTY = { camera_id: '', zone: '', start: '', end: '', bucket: 'hour' };
const BAR_COLORS = ['#6172ff', '#a855f7', '#f97316', '#10b981', '#f43f5e', '#06b6d4', '#eab308'];

function fmtClock(iso, bucket) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (bucket === 'day') return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    if (bucket === 'minute') return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
    return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

function fmtDuration(seconds) {
    const s = Math.round(seconds || 0);
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    return `${m}m ${s % 60}s`;
}

export default function CrowdPage() {
    const [draft, setDraft] = useState(EMPTY);
    const [filters, setFilters] = useState(EMPTY);
    const [facets, setFacets] = useState(null);
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);

    useEffect(() => {
        footfallAPI.facets().then((r) => setFacets(r.data)).catch(() => {});
    }, []);

    const load = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const p = { bucket: filters.bucket };
            if (filters.camera_id) p.camera_id = Number(filters.camera_id);
            if (filters.zone) p.zone = filters.zone;
            if (filters.start) p.start = filters.start;
            if (filters.end) p.end = filters.end;
            const r = await footfallAPI.overview(p);
            setData(r.data);
        } catch (e) {
            setError(e?.response?.data?.detail || e.message);
            setData(null);
        } finally {
            setLoading(false);
        }
    }, [filters]);

    useEffect(() => { load(); }, [load]);

    const dirty = useMemo(
        () => Object.keys(EMPTY).some((k) => draft[k] !== EMPTY[k]),
        [draft],
    );

    const t = data?.totals;
    const timeline = (data?.timeline ?? []).map((r) => ({
        ...r, label: fmtClock(r.at, data?.bucket),
    }));
    const zones = (data?.zones ?? []).filter((z) => z.people > 0 || z.detections > 0);
    const dwell = data?.dwell ?? [];
    const crossings = data?.crossings ?? [];

    const crossTotals = crossings.reduce(
        (a, c) => ({
            in: a.in + c.in_count, out: a.out + c.out_count,
            side: a.side + c.left_count + c.right_count,
        }),
        { in: 0, out: 0, side: 0 },
    );

    return (
        <div className="page-scroll ff-page">
            <header className="ff-head">
                <div>
                    <span className="ff-eyebrow"><UsersRound size={12} aria-hidden /> Insights</span>
                    <h2 className="ff-title">Footfall</h2>
                    <p className="ff-sub">
                        How many people were seen, where they went and how long they stayed —
                        counted from stored detections, not live memory.
                    </p>
                </div>
            </header>

            {/* ── filters ─────────────────────────────────────────── */}
            <section className="ff-filters">
                <label className="ff-field">
                    <span>Camera</span>
                    <select
                        className="select"
                        value={draft.camera_id}
                        onChange={(e) => setDraft({ ...draft, camera_id: e.target.value })}
                    >
                        <option value="">All cameras</option>
                        {(facets?.cameras ?? []).map((c) => (
                            <option key={c} value={c}>Camera {c}</option>
                        ))}
                    </select>
                </label>
                <label className="ff-field">
                    <span>Zone</span>
                    <select
                        className="select"
                        value={draft.zone}
                        onChange={(e) => setDraft({ ...draft, zone: e.target.value })}
                    >
                        <option value="">All zones</option>
                        {(facets?.zones ?? []).map((z) => <option key={z} value={z}>{z}</option>)}
                    </select>
                </label>
                <label className="ff-field ff-field--time">
                    <span>From</span>
                    <input
                        className="input" type="datetime-local" step="1"
                        value={draft.start}
                        onChange={(e) => setDraft({ ...draft, start: e.target.value })}
                    />
                </label>
                <label className="ff-field ff-field--time">
                    <span>To</span>
                    <input
                        className="input" type="datetime-local" step="1"
                        value={draft.end}
                        onChange={(e) => setDraft({ ...draft, end: e.target.value })}
                    />
                </label>
                <label className="ff-field">
                    <span>Group by</span>
                    <select
                        className="select"
                        value={draft.bucket}
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
                            type="button"
                            className="ff-btn"
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
                <div className="ff-empty"><p>Loading footfall…</p></div>
            ) : !t || t.detections === 0 ? (
                <div className="ff-empty">
                    <span className="ff-empty__glyph"><UsersRound size={26} aria-hidden /></span>
                    <h3>No people recorded yet</h3>
                    <p>Run a job with the person class enabled and this fills in.</p>
                </div>
            ) : (
                <>
                    {/* ── headline ────────────────────────────────── */}
                    <div className="ff-stats">
                        <Stat
                            icon={UsersRound} accent="indigo"
                            label="People seen" value={t.people.toLocaleString()}
                            note={`${t.detections.toLocaleString()} detections`}
                        />
                        <Stat
                            icon={TrendingUp} accent="amber"
                            label="Peak" value={t.peak_people.toLocaleString()}
                            note={t.peak_bucket ? fmtClock(t.peak_bucket, data.bucket) : '—'}
                        />
                        <Stat
                            icon={MapPin} accent="cyan"
                            label="Busiest zone" value={t.busiest_zone ?? '—'}
                            note={`${t.busiest_zone_people.toLocaleString()} people`}
                        />
                        <Stat
                            icon={ArrowLeftRight} accent="rose"
                            label="Line crossings"
                            value={(crossTotals.in + crossTotals.out + crossTotals.side).toLocaleString()}
                            note={`${crossTotals.in} in · ${crossTotals.out} out`}
                        />
                    </div>

                    {/* ── timeline ────────────────────────────────── */}
                    <section className="ff-card">
                        <header className="ff-card__head">
                            <h3>People over time</h3>
                            <span>Distinct tracked people per {data.bucket}</span>
                        </header>
                        <div className="ff-chart">
                            <ResponsiveContainer width="100%" height={260}>
                                <AreaChart data={timeline} margin={{ top: 8, right: 12, left: -18, bottom: 0 }}>
                                    <defs>
                                        <linearGradient id="ffFill" x1="0" y1="0" x2="0" y2="1">
                                            <stop offset="0%" stopColor="#6172ff" stopOpacity={0.55} />
                                            <stop offset="100%" stopColor="#6172ff" stopOpacity={0.04} />
                                        </linearGradient>
                                    </defs>
                                    <CartesianGrid stroke="rgba(128,128,128,0.16)" vertical={false} />
                                    <XAxis dataKey="label" stroke="currentColor" fontSize={11} tickLine={false} />
                                    <YAxis stroke="currentColor" fontSize={11} tickLine={false} allowDecimals={false} />
                                    <Tooltip
                                        contentStyle={{
                                            background: 'var(--bg-card)',
                                            border: '1px solid var(--border)',
                                            borderRadius: 10, fontSize: 12,
                                        }}
                                    />
                                    <Area
                                        type="monotone" dataKey="people" name="People"
                                        stroke="#6172ff" strokeWidth={2} fill="url(#ffFill)"
                                    />
                                </AreaChart>
                            </ResponsiveContainer>
                        </div>
                    </section>

                    {/* ── zones ───────────────────────────────────── */}
                    <section className="ff-card">
                        <header className="ff-card__head">
                            <h3>People by zone</h3>
                            <span>{zones.length} zone{zones.length === 1 ? '' : 's'} with activity</span>
                        </header>
                        <div className="ff-chart">
                            <ResponsiveContainer width="100%" height={Math.max(180, zones.length * 42)}>
                                <BarChart
                                    data={zones} layout="vertical"
                                    margin={{ top: 4, right: 20, left: 12, bottom: 4 }}
                                >
                                    <CartesianGrid stroke="rgba(128,128,128,0.16)" horizontal={false} />
                                    <XAxis type="number" stroke="currentColor" fontSize={11} allowDecimals={false} />
                                    <YAxis
                                        type="category" dataKey="zone" width={96}
                                        stroke="currentColor" fontSize={11} tickLine={false}
                                    />
                                    <Tooltip
                                        contentStyle={{
                                            background: 'var(--bg-card)',
                                            border: '1px solid var(--border)',
                                            borderRadius: 10, fontSize: 12,
                                        }}
                                    />
                                    <Bar dataKey="people" name="People" radius={[0, 6, 6, 0]}>
                                        {zones.map((z, i) => (
                                            <Cell key={z.zone} fill={BAR_COLORS[i % BAR_COLORS.length]} />
                                        ))}
                                    </Bar>
                                </BarChart>
                            </ResponsiveContainer>
                        </div>
                        <table className="ff-table">
                            <thead>
                                <tr>
                                    <th>Zone</th><th>Camera</th><th>People</th>
                                    <th>Detections</th><th>First seen</th><th>Last seen</th>
                                </tr>
                            </thead>
                            <tbody>
                                {zones.map((z) => (
                                    <tr key={`${z.camera_id}-${z.zone}`}>
                                        <td className="ff-table__strong">{z.zone}</td>
                                        <td className="ff-table__dim">{z.camera_id}</td>
                                        <td>{z.people.toLocaleString()}</td>
                                        <td className="ff-table__dim">{z.detections.toLocaleString()}</td>
                                        <td className="ff-table__dim">{new Date(z.first_seen).toLocaleTimeString()}</td>
                                        <td className="ff-table__dim">{new Date(z.last_seen).toLocaleTimeString()}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                        {t.untracked_detections > 0 && (
                            <p className="ff-note">
                                {t.untracked_detections.toLocaleString()} detections had no track id and
                                cannot be attributed to a person, so they are excluded from the counts above.
                            </p>
                        )}
                    </section>

                    {/* ── dwell ───────────────────────────────────── */}
                    <section className="ff-card">
                        <header className="ff-card__head">
                            <h3><Clock size={13} aria-hidden /> Dwell time by region</h3>
                            <span>How long each tracked object stayed</span>
                        </header>
                        {dwell.length === 0 ? (
                            <p className="ff-note">
                                No dwell recorded — run a job with the ROI region activity and draw a region.
                            </p>
                        ) : (
                            <table className="ff-table">
                                <thead>
                                    <tr>
                                        <th>Region</th><th>Camera</th><th>Tracks</th>
                                        <th>Average</th><th>Longest</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {dwell.map((d) => (
                                        <tr key={`${d.camera_id}-${d.region_name}`}>
                                            <td className="ff-table__strong">{d.region_name}</td>
                                            <td className="ff-table__dim">{d.camera_id}</td>
                                            <td>{d.tracks.toLocaleString()}</td>
                                            <td>{fmtDuration(d.avg_dwell_s)}</td>
                                            <td className="ff-table__dim">{fmtDuration(d.max_dwell_s)}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        )}
                    </section>

                    {/* ── crossings ───────────────────────────────── */}
                    <section className="ff-card">
                        <header className="ff-card__head">
                            <h3><ArrowLeftRight size={13} aria-hidden /> Line crossings</h3>
                            <span>Directional counts per line</span>
                        </header>
                        {crossings.length === 0 ? (
                            <p className="ff-note">
                                No crossings recorded — run a job with the line passing activity and draw a line.
                            </p>
                        ) : (
                            <>
                                <table className="ff-table">
                                    <thead>
                                        <tr>
                                            <th>Line</th><th>Camera</th><th>Class</th>
                                            <th>In</th><th>Out</th><th>Tracks</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {crossings.map((c) => (
                                            <tr key={`${c.camera_id}-${c.region_name}-${c.class_name}`}>
                                                <td className="ff-table__strong">{c.region_name}</td>
                                                <td className="ff-table__dim">{c.camera_id}</td>
                                                <td><span className="badge">{c.class_name}</span></td>
                                                <td className="ff-in">{c.in_count.toLocaleString()}</td>
                                                <td className="ff-out">{c.out_count.toLocaleString()}</td>
                                                <td className="ff-table__dim">{c.tracks.toLocaleString()}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                                {!crossings.some((c) => c.class_name === 'person') && (
                                    <p className="ff-note">
                                        Every crossing on record so far is a vehicle — the jobs that drew
                                        lines ran on traffic footage. Run a line job on people footage and
                                        they appear here alongside these.
                                    </p>
                                )}
                            </>
                        )}
                    </section>
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
