/**
 * OmniTrack AI — Captures
 *
 * Browse alert evidence (snapshots + clips from GET /api/evidence), grouped by
 * store-local day and camera, with retention expiry. The header also hosts the
 * data export panel (GET /api/export/...).
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Bell, Download, RefreshCw } from 'lucide-react';
import { apiErrorMessage, camerasAPI, evidenceAPI } from '../services/api';
import CaptureGrid from '../components/captures/CaptureGrid';
import CaptureViewer from '../components/captures/CaptureViewer';
import ExportPanel from '../components/captures/ExportPanel';
import { SOURCE_LABEL, cameraLabel, useStoreTimezone } from '../components/alerts/alertFormat';

const PAGE_SIZE = 60;
const DEFAULTS = { camera_id: '', source: '', kind: '' };
const KEYS = Object.keys(DEFAULTS);

function EmbedWrap({ children }) {
    return <div className="ops-embed">{children}</div>;
}

function PageScroll({ children }) {
    return <div className="page-scroll">{children}</div>;
}

export default function CapturesPage({ embedded = false }) {
    const [searchParams, setSearchParams] = useSearchParams();
    const pk = (k) => (embedded ? `cap_${k}` : k);
    const [filters, setFilters] = useState(() => {
        const f = { ...DEFAULTS };
        KEYS.forEach((k) => { if (searchParams.has(pk(k))) f[k] = searchParams.get(pk(k)) || ''; });
        return f;
    });
    const [items, setItems] = useState([]);
    const [total, setTotal] = useState(0);
    const [tzHint, setTzHint] = useState(null);
    const [loading, setLoading] = useState(true);
    const [loadingMore, setLoadingMore] = useState(false);
    const [error, setError] = useState(null);
    const [cameras, setCameras] = useState([]);
    const [openId, setOpenId] = useState(null);
    const [showExport, setShowExport] = useState(searchParams.get(pk('export')) === '1');
    const reqSeq = useRef(0);

    const tz = useStoreTimezone(tzHint);

    useEffect(() => {
        // Embedded in a hub (next to Alerts, which uses the same filter names), keys are
        // prefixed and every other param is kept.
        setSearchParams((prev) => {
            const next = new URLSearchParams(embedded ? prev : undefined);
            KEYS.forEach((k) => { if (filters[k]) next.set(pk(k), filters[k]); else next.delete(pk(k)); });
            if (showExport) next.set(pk('export'), '1'); else next.delete(pk('export'));
            return next;
        }, { replace: true });
    }, [filters, showExport, embedded, setSearchParams]);

    useEffect(() => {
        camerasAPI.list().then((r) => setCameras(Array.isArray(r.data) ? r.data : [])).catch(() => setCameras([]));
    }, []);

    const cameraNames = useMemo(() => new Map(cameras.map((c) => [String(c.id), c.name])), [cameras]);
    const cameraName = useCallback((id) => cameraLabel(cameraNames.get(String(id)), id), [cameraNames]);

    // The contract does not promise a server-side kind filter: send it, and also filter here.
    const keep = useCallback((it) => !filters.kind || it.kind === filters.kind, [filters.kind]);

    const fetchPage = useCallback(async (offset) => {
        const res = await evidenceAPI.list({ camera_id: filters.camera_id, source: filters.source, kind: filters.kind, limit: PAGE_SIZE, offset });
        const body = res.data || {};
        if (body.timezone) setTzHint(body.timezone);
        const raw = Array.isArray(body.items) ? body.items : [];
        return { raw, total: Number(body.total ?? raw.length) };
    }, [filters]);

    const load = useCallback(async ({ quiet = false } = {}) => {
        const seq = ++reqSeq.current;
        if (!quiet) setLoading(true);
        try {
            const page = await fetchPage(0);
            if (seq !== reqSeq.current) return;
            setItems(page.raw);
            setTotal(page.total);
            setError(null);
        } catch (err) {
            if (seq !== reqSeq.current) return;
            setError(err?.response?.status === 404
                ? 'The evidence service is not available on this server yet.'
                : apiErrorMessage(err, 'Could not load captures.'));
        } finally {
            if (seq === reqSeq.current) setLoading(false);
        }
    }, [fetchPage]);

    useEffect(() => { load(); }, [load]);

    const loadMore = async () => {
        const seq = reqSeq.current;
        setLoadingMore(true);
        try {
            const page = await fetchPage(items.length);
            if (seq !== reqSeq.current) return;
            setItems((prev) => {
                const seen = new Set(prev.map((i) => i.id));
                return [...prev, ...page.raw.filter((i) => !seen.has(i.id))];
            });
            setTotal(page.total);
        } catch (err) {
            setError(apiErrorMessage(err, 'Could not load more captures.'));
        } finally {
            setLoadingMore(false);
        }
    };

    const visible = useMemo(() => items.filter(keep), [items, keep]);
    const openIndex = visible.findIndex((i) => i.id === openId);
    const openItem = openIndex >= 0 ? visible[openIndex] : null;
    const filtersActive = KEYS.some((k) => filters[k]);
    const change = (patch) => { setFilters((f) => ({ ...f, ...patch })); setOpenId(null); };

    const clipCount = visible.filter((i) => i.kind === 'clip').length;

    const snapCount = visible.length - clipCount;
    const pending = loading && !items.length;
    const summaryOsd = (
        <div className="osd-strip ops-head-osd" aria-label="Capture summary">
            <span>Files <strong>{pending ? '—' : total.toLocaleString()}</strong></span>
            <span>Snapshots <strong>{pending ? '—' : snapCount}</strong></span>
            <span>Clips <strong>{pending ? '—' : clipCount}</strong></span>
            {tz ? <span>Store time <strong>{tz}</strong></span> : null}
        </div>
    );
    const exportBtn = (
        <button
            type="button"
            className={`btn btn-sm ${showExport ? 'btn-primary' : 'btn-secondary'}`}
            onClick={() => setShowExport((v) => !v)}
            aria-expanded={showExport}
            aria-controls="captures-export"
        >
            <Download size={16} strokeWidth={1.75} /> Export data
        </button>
    );
    const refreshBtn = (
        <button type="button" className="btn btn-secondary btn-sm" onClick={() => load()} disabled={loading}>
            <RefreshCw size={16} strokeWidth={1.75} /> {loading ? 'Refreshing…' : 'Refresh'}
        </button>
    );
    const Wrap = embedded ? EmbedWrap : PageScroll;

    return (
        <Wrap>
            {embedded ? (
                <div className="ops-embed-bar">
                    {summaryOsd}
                    <div className="ops-embed-actions">{exportBtn}{refreshBtn}</div>
                </div>
            ) : (
                <header className="page-header">
                    <div>
                        <h1 className="page-title">Captures</h1>
                        {summaryOsd}
                    </div>
                    <div className="page-header-actions">
                        <Link to="/incidents/alerts" className="btn btn-secondary btn-sm"><Bell size={16} strokeWidth={1.75} /> Alerts</Link>
                        {exportBtn}
                        {refreshBtn}
                    </div>
                </header>
            )}

            {showExport && (
                <section className="panel ops-panel" id="captures-export" aria-labelledby="captures-export-title">
                    <div className="panel-head">
                        <div>
                            <h2 className="panel-title" id="captures-export-title">Export data</h2>
                            <p className="panel-sub">Raw records as recorded by the server. Times in the file are UTC.</p>
                        </div>
                    </div>
                    <div className="panel-body"><ExportPanel /></div>
                </section>
            )}

            <section className="panel ops-panel" aria-labelledby="captures-evidence">
                <div className="panel-head">
                    <div>
                        <h2 className="panel-title" id="captures-evidence">Evidence</h2>
                        <p className="panel-sub">
                            {loading
                                ? 'Loading…'
                                : `${visible.length} shown${total > items.length ? ` of ${total}` : ''}. Snapshots and clips saved when an alert opened or fire was detected.`}
                        </p>
                    </div>
                </div>
                <div className="panel-body ops-col-body">
                    <div className="ops-filter-bar">
                        <div className="form-group">
                            <label className="form-label" htmlFor="cap-camera">Camera</label>
                            <select id="cap-camera" className="form-select" value={filters.camera_id} onChange={(e) => change({ camera_id: e.target.value })}>
                                <option value="">All cameras</option>
                                {cameras.map((c) => <option key={c.id} value={String(c.id)}>{cameraLabel(c.name, c.id)}</option>)}
                            </select>
                        </div>
                        <div className="form-group">
                            <label className="form-label" htmlFor="cap-source">Source</label>
                            <select id="cap-source" className="form-select" value={filters.source} onChange={(e) => change({ source: e.target.value })}>
                                <option value="">All sources</option>
                                {Object.entries(SOURCE_LABEL).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                            </select>
                        </div>
                        <div className="form-group">
                            <span className="form-label" id="cap-kind-label">Type</span>
                            <div className="seg" role="group" aria-labelledby="cap-kind-label">
                                {[['', 'All'], ['snapshot', 'Snapshots'], ['clip', 'Clips']].map(([v, l]) => (
                                    <button key={v || 'all'} type="button" aria-pressed={filters.kind === v} onClick={() => change({ kind: v })}>{l}</button>
                                ))}
                            </div>
                        </div>
                        <div className="ops-filter-reset">
                            <button type="button" className="btn btn-ghost btn-sm" onClick={() => change(DEFAULTS)} disabled={!filtersActive}>
                                Reset filters
                            </button>
                        </div>
                    </div>

                    {error && <div className="alert-banner danger">{error}</div>}

                    {loading && !items.length && !error ? (
                        <div className="empty-state ops-alerts-empty" aria-busy="true">
                            <div className="empty-state-title">Loading captures…</div>
                        </div>
                    ) : null}

                    {!loading && !error && visible.length === 0 && (
                        filtersActive ? (
                            <div className="empty-state ops-alerts-empty">
                                <div className="empty-state-title">No captures match these filters</div>
                                <button type="button" className="btn btn-secondary btn-sm" onClick={() => change(DEFAULTS)}>Clear filters</button>
                            </div>
                        ) : (
                            <div className="empty-state ops-alerts-empty">
                                <div className="empty-state-title">No captures recorded yet</div>
                                <p>Evidence is saved automatically when a zone alert opens or fire or smoke is detected on a running camera.</p>
                            </div>
                        )
                    )}

                    {visible.length > 0 && (
                        <CaptureGrid items={visible} tz={tz} cameraName={cameraName} onOpen={(it) => setOpenId(it.id)} />
                    )}

                    {items.length < total && !error && (
                        <div className="ops-pager ops-pager--center">
                            <button type="button" className="btn btn-secondary btn-sm" onClick={loadMore} disabled={loadingMore}>
                                {loadingMore ? 'Loading…' : `Load ${Math.min(PAGE_SIZE, total - items.length)} older`}
                            </button>
                            <span className="ops-pager-pos">{total - items.length} not loaded</span>
                        </div>
                    )}
                </div>
            </section>

            {openItem && (
                <CaptureViewer
                    item={openItem}
                    index={openIndex}
                    count={visible.length}
                    tz={tz}
                    cameraName={cameraName}
                    onClose={() => setOpenId(null)}
                    onPrev={openIndex > 0 ? () => setOpenId(visible[openIndex - 1].id) : undefined}
                    onNext={openIndex < visible.length - 1 ? () => setOpenId(visible[openIndex + 1].id) : undefined}
                />
            )}
        </Wrap>
    );
}
