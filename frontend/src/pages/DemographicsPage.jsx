/**
 * OmniTrack AI — Audience Mix
 *
 * Reads /api/demographics, which is backed entirely by Postgres, so the page
 * shows history and survives a restart. It previously polled an endpoint whose
 * cold-start fallback returned a hand-written age curve and a 72/66 gender
 * split — 138 people who did not exist, and, since nothing ever wrote the
 * table, the only thing it could ever show.
 *
 * A "visitor" here is one tracked person, not one observation. DeepFace's age
 * estimate moves several years between consecutive frames of the same face, so
 * the backend banks every sample against the person's track and keeps the
 * median. Faces it could not tie to anyone are reported separately rather than
 * quietly inflating the count.
 *
 * Shares are suppressed below the sample size the API names. "62% female" out
 * of eight faces is noise presented as fact.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
    Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend,
    Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import {
    CalendarClock, ChartPie, Info, MapPin, RotateCcw, ScanFace, Smile, UsersRound,
} from 'lucide-react';
import { demographicsAPI } from '../services/api';

const EMPTY = { camera_id: '', zone: '', start: '', end: '', bucket: 'hour' };

const MALE = '#4f8bff';
const FEMALE = '#f472b6';
const UNKNOWN = '#8b93a7';

/* Oldest band darkest — an age axis reads better as one ramp than as a rainbow. */
const AGE_COLORS = ['#a5d8ff', '#6fb4ff', '#4f8bff', '#6172ff', '#7c4ddb', '#5b2ea6'];

const MOOD_COLOR = {
    happy: '#10b981', surprise: '#06b6d4', neutral: '#8b93a7',
    sad: '#6172ff', angry: '#f43f5e', fear: '#a855f7', disgust: '#f97316',
};

function fmtClock(iso, bucket) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (bucket === 'day') return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

const pct = (n, total) => (total > 0 ? Math.round((n / total) * 100) : 0);

export default function DemographicsPage() {
    const [draft, setDraft] = useState(EMPTY);
    const [filters, setFilters] = useState(EMPTY);
    const [facets, setFacets] = useState(null);
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);

    useEffect(() => {
        demographicsAPI.facets().then((r) => setFacets(r.data)).catch(() => {});
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
            const r = await demographicsAPI.overview(p);
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
    const ageGroups = data?.age_groups ?? [];
    const minSample = data?.min_sample_for_percentages ?? 30;
    const reportable = !!t?.reportable;

    /* Males negative so the bars diverge from a shared zero — the axis is
       relabelled back to positive numbers below. */
    const pyramid = (data?.pyramid ?? []).map((r) => ({
        ...r, maleNeg: -r.male,
    }));
    /* A symmetric axis, so one side is not silently scaled against the other:
       with an auto domain, three men and no women fills the plot and reads as
       if the chart were full. */
    const pyramidMax = Math.max(1, ...pyramid.map((r) => Math.max(r.male, r.female)));

    const genderDist = data?.gender_distribution ?? {};
    const genderTotal = Object.values(genderDist).reduce((a, b) => a + b, 0);
    const genderChart = ['male', 'female', 'unknown']
        .filter((k) => genderDist[k])
        .map((k) => ({ name: k, value: genderDist[k] }));

    const ageDist = data?.age_distribution ?? {};
    const ageChart = ageGroups
        .filter((g) => ageDist[g])
        .map((g, i) => ({ name: g, count: ageDist[g], fill: AGE_COLORS[i % AGE_COLORS.length] }));

    const timeline = (data?.timeline ?? []).map((r) => ({
        ...r, label: fmtClock(r.at, data?.bucket),
    }));
    const timelineBands = ageGroups.filter((g) => timeline.some((r) => r[g]));

    const zones = data?.zones ?? [];
    const moods = data?.moods ?? [];
    const moodTotal = moods.reduce((a, m) => a + m.count, 0);

    return (
        <div className="page-scroll ff-page dm-page">
            <header className="ff-head">
                <div>
                    <span className="ff-eyebrow"><ScanFace size={12} aria-hidden /> Insights</span>
                    <h2 className="ff-title">Audience Mix</h2>
                    <p className="ff-sub">
                        Who is in the store — estimated age and gender per visitor, read from
                        faces the cameras actually saw. One row per person, not per frame.
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
                <div className="ff-empty"><p>Loading audience mix…</p></div>
            ) : !t || t.visitors === 0 ? (
                <EmptyState unattributed={t?.unattributed_faces ?? 0} />
            ) : (
                <>
                    {/* ── how much this is based on ───────────────── */}
                    <div className={`dm-basis ${reportable ? '' : 'dm-basis--thin'}`}>
                        <Info size={15} aria-hidden />
                        <p>
                            Based on <strong>{t.faces_sampled.toLocaleString()}</strong> face
                            {t.faces_sampled === 1 ? '' : 's'} from{' '}
                            <strong>{t.visitors.toLocaleString()}</strong> visitor
                            {t.visitors === 1 ? '' : 's'}
                            {t.unattributed_faces > 0 && (
                                <>, plus {t.unattributed_faces.toLocaleString()} face
                                    {t.unattributed_faces === 1 ? '' : 's'} that could not be matched
                                    to a tracked person</>
                            )}.
                            {!reportable && (
                                <> Percentages are hidden below {minSample} visitors — at this
                                    sample size a share says more about the sample than the store.</>
                            )}
                            {t.visitors > t.with_age && (
                                <>
                                    {' '}
                                    <strong>{(t.visitors - t.with_age).toLocaleString()}</strong> of
                                    them were too far from the camera to estimate an age — a wide
                                    view puts faces at a couple of dozen pixels, which is below what
                                    any age model can read.
                                </>
                            )}
                        </p>
                    </div>

                    {/* ── headline ────────────────────────────────── */}
                    <div className="ff-stats">
                        <Stat
                            icon={UsersRound} accent="indigo"
                            label="Visitors analysed" value={t.visitors.toLocaleString()}
                            note={`${t.faces_sampled.toLocaleString()} faces sampled`}
                        />
                        <Stat
                            icon={CalendarClock} accent="cyan"
                            label="Median age"
                            value={t.median_age != null ? Math.round(t.median_age) : '—'}
                            note={`${t.with_age.toLocaleString()} of ${t.visitors.toLocaleString()} gave an age`}
                        />
                        <Stat
                            icon={ChartPie} accent="rose"
                            label="Gender split"
                            value={
                                reportable && genderTotal
                                    ? `${pct(genderDist.male || 0, genderTotal)}% / ${pct(genderDist.female || 0, genderTotal)}%`
                                    : `${genderDist.male || 0} / ${genderDist.female || 0}`
                            }
                            note={reportable ? 'male / female' : 'male / female (counts)'}
                        />
                        <Stat
                            icon={MapPin} accent="amber"
                            label="Busiest zone"
                            value={zones[0]?.zone ?? '—'}
                            note={zones[0] ? `${zones[0].visitors.toLocaleString()} visitors` : '—'}
                        />
                    </div>

                    {/* ── pyramid ─────────────────────────────────── */}
                    <section className="ff-card">
                        <header className="ff-card__head">
                            <h3><ChartPie size={15} aria-hidden /> Age and gender</h3>
                            <span>Visitors per band · male left, female right</span>
                        </header>
                        {pyramid.length === 0 ? (
                            <p className="ff-note">No visitor produced a usable age yet.</p>
                        ) : (
                            <div className="ff-chart">
                                <ResponsiveContainer width="100%" height={Math.max(220, pyramid.length * 46)}>
                                    <BarChart
                                        data={pyramid} layout="vertical" stackOffset="sign"
                                        margin={{ top: 4, right: 24, left: 12, bottom: 4 }}
                                    >
                                        <CartesianGrid stroke="rgba(128,128,128,0.16)" horizontal={false} />
                                        <XAxis
                                            type="number" stroke="currentColor" fontSize={11}
                                            allowDecimals={false}
                                            domain={[-pyramidMax, pyramidMax]}
                                            tickFormatter={(v) => Math.abs(v)}
                                        />
                                        <YAxis
                                            type="category" dataKey="age_group" width={62}
                                            stroke="currentColor" fontSize={11} tickLine={false}
                                        />
                                        <Tooltip
                                            cursor={{ fill: 'rgba(128,128,128,0.08)' }}
                                            contentStyle={{
                                                background: 'var(--bg-card)',
                                                border: '1px solid var(--border)',
                                                borderRadius: 10, fontSize: 12,
                                            }}
                                            formatter={(v, name) => [Math.abs(v), name]}
                                        />
                                        <Legend iconType="circle" wrapperStyle={{ fontSize: 12 }} />
                                        <Bar dataKey="maleNeg" name="Male" fill={MALE} stackId="a"
                                             radius={[4, 0, 0, 4]} maxBarSize={30} />
                                        <Bar dataKey="female" name="Female" fill={FEMALE} stackId="a"
                                             radius={[0, 4, 4, 0]} maxBarSize={30} />
                                    </BarChart>
                                </ResponsiveContainer>
                            </div>
                        )}
                        {pyramid.some((r) => r.unknown > 0) && (
                            <p className="ff-note">
                                {pyramid.reduce((a, r) => a + r.unknown, 0).toLocaleString()} visitor(s)
                                gave an age but no confident gender, so they appear in the age bands
                                below but not in the bars above.
                            </p>
                        )}
                    </section>

                    <div className="dm-split">
                        {/* ── age bands (includes unknown gender) ─── */}
                        <section className="ff-card">
                            <header className="ff-card__head">
                                <h3>Age bands</h3>
                                <span>Every visitor with an age</span>
                            </header>
                            <div className="ff-chart">
                                <ResponsiveContainer width="100%" height={240}>
                                    <BarChart data={ageChart} margin={{ top: 8, right: 12, left: -20, bottom: 0 }}>
                                        <CartesianGrid stroke="rgba(128,128,128,0.16)" vertical={false} />
                                        <XAxis dataKey="name" stroke="currentColor" fontSize={11} tickLine={false} />
                                        <YAxis stroke="currentColor" fontSize={11} tickLine={false} allowDecimals={false} />
                                        <Tooltip
                                            cursor={{ fill: 'rgba(128,128,128,0.08)' }}
                                            contentStyle={{
                                                background: 'var(--bg-card)',
                                                border: '1px solid var(--border)',
                                                borderRadius: 10, fontSize: 12,
                                            }}
                                        />
                                        <Bar dataKey="count" name="Visitors" radius={[6, 6, 0, 0]} maxBarSize={64}>
                                            {ageChart.map((d) => <Cell key={d.name} fill={d.fill} />)}
                                        </Bar>
                                    </BarChart>
                                </ResponsiveContainer>
                            </div>
                        </section>

                        {/* ── gender ──────────────────────────────── */}
                        <section className="ff-card">
                            <header className="ff-card__head">
                                <h3>Gender</h3>
                                <span>n = {genderTotal.toLocaleString()}</span>
                            </header>
                            <div className="ff-chart">
                                <ResponsiveContainer width="100%" height={240}>
                                    <PieChart>
                                        <Pie
                                            data={genderChart} dataKey="value" nameKey="name"
                                            cx="50%" cy="50%" innerRadius={52} outerRadius={86}
                                            paddingAngle={2}
                                        >
                                            {genderChart.map((d) => (
                                                <Cell
                                                    key={d.name}
                                                    fill={d.name === 'male' ? MALE : d.name === 'female' ? FEMALE : UNKNOWN}
                                                />
                                            ))}
                                        </Pie>
                                        <Tooltip
                                            contentStyle={{
                                                background: 'var(--bg-card)',
                                                border: '1px solid var(--border)',
                                                borderRadius: 10, fontSize: 12,
                                            }}
                                        />
                                        <Legend iconType="circle" wrapperStyle={{ fontSize: 12 }} />
                                    </PieChart>
                                </ResponsiveContainer>
                            </div>
                            {genderDist.unknown > 0 && (
                                <p className="ff-note">
                                    “Unknown” is a face the model saw but was too small or too
                                    turned away to call — not a third category.
                                </p>
                            )}
                        </section>
                    </div>

                    {/* ── who visits when ─────────────────────────── */}
                    <section className="ff-card">
                        <header className="ff-card__head">
                            <h3>Who visits when</h3>
                            <span>Visitors per {data.bucket}, by age band</span>
                        </header>
                        {timelineBands.length === 0 ? (
                            <p className="ff-note">Not enough timed samples to draw a pattern yet.</p>
                        ) : (
                            <div className="ff-chart">
                                <ResponsiveContainer width="100%" height={270}>
                                    <AreaChart data={timeline} margin={{ top: 8, right: 12, left: -18, bottom: 0 }}>
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
                                        <Legend iconType="circle" wrapperStyle={{ fontSize: 12 }} />
                                        {timelineBands.map((g, i) => (
                                            <Area
                                                key={g} type="monotone" dataKey={g} name={g} stackId="1"
                                                stroke={AGE_COLORS[ageGroups.indexOf(g) % AGE_COLORS.length]}
                                                fill={AGE_COLORS[ageGroups.indexOf(g) % AGE_COLORS.length]}
                                                fillOpacity={0.55} strokeWidth={1.5}
                                            />
                                        ))}
                                    </AreaChart>
                                </ResponsiveContainer>
                            </div>
                        )}
                        <p className="ff-note">
                            Morning and evening rarely share an audience. This is the chart that
                            says whether staffing or promotions should differ by time of day.
                        </p>
                    </section>

                    {/* ── mood ────────────────────────────────────── */}
                    {moods.length > 0 && (
                        <section className="ff-card">
                            <header className="ff-card__head">
                                <h3><Smile size={15} aria-hidden /> Mood on arrival</h3>
                                <span>Most common expression per visitor</span>
                            </header>
                            <div className="dm-moods">
                                {moods.map((m) => (
                                    <div className="dm-mood" key={m.emotion}>
                                        <span
                                            className="dm-mood__swatch"
                                            style={{ background: MOOD_COLOR[m.emotion] || UNKNOWN }}
                                        />
                                        <span className="dm-mood__name">{m.emotion}</span>
                                        <span className="dm-mood__count">{m.count.toLocaleString()}</span>
                                        <span className="dm-mood__bar">
                                            <i style={{
                                                width: `${pct(m.count, moodTotal)}%`,
                                                background: MOOD_COLOR[m.emotion] || UNKNOWN,
                                            }} />
                                        </span>
                                    </div>
                                ))}
                            </div>
                            <p className="ff-note">
                                Recorded on the same face read as age and gender. This is the only
                                emotion history the system keeps — the live Emotion page blanks
                                when the pipeline stops.
                            </p>
                        </section>
                    )}

                    {/* ── zones ───────────────────────────────────── */}
                    <section className="ff-card">
                        <header className="ff-card__head">
                            <h3><MapPin size={15} aria-hidden /> By zone</h3>
                            <span>{zones.length} zone{zones.length === 1 ? '' : 's'} with faces</span>
                        </header>
                        <table className="ff-table">
                            <thead>
                                <tr>
                                    <th>Zone</th><th>Camera</th><th>Visitors</th>
                                    <th>Median age</th><th>Male</th><th>Female</th><th>Unread</th>
                                </tr>
                            </thead>
                            <tbody>
                                {zones.map((z) => (
                                    <tr key={`${z.camera_id}-${z.zone}`}>
                                        <td className="ff-table__strong">{z.zone}</td>
                                        <td className="ff-table__dim">{z.camera_id}</td>
                                        <td>{z.visitors.toLocaleString()}</td>
                                        <td>{z.median_age != null ? Math.round(z.median_age) : '—'}</td>
                                        <td style={{ color: MALE, fontWeight: 600 }}>{z.male}</td>
                                        <td style={{ color: FEMALE, fontWeight: 600 }}>{z.female}</td>
                                        <td className="ff-table__dim">{z.unknown_gender}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                        <p className="ff-note">{data.age_caveat}</p>
                    </section>
                </>
            )}
        </div>
    );
}

function EmptyState({ unattributed }) {
    return (
        <div className="ff-empty">
            <span className="ff-empty__glyph"><ScanFace size={26} aria-hidden /></span>
            <h3>No faces analysed yet</h3>
            {unattributed > 0 ? (
                <p>
                    {unattributed.toLocaleString()} face(s) were read but could not be matched to a
                    tracked person. Enable the person class on the job so each face can be
                    attributed to someone.
                </p>
            ) : (
                <p>
                    Run a job on footage where faces are visible to the camera. A ceiling-mounted
                    view shows the tops of heads, and no face model can read an age from that.
                </p>
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
