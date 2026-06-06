import React, { useMemo, useState } from 'react';
import {
    AlertTriangle, BrainCircuit, CheckCircle, Receipt, ScanLine, ShoppingCart,
    Store, UsersRound,
} from 'lucide-react';
import { humanlessAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import useUserRole from '../hooks/useUserRole';
import ContentCard from '../components/ui/ContentCard';
import StatusTile from '../components/ui/StatusTile';

const money = (value) => `$${Number(value || 0).toFixed(2)}`;
const dt = (value) => (value ? new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '-');

export default function HumanlessStorePage() {
    const { canOperate } = useUserRole();
    const [eventForm, setEventForm] = useState({
        session_id: '',
        global_id: '',
        product_id: '',
        shelf_zone_id: '',
        event_type: 'pickup',
        quantity_delta: 1,
        confidence: 0.85,
    });
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState(null);
    const [notice, setNotice] = useState(null);

    const { data: overview, refresh: refreshOverview } = useLivePoll(() => humanlessAPI.overview(20), { intervalMs: 5000 });
    const { data: cashierQueue, refresh: refreshQueue } = useLivePoll(() => humanlessAPI.cashierQueue(null, 20), { intervalMs: 3000 });
    const { data: alerts, refresh: refreshAlerts } = useLivePoll(() => humanlessAPI.alerts('open', 20), { intervalMs: 8000 });
    const { data: products } = useLivePoll(() => humanlessAPI.products(true), { intervalMs: 12000 });
    const { data: zones } = useLivePoll(() => humanlessAPI.shelfZones(true), { intervalMs: 12000 });

    const sessions = Array.isArray(overview?.sessions) ? overview.sessions : [];
    const queueRows = Array.isArray(cashierQueue) ? cashierQueue : [];
    const alertRows = Array.isArray(alerts) ? alerts : [];
    const productRows = Array.isArray(products) ? products : [];
    const zoneRows = Array.isArray(zones) ? zones : [];
    const mergedSessions = useMemo(() => {
        const byId = new Map();
        [...queueRows, ...sessions].forEach((session) => {
            if (session?.id) byId.set(session.id, session);
        });
        return Array.from(byId.values());
    }, [queueRows, sessions]);

    const refreshCommerce = async () => {
        await Promise.all([refreshOverview(), refreshQueue(), refreshAlerts()]);
    };

    const checkout = async (sessionId) => {
        if (!canOperate) return;
        setBusy(true); setError(null); setNotice(null);
        try {
            const { data } = await humanlessAPI.checkoutSession(sessionId);
            setNotice(`Checkout simulated for session ${data.session_id}: ${money(data.subtotal)}.`);
            await refreshCommerce();
        } catch (e) {
            setError(e?.response?.data?.detail || e.message);
        } finally {
            setBusy(false);
        }
    };

    const markCounter = async (sessionId) => {
        if (!canOperate) return;
        setBusy(true); setError(null); setNotice(null);
        try {
            await humanlessAPI.counterArrival(sessionId, 'checkout', 1);
            setNotice(`Session ${sessionId} moved to checkout queue.`);
            await refreshCommerce();
        } catch (e) {
            setError(e?.response?.data?.detail || e.message);
        } finally {
            setBusy(false);
        }
    };

    const closeStale = async () => {
        if (!canOperate) return;
        setBusy(true); setError(null); setNotice(null);
        try {
            const { data } = await humanlessAPI.closeStale(300);
            setNotice(`${data.closed || 0} stale session(s) closed.`);
            await refreshCommerce();
        } catch (e) {
            setError(e?.response?.data?.detail || e.message);
        } finally {
            setBusy(false);
        }
    };

    const createEvent = async (e) => {
        e.preventDefault();
        if (!canOperate || (!eventForm.session_id && !eventForm.global_id)) return;
        setBusy(true); setError(null); setNotice(null);
        try {
            const qty = Number(eventForm.quantity_delta || 0);
            await humanlessAPI.createCartEvent({
                session_id: eventForm.session_id ? Number(eventForm.session_id) : null,
                global_id: eventForm.global_id || null,
                product_id: eventForm.product_id ? Number(eventForm.product_id) : null,
                shelf_zone_id: eventForm.shelf_zone_id ? Number(eventForm.shelf_zone_id) : null,
                event_type: eventForm.event_type,
                quantity_delta: eventForm.event_type === 'putback' ? -Math.abs(qty || 1) : Math.abs(qty || 1),
                confidence: Number(eventForm.confidence || 0.85),
                rule_source: 'operator_console',
                evidence: { source: 'smart_store_page' },
            });
            setNotice('Cart event recorded.');
            await refreshCommerce();
        } catch (e) {
            setError(e?.response?.data?.detail || e.message);
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="page-scroll">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Smart Store</h1>
                    <p className="page-subtitle">Virtual carts, checkout handoff, session review, and operator correction.</p>
                </div>
                <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                    <button type="button" className="btn btn-secondary btn-xs" onClick={refreshCommerce} disabled={busy}>
                        <BrainCircuit size={13} /> Refresh
                    </button>
                    <button type="button" className="btn btn-secondary btn-xs" onClick={closeStale} disabled={!canOperate || busy}>
                        <CheckCircle size={13} /> Close stale
                    </button>
                </div>
            </div>

            {error ? <div className="alert-banner danger">{error}</div> : null}
            {notice ? <div className="alert-banner info">{notice}</div> : null}

            <div className="ui-status-grid" style={{ marginBottom: 14 }}>
                <StatusTile icon={UsersRound} label="Active sessions" value={overview?.active_sessions || 0} tone="teal" compact />
                <StatusTile icon={ShoppingCart} label="Open carts" value={overview?.open_carts || 0} tone="sky" compact />
                <StatusTile icon={Receipt} label="Open subtotal" value={money(overview?.subtotal_open)} tone="ok" compact />
                <StatusTile icon={AlertTriangle} label="Review alerts" value={overview?.open_alerts || alertRows.length} tone={(overview?.open_alerts || alertRows.length) ? 'warn' : 'neutral'} compact />
            </div>

            <div className="two-col">
                <ContentCard title="Cashier queue" subtitle="Sessions that have reached the checkout handoff zone.">
                    <div className="ui-feed-list" style={{ maxHeight: 360, overflow: 'auto' }}>
                        {queueRows.length === 0 ? <div className="page-empty-hint">No shoppers at checkout yet.</div> : queueRows.map((session) => (
                            <div key={session.id} className="ui-lane-row" style={{ gridTemplateColumns: '130px 1fr 95px 96px' }}>
                                <span className="ui-lane-row-title">{session.global_id}</span>
                                <span>{session.last_zone || session.counter_id || 'checkout'}</span>
                                <span style={{ fontWeight: 700 }}>{money(session.cart?.subtotal)}</span>
                                <button type="button" className="btn btn-primary btn-xs" onClick={() => checkout(session.id)} disabled={!canOperate || busy}>
                                    Checkout
                                </button>
                            </div>
                        ))}
                    </div>
                </ContentCard>

                <ContentCard title="Active smart carts" subtitle="Open sessions with current basket value and last-seen zone.">
                    <div className="ui-feed-list" style={{ maxHeight: 360, overflow: 'auto' }}>
                        {sessions.length === 0 ? <div className="page-empty-hint">No active smart carts yet.</div> : sessions.map((session) => (
                            <div key={session.id} className="ui-lane-row" style={{ gridTemplateColumns: '130px 1fr 90px 90px' }}>
                                <span className="ui-lane-row-title">{session.global_id}</span>
                                <span>{session.last_zone || 'shopping floor'} / {dt(session.last_seen)}</span>
                                <span>{money(session.cart?.subtotal)}</span>
                                <button type="button" className="btn btn-secondary btn-xs" onClick={() => markCounter(session.id)} disabled={!canOperate || busy}>
                                    Counter
                                </button>
                            </div>
                        ))}
                    </div>
                </ContentCard>
            </div>

            <div className="two-col">
                <ContentCard title="Manual cart event" subtitle="Operator override for low-confidence shelf decisions and demos.">
                    <form className="settings-form" onSubmit={createEvent}>
                        <select className="form-input" value={eventForm.session_id} onChange={(e) => setEventForm((f) => ({ ...f, session_id: e.target.value }))}>
                            <option value="">Use global ID instead</option>
                            {mergedSessions.map((session) => (
                                <option key={session.id} value={session.id}>{session.global_id} / session {session.id}</option>
                            ))}
                        </select>
                        <input className="form-input" value={eventForm.global_id} placeholder="PERSON-00042" onChange={(e) => setEventForm((f) => ({ ...f, global_id: e.target.value }))} />
                        <select className="form-input" value={eventForm.product_id} onChange={(e) => setEventForm((f) => ({ ...f, product_id: e.target.value }))}>
                            <option value="">Infer from shelf zone</option>
                            {productRows.map((product) => (
                                <option key={product.id} value={product.id}>{product.name} / {money(product.price)}</option>
                            ))}
                        </select>
                        <select className="form-input" value={eventForm.shelf_zone_id} onChange={(e) => setEventForm((f) => ({ ...f, shelf_zone_id: e.target.value }))}>
                            <option value="">No shelf zone</option>
                            {zoneRows.map((zone) => (
                                <option key={zone.id} value={zone.id}>{zone.name}</option>
                            ))}
                        </select>
                        <div className="two-col" style={{ gap: 10 }}>
                            <select className="form-input" value={eventForm.event_type} onChange={(e) => setEventForm((f) => ({ ...f, event_type: e.target.value }))}>
                                <option value="pickup">Pickup</option>
                                <option value="putback">Putback</option>
                                <option value="adjustment">Adjustment</option>
                                <option value="uncertain">Uncertain</option>
                            </select>
                            <input className="form-input" type="number" value={eventForm.quantity_delta} onChange={(e) => setEventForm((f) => ({ ...f, quantity_delta: e.target.value }))} />
                        </div>
                        <button type="submit" className="btn btn-primary" disabled={!canOperate || busy}>
                            <ScanLine size={14} /> Record event
                        </button>
                    </form>
                </ContentCard>

                <ContentCard title="Alert review" subtitle="Open loss-prevention and low-confidence events.">
                    <div className="ui-feed-list" style={{ maxHeight: 380, overflow: 'auto' }}>
                        {alertRows.length === 0 ? <div className="page-empty-hint">No open review alerts.</div> : alertRows.map((alert) => (
                            <div key={alert.id} className="ui-lane-row" style={{ gridTemplateColumns: '1fr 88px 72px' }}>
                                <span>
                                    <span className="ui-lane-row-title">{alert.description || alert.alert_type}</span>
                                    <span style={{ display: 'block', fontSize: 12, color: 'var(--text-muted)' }}>
                                        {alert.global_id || alert.alert_type} / {dt(alert.timestamp)}
                                    </span>
                                </span>
                                <span className="pill pill-warning">{alert.severity}</span>
                                <span>{Math.round((alert.confidence || 0) * 100)}%</span>
                            </div>
                        ))}
                    </div>
                </ContentCard>
            </div>

            <ContentCard title="Cart inspector" subtitle="Recent carts and item counts.">
                <div className="ui-feed-list">
                    {sessions.length === 0 ? <div className="page-empty-hint">Cart details appear once sessions are active.</div> : sessions.map((session) => (
                        <div key={`cart-${session.id}`} className="ui-lane-row" style={{ gridTemplateColumns: '140px 1fr 120px 90px' }}>
                            <span className="ui-lane-row-title">{session.global_id}</span>
                            <span>{session.cart?.item_count || 0} item(s) / confidence {Math.round((session.cart?.confidence || 0) * 100)}%</span>
                            <span>{money(session.cart?.subtotal)}</span>
                            <span className={`pill ${session.payment_status === 'paid' ? 'pill-success' : 'pill-info'}`}>{session.payment_status}</span>
                        </div>
                    ))}
                </div>
            </ContentCard>
        </div>
    );
}
