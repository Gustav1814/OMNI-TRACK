/**
 * OmniTrack AI — Alerts centre
 *
 * One list for zone alerts (overcrowding, long queue, dwell overrun), fire /
 * smoke incidents and smart-store alerts, from GET /api/alerts. Filters and
 * their counts come from /api/alerts/facets. Each alert can carry evidence
 * (snapshot + short clip) captured when it opened.
 *
 * Freshness: the list re-polls every 15 s and also refreshes shortly after the
 * live WebSocket reports a new alert or fire event. Acknowledging is admin-only
 * and optimistic (rolled back if the server refuses).
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { ChevronLeft, ChevronRight, Images, RefreshCw } from 'lucide-react';
import { alertsAPI, apiErrorMessage } from '../services/api';
import useUserRole from '../hooks/useUserRole';
import useWebSocket from '../hooks/useWebSocket';
import { useToast } from '../contexts/ToastContext';
import AlertFilters, {
    AlertSourceChips, AlertStatusSeg, DEFAULT_FILTERS, filtersToParams,
} from '../components/alerts/AlertFilters';
import AlertList, { alertKey } from '../components/alerts/AlertList';
import AlertEvidenceModal from '../components/alerts/AlertEvidenceModal';
import { notifyAlertsChanged, useStoreTimezone } from '../components/alerts/alertFormat';

const PAGE_SIZE = 50;
const POLL_MS = 15000;
const WS_REFRESH_DELAY_MS = 1500; // let the alert row (and its event_uid) be persisted first
const FILTER_KEYS = Object.keys(DEFAULT_FILTERS);

function filtersFromSearch(sp) {
    const f = { ...DEFAULT_FILTERS };
    FILTER_KEYS.forEach((k) => { if (sp.has(k)) f[k] = sp.get(k) || ''; });
    return f;
}

function EmbedWrap({ children }) {
    return <div className="ops-embed">{children}</div>;
}

function PageScroll({ children }) {
    return <div className="page-scroll">{children}</div>;
}

export default function AlertsPage({ embedded = false }) {
    const { isAdmin, user } = useUserRole();
    const { toast } = useToast();
    const [searchParams, setSearchParams] = useSearchParams();
    const [filters, setFilters] = useState(() => filtersFromSearch(searchParams));
    const [offset, setOffset] = useState(0);

    const [data, setData] = useState(null);       // {items,total,status,timezone}
    const [facets, setFacets] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [openKey, setOpenKey] = useState(null);
    const [ackingKey, setAckingKey] = useState(null);

    const tz = useStoreTimezone(data?.timezone);
    const reqSeq = useRef(0);
    const pendingAcks = useRef(new Map());        // key -> optimistic item (survives polls)

    // Mirror filters into the URL so a filtered view can be shared/bookmarked.
    useEffect(() => {
        // Embedded in a hub, other sections' params (and the hub's own) are kept.
        setSearchParams((prev) => {
            const next = new URLSearchParams(embedded ? prev : undefined);
            FILTER_KEYS.forEach((k) => {
                if (filters[k] !== DEFAULT_FILTERS[k]) next.set(k, filters[k]);
                else next.delete(k);
            });
            return next;
        }, { replace: true });
    }, [filters, embedded, setSearchParams]);

    const load = useCallback(async ({ quiet = false } = {}) => {
        const seq = ++reqSeq.current;
        if (!quiet) setLoading(true);
        // The relative range ("last 24 h") is recomputed on every poll.
        const p = filtersToParams(filters);
        const [listRes, facetRes] = await Promise.allSettled([
            alertsAPI.list({ ...p, limit: PAGE_SIZE, offset }),
            alertsAPI.facets(),
        ]);
        if (seq !== reqSeq.current) return;
        if (listRes.status === 'fulfilled') {
            const body = listRes.value.data || {};
            const items = (Array.isArray(body.items) ? body.items : []).map((a) => pendingAcks.current.get(alertKey(a)) || a);
            setData({ ...body, items, total: Number(body.total ?? items.length) });
            setError(null);
        } else {
            const err = listRes.reason;
            setError(err?.response?.status === 404
                ? 'The alerts service is not available on this server yet.'
                : apiErrorMessage(err, 'Could not load alerts.'));
        }
        if (facetRes.status === 'fulfilled') setFacets(facetRes.value.data || null);
        setLoading(false);
    }, [filters, offset]);

    useEffect(() => { load(); }, [load]);

    // Periodic refresh while the tab is visible.
    useEffect(() => {
        const id = setInterval(() => { if (document.visibilityState === 'visible') load({ quiet: true }); }, POLL_MS);
        return () => clearInterval(id);
    }, [load]);

    // Push: the live channel announces alert / fire events; refresh soon after.
    const wsTimer = useRef(null);
    const loadRef = useRef(load);
    loadRef.current = load;
    const onWsAlert = useCallback(() => {
        clearTimeout(wsTimer.current);
        wsTimer.current = setTimeout(() => loadRef.current({ quiet: true }), WS_REFRESH_DELAY_MS);
    }, []);
    const wsHandlers = useMemo(() => ({ analytics_alert: onWsAlert, analytics_fire: onWsAlert, fire_alert: onWsAlert }), [onWsAlert]);
    const { status: wsStatus } = useWebSocket('/ws/live', { onType: wsHandlers });
    useEffect(() => () => clearTimeout(wsTimer.current), []);

    const changeFilters = (patch) => {
        setFilters((f) => ({ ...f, ...patch }));
        setOffset(0);
        setOpenKey(null);
    };
    const resetFilters = () => changeFilters(DEFAULT_FILTERS);

    const items = data?.items || [];
    const total = data?.total || 0;
    const filtersActive = FILTER_KEYS.some((k) => filters[k] !== DEFAULT_FILTERS[k]);

    const patchItem = (key, next) => setData((d) => (d ? { ...d, items: d.items.map((a) => (alertKey(a) === key ? next : a)) } : d));

    const ack = async (a) => {
        const key = alertKey(a);
        if (!isAdmin || a.acked || ackingKey) return;
        const optimistic = { ...a, acked: true, acked_by: user?.username || 'you', acked_at: new Date().toISOString() };
        pendingAcks.current.set(key, optimistic);
        patchItem(key, optimistic);
        setFacets((f) => (f && Number.isFinite(f.unacked) ? { ...f, unacked: Math.max(0, f.unacked - 1) } : f));
        setAckingKey(key);
        try {
            const res = await alertsAPI.ack(a.source, a.id);
            const server = res?.data && typeof res.data === 'object' && res.data.id != null ? res.data : null;
            pendingAcks.current.delete(key);
            patchItem(key, server ? { ...optimistic, ...server } : optimistic);
            notifyAlertsChanged();
        } catch (e) {
            pendingAcks.current.delete(key);
            if (e?.response?.status === 409) {
                // Someone else acknowledged it first: the server state wins.
                toast({ type: 'info', title: 'Already acknowledged', message: 'Another user acknowledged this alert.' });
            } else {
                patchItem(key, a);
                toast({ type: 'error', title: 'Could not acknowledge alert', message: apiErrorMessage(e) });
            }
            load({ quiet: true });
        } finally {
            setAckingKey(null);
        }
    };

    const openIndex = openKey ? items.findIndex((a) => alertKey(a) === openKey) : -1;
    const openAlert = openIndex >= 0 ? items[openIndex] : null;
    useEffect(() => { if (openKey && openIndex < 0 && !loading) setOpenKey(null); }, [openKey, openIndex, loading]);

    const from = total ? offset + 1 : 0;
    const to = Math.min(offset + items.length, total);
    const wsOpen = wsStatus === 'open';
    const statusOsd = (
        <div className="osd-strip ops-head-osd" aria-label="Alert channel status">
            <span>
                <span className={`live-dot ${wsOpen ? 'is-ok' : 'is-warn'}`} aria-hidden />
                <strong>{wsOpen ? 'Live' : 'Reconnecting'}</strong>
            </span>
            <span>Open <strong>{facets?.unacked != null ? Number(facets.unacked).toLocaleString() : '—'}</strong></span>
            <span>Showing <strong>{data ? total.toLocaleString() : '—'}</strong></span>
            {tz ? <span>Store time <strong>{tz}</strong></span> : null}
        </div>
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
                    {statusOsd}
                    <div className="ops-embed-actions">{refreshBtn}</div>
                </div>
            ) : (
                <header className="page-header">
                    <div>
                        <h1 className="page-title">Alerts</h1>
                        {statusOsd}
                    </div>
                    <div className="page-header-actions">
                        <Link to="/incidents/captures" className="btn btn-secondary btn-sm"><Images size={16} strokeWidth={1.75} /> Captures</Link>
                        {refreshBtn}
                    </div>
                </header>
            )}

            {error ? <div className="alert-banner danger">{error}</div> : null}

            <div className="ops-toolbar">
                <AlertSourceChips value={filters.source} onChange={changeFilters} facets={facets} />
                <AlertStatusSeg value={filters.acked} onChange={changeFilters} facets={facets} />
            </div>

            <section className="panel ops-panel" aria-labelledby="alerts-history">
                <div className="panel-head">
                    <div>
                        <h2 className="panel-title" id="alerts-history">Alert history</h2>
                        <p className="panel-sub">
                            {total
                                ? `${from.toLocaleString()}–${to.toLocaleString()} of ${total.toLocaleString()}, newest first`
                                : 'Newest first'}
                        </p>
                    </div>
                </div>
                <div className="panel-body ops-col-body">
                    <AlertFilters filters={filters} onChange={changeFilters} facets={facets} onReset={resetFilters} />

                    {loading && !data ? (
                        <div className="empty-state ops-alerts-empty" aria-busy="true">
                            <div className="empty-state-title">Loading alerts…</div>
                        </div>
                    ) : items.length === 0 ? (
                        error ? null : filtersActive ? (
                            <div className="empty-state ops-alerts-empty">
                                <div className="empty-state-title">No alerts match these filters</div>
                                <p>Try a wider time range or clear the filters.</p>
                                <button type="button" className="btn btn-secondary btn-sm" onClick={resetFilters}>Clear filters</button>
                            </div>
                        ) : (
                            <div className="empty-state ops-alerts-empty">
                                <div className="empty-state-title">No alerts recorded yet</div>
                                <p>
                                    Alerts appear when a zone threshold is crossed, fire or smoke is confirmed,
                                    or someone stays past a dwell limit. Draw zones and set thresholds in store setup.
                                </p>
                                <Link to="/setup/zones" className="btn btn-secondary btn-sm">Open zone setup</Link>
                            </div>
                        )
                    ) : (
                        <>
                            <AlertList
                                items={items}
                                tz={tz}
                                isAdmin={isAdmin}
                                onOpen={(a) => setOpenKey(alertKey(a))}
                                onAck={ack}
                                ackingKey={ackingKey}
                            />
                            {total > PAGE_SIZE ? (
                                <nav className="ops-pager" aria-label="Pages">
                                    <button type="button" className="btn btn-secondary btn-sm" disabled={offset === 0}
                                        onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}>
                                        <ChevronLeft size={16} strokeWidth={1.75} /> Newer
                                    </button>
                                    <span className="ops-pager-pos">Page {Math.floor(offset / PAGE_SIZE) + 1} of {Math.ceil(total / PAGE_SIZE)}</span>
                                    <button type="button" className="btn btn-secondary btn-sm" disabled={offset + PAGE_SIZE >= total}
                                        onClick={() => setOffset((o) => o + PAGE_SIZE)}>
                                        Older <ChevronRight size={16} strokeWidth={1.75} />
                                    </button>
                                </nav>
                            ) : null}
                        </>
                    )}
                </div>
            </section>

            {openAlert ? (
                <AlertEvidenceModal
                    alert={openAlert}
                    index={openIndex}
                    count={items.length}
                    tz={tz}
                    isAdmin={isAdmin}
                    acking={ackingKey === openKey}
                    onAck={ack}
                    onPrev={openIndex > 0 ? () => setOpenKey(alertKey(items[openIndex - 1])) : undefined}
                    onNext={openIndex < items.length - 1 ? () => setOpenKey(alertKey(items[openIndex + 1])) : undefined}
                    onClose={() => setOpenKey(null)}
                />
            ) : null}
        </Wrap>
    );
}
