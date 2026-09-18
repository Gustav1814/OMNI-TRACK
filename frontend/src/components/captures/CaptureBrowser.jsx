/**
 * OmniTrack AI — Capture browser
 *
 * Browses what detection PRODUCED (cropped objects and annotated clips written to
 * shared/ais1), as opposed to the source clips you upload — those stay on the
 * Recordings tab next door.
 *
 * Filters are populated from /api/artifacts/facets rather than hardcoded, so the
 * dropdowns only ever offer values that actually exist on disk.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
    Camera, ChevronLeft, ChevronRight, Filter, ImageOff, Layers, RotateCcw, X,
} from 'lucide-react';
import { artifactsAPI } from '../../services/api';

const PAGE_SIZE = 60;

const EMPTY_FILTERS = {
    kind: 'image',
    camera_id: '',
    zone: '',
    activity: '',
    class_name: '',
    job_id: '',
    start: '',
    end: '',
};

function formatBytes(n) {
    const kb = (n || 0) / 1024;
    return kb >= 1024 ? `${(kb / 1024).toFixed(1)} MB` : `${Math.round(kb)} KB`;
}

function formatWhen(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
        month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit',
    });
}

/**
 * A datetime-local value is wall-clock with no zone, but artifact timestamps are
 * UTC. Send it through as-is and let the backend read it as UTC, which matches
 * what the facet range displays.
 */
function toParam(localValue) {
    return localValue ? localValue : undefined;
}

export default function CaptureBrowser() {
    const [facets, setFacets] = useState(null);
    const [filters, setFilters] = useState(EMPTY_FILTERS);
    const [page, setPage] = useState(0);
    const [data, setData] = useState({ total: 0, items: [] });
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [selected, setSelected] = useState(null);

    // Facets once — the folder does not change while you are looking at it.
    useEffect(() => {
        let alive = true;
        artifactsAPI.facets()
            .then((r) => { if (alive) setFacets(r.data); })
            .catch((e) => { if (alive) setError(e?.response?.data?.detail || e.message); });
        return () => { alive = false; };
    }, []);

    const load = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const params = {
                kind: filters.kind || 'all',
                limit: PAGE_SIZE,
                offset: page * PAGE_SIZE,
            };
            if (filters.camera_id) params.camera_id = Number(filters.camera_id);
            if (filters.zone) params.zone = filters.zone;
            if (filters.activity) params.activity = filters.activity;
            if (filters.class_name) params.class_name = filters.class_name;
            if (filters.job_id) params.job_id = filters.job_id;
            const start = toParam(filters.start);
            const end = toParam(filters.end);
            if (start) params.start = start;
            if (end) params.end = end;

            const r = await artifactsAPI.list(params);
            setData(r.data);
        } catch (e) {
            setError(e?.response?.data?.detail || e.message);
            setData({ total: 0, items: [] });
        } finally {
            setLoading(false);
        }
    }, [filters, page]);

    useEffect(() => { load(); }, [load]);

    // Any filter change invalidates the current page number.
    const patch = (next) => { setFilters((f) => ({ ...f, ...next })); setPage(0); };
    const reset = () => { setFilters(EMPTY_FILTERS); setPage(0); };

    // One job_id can span several runs (stop/restart reuses it), so the raw list
    // repeats. Dedupe for the dropdown — filtering by id already covers every run.
    const jobIds = useMemo(
        () => Array.from(new Set((facets?.jobs ?? []).map((j) => j.job_id))),
        [facets],
    );

    const dirty = useMemo(
        () => Object.keys(EMPTY_FILTERS).some((k) => filters[k] !== EMPTY_FILTERS[k]),
        [filters],
    );

    const totalPages = Math.max(1, Math.ceil((data.total || 0) / PAGE_SIZE));
    const items = data.items || [];

    useEffect(() => {
        if (!selected) return undefined;
        const onKey = (e) => { if (e.key === 'Escape') setSelected(null); };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [selected]);

    return (
        <div className="cap">
            {/* ── filters ─────────────────────────────────────────── */}
            <div className="cap-filters">
                <span className="cap-filters__icon"><Filter size={14} aria-hidden /></span>

                <label className="cap-field">
                    <span>Type</span>
                    <select
                        className="select"
                        value={filters.kind}
                        onChange={(e) => {
                            const kind = e.target.value;
                            // Clips have no class, so a stale class filter would
                            // silently return nothing while its control is disabled.
                            patch(kind === 'clip' ? { kind, class_name: '' } : { kind });
                        }}
                    >
                        <option value="image">Images</option>
                        <option value="clip">Clips</option>
                        <option value="all">Both</option>
                    </select>
                </label>

                <label className="cap-field">
                    <span>Camera</span>
                    <select
                        className="select"
                        value={filters.camera_id}
                        onChange={(e) => patch({ camera_id: e.target.value })}
                    >
                        <option value="">Any</option>
                        {(facets?.cameras ?? []).map((c) => (
                            <option key={c} value={c}>Camera {c}</option>
                        ))}
                    </select>
                </label>

                <label className="cap-field">
                    <span>Location</span>
                    <select
                        className="select"
                        value={filters.zone}
                        onChange={(e) => patch({ zone: e.target.value })}
                    >
                        <option value="">Any</option>
                        {(facets?.zones ?? []).map((z) => <option key={z} value={z}>{z}</option>)}
                    </select>
                </label>

                <label className="cap-field">
                    <span>Activity</span>
                    <select
                        className="select"
                        value={filters.activity}
                        onChange={(e) => patch({ activity: e.target.value })}
                    >
                        <option value="">Any</option>
                        {(facets?.activities ?? []).map((a) => (
                            <option key={a} value={a}>{a.replace(/_/g, ' ')}</option>
                        ))}
                    </select>
                </label>

                <label className="cap-field">
                    <span>Object</span>
                    <select
                        className="select"
                        value={filters.class_name}
                        onChange={(e) => patch({ class_name: e.target.value })}
                        disabled={filters.kind === 'clip'}
                        title={filters.kind === 'clip' ? 'Clips cover every object, so they have no single class' : undefined}
                    >
                        <option value="">Any</option>
                        {(facets?.classes ?? []).map((c) => <option key={c} value={c}>{c}</option>)}
                    </select>
                </label>

                <label className="cap-field">
                    <span>Job</span>
                    <select
                        className="select"
                        value={filters.job_id}
                        onChange={(e) => patch({ job_id: e.target.value })}
                    >
                        <option value="">Any</option>
                        {jobIds.map((id) => <option key={id} value={id}>{id}</option>)}
                    </select>
                </label>

                <label className="cap-field cap-field--time">
                    <span>From</span>
                    <input
                        className="input"
                        type="datetime-local"
                        step="1"
                        value={filters.start}
                        onChange={(e) => patch({ start: e.target.value })}
                    />
                </label>

                <label className="cap-field cap-field--time">
                    <span>To</span>
                    <input
                        className="input"
                        type="datetime-local"
                        step="1"
                        value={filters.end}
                        onChange={(e) => patch({ end: e.target.value })}
                    />
                </label>

                {dirty && (
                    <button type="button" className="cap-reset" onClick={reset}>
                        <RotateCcw size={13} aria-hidden /> Reset
                    </button>
                )}

                {facets?.earliest && (
                    <span className="cap-range-hint">
                        Captures available {formatWhen(facets.earliest)} — {formatWhen(facets.latest)}
                    </span>
                )}
            </div>

            {/* ── result bar ──────────────────────────────────────── */}
            <div className="cap-bar">
                <span className="cap-bar__count">
                    <Layers size={13} aria-hidden />
                    {error ? (
                        <em>could not load</em>
                    ) : (
                        <>
                            <b>{(data.total ?? 0).toLocaleString()}</b>
                            <em>
                                {filters.kind === 'clip' ? 'clip' : 'capture'}
                                {data.total === 1 ? '' : 's'}
                            </em>
                        </>
                    )}
                </span>
                {totalPages > 1 && (
                    <span className="cap-pager">
                        <button
                            type="button"
                            disabled={page === 0 || loading}
                            onClick={() => setPage((p) => Math.max(0, p - 1))}
                            aria-label="Previous page"
                        >
                            <ChevronLeft size={14} />
                        </button>
                        <em>{page + 1} / {totalPages}</em>
                        <button
                            type="button"
                            disabled={page + 1 >= totalPages || loading}
                            onClick={() => setPage((p) => p + 1)}
                            aria-label="Next page"
                        >
                            <ChevronRight size={14} />
                        </button>
                    </span>
                )}
            </div>

            {error && <div className="alert-banner danger">{error}</div>}

            {/* ── grid ────────────────────────────────────────────── */}
            {loading && items.length === 0 ? (
                <div className="cap-empty"><p>Loading captures…</p></div>
            ) : items.length === 0 ? (
                <div className="cap-empty">
                    <span className="cap-empty__glyph"><ImageOff size={26} aria-hidden /></span>
                    <h3>Nothing matches those filters</h3>
                    <p>
                        {dirty
                            ? 'Try widening the time range or clearing a filter.'
                            : 'Captures appear here once a job has run.'}
                    </p>
                </div>
            ) : (
                <div className={`cap-grid${filters.kind === 'clip' ? ' cap-grid--clips' : ''}`}>
                    {items.map((it) => (
                        <button
                            type="button"
                            className="cap-card"
                            key={it.filename}
                            onClick={() => setSelected(it)}
                            title={it.filename}
                        >
                            <span className="cap-card__media">
                                {it.kind === 'clip' ? (
                                    <video src={artifactsAPI.fileUrl(it.filename)} muted preload="metadata" />
                                ) : (
                                    // Crops are small (some are 10x22px) — contain + pixelated
                                    // keeps them honest rather than smeared.
                                    <img
                                        src={artifactsAPI.fileUrl(it.filename)}
                                        alt={`${it.class_name ?? 'capture'} on camera ${it.camera_id}`}
                                        loading="lazy"
                                    />
                                )}
                            </span>
                            <span className="cap-card__body">
                                <span className="cap-card__title">
                                    {it.class_name ?? `clip ${it.frame_number}–${it.end_frame}`}
                                </span>
                                <span className="cap-card__meta">
                                    cam {it.camera_id} · {it.zone}
                                    {it.track_id != null && it.track_id >= 0 && ` · track ${it.track_id}`}
                                </span>
                                <span className="cap-card__time">{formatWhen(it.captured_at)}</span>
                            </span>
                        </button>
                    ))}
                </div>
            )}

            {/* ── lightbox ────────────────────────────────────────── */}
            {selected && (
                <div className="cap-light" role="dialog" aria-modal="true" onClick={() => setSelected(null)}>
                    <div className="cap-light__box" onClick={(e) => e.stopPropagation()}>
                        <header className="cap-light__head">
                            <span>
                                {selected.class_name ?? 'Clip'}
                                {selected.track_id != null && selected.track_id >= 0 && ` · track ${selected.track_id}`}
                            </span>
                            <button type="button" onClick={() => setSelected(null)} aria-label="Close">
                                <X size={16} />
                            </button>
                        </header>

                        <div className="cap-light__stage">
                            {selected.kind === 'clip' ? (
                                <video src={artifactsAPI.fileUrl(selected.filename)} controls autoPlay />
                            ) : (
                                <img src={artifactsAPI.fileUrl(selected.filename)} alt={selected.filename} />
                            )}
                        </div>

                        <dl className="cap-light__facts">
                            <div><dt><Camera size={12} aria-hidden /> Camera</dt><dd>{selected.camera_id}</dd></div>
                            <div><dt>Location</dt><dd>{selected.zone}</dd></div>
                            <div><dt>Activity</dt><dd>{selected.activity.replace(/_/g, ' ')}</dd></div>
                            <div><dt>Job</dt><dd>{selected.job_id ?? 'unattributed'}</dd></div>
                            <div><dt>Captured</dt><dd>{formatWhen(selected.captured_at)}</dd></div>
                            <div><dt>Frame</dt><dd>{selected.frame_number ?? '—'}</dd></div>
                            <div><dt>Size</dt><dd>{formatBytes(selected.size_bytes)}</dd></div>
                        </dl>

                        <footer className="cap-light__foot"><code>{selected.filename}</code></footer>
                    </div>
                </div>
            )}
        </div>
    );
}
