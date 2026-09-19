/**
 * OmniTrack AI — Alerts
 *
 * Ported from VisRax's AlertsPage: a filter panel, Live/History tabs, a table
 * and an evidence modal.
 *
 * Two departures, both because of what OmniTrack actually has:
 *
 *   Live tab   VisRax streams alerts over SSE. OmniTrack has no alert stream,
 *              so "Live" polls for unacknowledged alerts every few seconds.
 *              Same intent — the things still needing attention — without
 *              pretending a push channel exists.
 *
 *   Filters    Populated from /api/alerts/facets rather than hardcoded, so a
 *              dropdown never offers a value that would return nothing.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Bell, ChevronLeft, ChevronRight, RotateCcw } from 'lucide-react';
import { alertsAPI } from '../services/api';
import AlertList from '../components/alerts/AlertList';
import AlertImagesModal from '../components/alerts/AlertImagesModal';

const PAGE_SIZES = [10, 25, 50, 100, 200];
const LIVE_POLL_MS = 5000;

const EMPTY = {
    camera_id: '', zone: '', job_id: '', alert_id: '',
    start: '', end: '', sort: 'descending',
};

export default function AlertsPage() {
    const [tab, setTab] = useState('live');
    const [draft, setDraft] = useState(EMPTY);        // what is typed
    const [filters, setFilters] = useState(EMPTY);    // what is applied
    const [page, setPage] = useState(1);
    const [pageSize, setPageSize] = useState(25);

    const [facets, setFacets] = useState(null);
    const [history, setHistory] = useState({ count: 0, items: [] });
    const [live, setLive] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);

    const [open, setOpen] = useState(null);
    const [acking, setAcking] = useState(null);

    const loadFacets = useCallback(() => {
        alertsAPI.facets()
            .then((r) => setFacets(r.data))
            .catch(() => { /* filters degrade to free text; not worth an error banner */ });
    }, []);

    useEffect(() => { loadFacets(); }, [loadFacets]);

    const params = useCallback((extra = {}) => {
        const p = { sort: filters.sort, ...extra };
        if (filters.camera_id) p.camera_id = Number(filters.camera_id);
        if (filters.zone) p.zone = filters.zone;
        if (filters.job_id) p.job_id = filters.job_id;
        if (filters.alert_id) p.alert_id = filters.alert_id;
        if (filters.start) p.start = filters.start;
        if (filters.end) p.end = filters.end;
        return p;
    }, [filters]);

    // History: one page at a time.
    const loadHistory = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const r = await alertsAPI.list(params({
                limit: pageSize, offset: (page - 1) * pageSize,
            }));
            setHistory(r.data);
        } catch (e) {
            setError(e?.response?.data?.detail || e.message);
            setHistory({ count: 0, items: [] });
        } finally {
            setLoading(false);
        }
    }, [params, page, pageSize]);

    // Live: the unacknowledged backlog, refreshed on a timer.
    const loadLive = useCallback(async () => {
        try {
            const r = await alertsAPI.list(params({ acknowledged: false, limit: 100 }));
            setLive(r.data.items ?? []);
            setError(null);
        } catch (e) {
            setError(e?.response?.data?.detail || e.message);
        } finally {
            setLoading(false);
        }
    }, [params]);

    useEffect(() => {
        if (tab !== 'history') return undefined;
        loadHistory();
        return undefined;
    }, [tab, loadHistory]);

    useEffect(() => {
        if (tab !== 'live') return undefined;
        loadLive();
        const id = setInterval(loadLive, LIVE_POLL_MS);
        return () => clearInterval(id);
    }, [tab, loadLive]);

    const apply = () => { setFilters(draft); setPage(1); };
    const reset = () => { setDraft(EMPTY); setFilters(EMPTY); setPage(1); };
    const dirty = useMemo(
        () => Object.keys(EMPTY).some((k) => draft[k] !== EMPTY[k]),
        [draft],
    );

    const ack = async (alertId) => {
        setAcking(alertId);
        try {
            await alertsAPI.ack(alertId);
            const patch = (a) => (a.alert_id === alertId ? { ...a, acknowledged: true } : a);
            setHistory((h) => ({ ...h, items: (h.items ?? []).map(patch) }));
            // An acknowledged alert leaves the live view by definition.
            setLive((l) => l.filter((a) => a.alert_id !== alertId));
            setOpen((o) => (o && o.alert_id === alertId ? { ...o, acknowledged: true } : o));
            loadFacets();
        } catch (e) {
            setError(e?.response?.data?.detail || e.message);
        } finally {
            setAcking(null);
        }
    };

    const items = tab === 'live' ? live : (history.items ?? []);
    const total = history.count ?? 0;
    const totalPages = Math.max(1, Math.ceil(total / pageSize));

    const field = (key, label, node) => (
        <label className="alerts-field" key={key}>
            <span>{label}</span>
            {node}
        </label>
    );

    return (
        <div className="page-scroll alerts-page">
            <header className="alerts-head">
                <div>
                    <span className="alerts-eyebrow">
                        <Bell size={12} aria-hidden /> Monitor
                    </span>
                    <h2 className="alerts-title">Alerts</h2>
                    <p className="alerts-sub">
                        Everything your cameras flagged — watch what still needs attention,
                        or search back through history.
                    </p>
                </div>
                {facets && (
                    <div className="alerts-counts">
                        <span className="alerts-counts__item">
                            <b>{facets.unacknowledged}</b><em>unacknowledged</em>
                        </span>
                        <span className="alerts-counts__dot" aria-hidden />
                        <span className="alerts-counts__item">
                            <b>{facets.total}</b><em>total</em>
                        </span>
                    </div>
                )}
            </header>

            {/* ── filters ─────────────────────────────────────────── */}
            <section className="alerts-filters">
                <header className="alerts-filters__head">
                    <Bell size={14} aria-hidden />
                    <div>
                        <h3>Find alerts</h3>
                        <p>Narrow by camera, location, job or time — or just scroll the list.</p>
                    </div>
                </header>

                <div className="alerts-filters__grid">
                    {field('camera', 'Camera', (
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
                    ))}
                    {field('zone', 'Location', (
                        <select
                            className="select"
                            value={draft.zone}
                            onChange={(e) => setDraft({ ...draft, zone: e.target.value })}
                        >
                            <option value="">All locations</option>
                            {(facets?.zones ?? []).map((z) => <option key={z} value={z}>{z}</option>)}
                        </select>
                    ))}
                    {field('job', 'Camera job', (
                        <select
                            className="select"
                            value={draft.job_id}
                            onChange={(e) => setDraft({ ...draft, job_id: e.target.value })}
                        >
                            <option value="">All jobs</option>
                            {(facets?.jobs ?? []).map((j) => <option key={j} value={j}>{j}</option>)}
                        </select>
                    ))}
                    {field('alertId', 'Alert number', (
                        <input
                            className="input"
                            placeholder="Optional"
                            value={draft.alert_id}
                            onChange={(e) => setDraft({ ...draft, alert_id: e.target.value })}
                        />
                    ))}
                    {field('from', 'From', (
                        <input
                            className="input"
                            type="datetime-local"
                            step="1"
                            value={draft.start}
                            onChange={(e) => setDraft({ ...draft, start: e.target.value })}
                        />
                    ))}
                    {field('to', 'To', (
                        <input
                            className="input"
                            type="datetime-local"
                            step="1"
                            value={draft.end}
                            onChange={(e) => setDraft({ ...draft, end: e.target.value })}
                        />
                    ))}
                    {field('sort', 'Sort', (
                        <select
                            className="select"
                            value={draft.sort}
                            onChange={(e) => setDraft({ ...draft, sort: e.target.value })}
                        >
                            <option value="descending">Newest first</option>
                            <option value="ascending">Oldest first</option>
                        </select>
                    ))}
                </div>

                <div className="alerts-filters__actions">
                    <button type="button" className="alerts-btn alerts-btn--primary" onClick={apply}>
                        Apply filters
                    </button>
                    {dirty && (
                        <button type="button" className="alerts-btn" onClick={reset}>
                            <RotateCcw size={13} aria-hidden /> Reset
                        </button>
                    )}
                </div>
            </section>

            {/* ── tabs ────────────────────────────────────────────── */}
            <div className="alerts-tabs" role="tablist" aria-label="Alert views">
                <button
                    type="button"
                    role="tab"
                    aria-selected={tab === 'live'}
                    className={`alerts-tab${tab === 'live' ? ' is-active' : ''}`}
                    onClick={() => setTab('live')}
                >
                    Needs attention
                    {facets?.unacknowledged > 0 && (
                        <span className="alerts-tab__count">{facets.unacknowledged}</span>
                    )}
                </button>
                <button
                    type="button"
                    role="tab"
                    aria-selected={tab === 'history'}
                    className={`alerts-tab${tab === 'history' ? ' is-active' : ''}`}
                    onClick={() => setTab('history')}
                >
                    History
                </button>
            </div>

            {error && <div className="alert-banner danger">{error}</div>}

            <section className="alerts-card">
                {loading && items.length === 0 ? (
                    <div className="alerts-empty"><p>Loading alerts…</p></div>
                ) : (
                    <AlertList
                        items={items}
                        onOpen={setOpen}
                        empty={tab === 'live'
                            ? 'Nothing needs attention — every alert has been acknowledged.'
                            : 'No alerts match those filters.'}
                    />
                )}

                {tab === 'history' && (
                    <div className="alerts-pager">
                        <span className="alerts-pager__info">
                            {total === 0
                                ? '0 results'
                                : `${(page - 1) * pageSize + 1}–${Math.min(page * pageSize, total)} of ${total}`}
                        </span>
                        <label className="alerts-pager__size">
                            <span>Results per page</span>
                            <select
                                className="select"
                                value={pageSize}
                                onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1); }}
                                aria-label="Results per page"
                            >
                                {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
                            </select>
                        </label>
                        <div className="alerts-pager__nav">
                            <button
                                type="button"
                                className="alerts-pager__btn"
                                disabled={page <= 1}
                                onClick={() => setPage((p) => Math.max(1, p - 1))}
                                aria-label="Previous page"
                            >
                                <ChevronLeft size={14} />
                            </button>
                            <span className="alerts-pager__current">Page {page} of {totalPages}</span>
                            <button
                                type="button"
                                className="alerts-pager__btn"
                                disabled={page >= totalPages}
                                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                                aria-label="Next page"
                            >
                                <ChevronRight size={14} />
                            </button>
                        </div>
                    </div>
                )}
            </section>

            <AlertImagesModal
                alert={open}
                alerts={items}
                onSelect={setOpen}
                open={open !== null}
                onClose={() => setOpen(null)}
                onAck={ack}
                acking={acking === open?.alert_id}
            />
        </div>
    );
}
