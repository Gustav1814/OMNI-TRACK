/**
 * OmniTrack AI — Audit Log (admin only)
 *
 * Entries are chained with HMAC-SHA256 (key never stored in the DB): editing,
 * deleting, inserting or reordering any row breaks verification at that row,
 * and a signed anchor detects truncation of the newest rows. The backend
 * re-verifies at startup and every AUDIT_VERIFY_INTERVAL_HOURS; "Verify now"
 * runs a full check on demand.
 */

import React, { useCallback, useEffect, useState } from 'react';
import { ShieldCheck, RefreshCw } from 'lucide-react';
import { apiErrorMessage, auditAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import useUserRole from '../hooks/useUserRole';
import PageHeader from '../components/PageHeader';

const EVENT_FILTERS = ['', 'LOGIN', 'LOGIN_FAILED', 'LOGIN_BLOCKED', 'ACCOUNT_LOCKED', 'REFRESH_REUSE', 'LOGOUT',
    'USER_CREATE', 'USER_UPDATE', 'USER_DELETE', 'PASSWORD_CHANGE', 'CAMERA_CREATE', 'CAMERA_UPDATE',
    'CAMERA_DELETE', 'ADMIN_LOGIN', 'ADMIN_LOGIN_FAILED', 'ROBUSTNESS_RUN'];

const LIMITS = [25, 50, 100, 200, 500];

/** LOGIN_FAILED -> "Login failed" (sentence case for display; the filter value stays the raw type). */
const eventLabel = (t) => {
    const s = String(t || '').toLowerCase().replace(/_/g, ' ');
    return s ? s.charAt(0).toUpperCase() + s.slice(1) : '—';
};

const fmtTime = (iso) => (iso ? new Date(iso).toLocaleString() : '—');

export default function AuditPage({ embedded = false }) {
    const { isAdmin } = useUserRole();
    const [limit, setLimit] = useState(50);
    const [eventType, setEventType] = useState('');
    const [chain, setChain] = useState(null);
    const [verifying, setVerifying] = useState(false);
    const [error, setError] = useState('');

    const { data: logs, error: logsError, refresh: refreshLogs } = useLivePoll(
        () => auditAPI.logs(limit, { eventType: eventType || null }),
        { intervalMs: 8000, enabled: isAdmin },
    );
    const { data: lastCheck, refresh: refreshStatus } = useLivePoll(() => auditAPI.status(), { intervalMs: 30000 });

    // re-query immediately when the filter changes (the poller picks up the new closure)
    useEffect(() => { if (isAdmin) refreshLogs(); }, [limit, eventType]); // eslint-disable-line react-hooks/exhaustive-deps

    const verify = useCallback(async () => {
        setVerifying(true);
        setError('');
        try {
            const { data } = await auditAPI.verify();
            setChain(data);
            refreshStatus();
        } catch (e) {
            setError(apiErrorMessage(e, 'Verification failed'));
        } finally {
            setVerifying(false);
        }
    }, [refreshStatus]);

    useEffect(() => { if (isAdmin) verify(); }, [isAdmin, verify]);

    if (!isAdmin) {
        return (
            <div className={embedded ? 'adm-embedded' : 'adm-page'}>
                {!embedded && <PageHeader title="Activity log" subtitle="The tamper-evident record of sign-ins and admin changes." />}
                <div className="empty-state">
                    <span className="empty-state-title">Administrators only</span>
                    The audit trail is visible to administrators. Ask one to review it or to change your role.
                </div>
            </div>
        );
    }

    const list = Array.isArray(logs) ? logs : [];
    const result = chain || (lastCheck?.status && lastCheck.status !== 'pending' ? lastCheck : null);
    const valid = result?.valid;
    const statusPill = valid == null
        ? <span className="pill pill-neutral">{verifying ? 'Checking' : 'Not checked'}</span>
        : valid
            ? <span className="pill pill-success">Chain intact</span>
            : <span className="pill pill-danger">Chain broken</span>;
    const headline = valid == null
        ? (verifying ? 'Verifying the chain' : 'No verification yet')
        : valid
            ? `${result.total ?? 0} entries verified`
            : `Broken at entry #${result.broken_at ?? '?'}`;

    const actions = (
        <>
            <button type="button" className="btn btn-secondary btn-sm" onClick={() => refreshLogs()}>
                <RefreshCw size={16} strokeWidth={1.75} /> Refresh
            </button>
            <button type="button" className="btn btn-primary btn-sm" onClick={verify} disabled={verifying}>
                <ShieldCheck size={16} strokeWidth={1.75} /> {verifying ? 'Verifying…' : 'Verify chain'}
            </button>
        </>
    );

    return (
        <div className={embedded ? 'adm-embedded' : 'adm-page'}>
            {embedded ? <div className="adm-embed-bar">{actions}</div> : (
                <PageHeader
                    title="Activity log"
                    subtitle="Every sign-in and admin change, chained with HMAC-SHA256 so edits and deletions show up."
                >
                    {actions}
                </PageHeader>
            )}

            {(error || logsError) && (
                <div className="alert-banner danger" role="alert">{error || apiErrorMessage(logsError, 'Could not load audit log')}</div>
            )}

            {valid === false && (
                <div className="alert-banner danger" role="alert">
                    <strong>Audit chain broken</strong> at entry #{result.broken_at ?? '?'}: {result.reason || result.message}.
                    {' '}Rows from that point on cannot be trusted. If the break is in unkeyed rows written before
                    the HMAC chain existed, an operator can exclude that legacy prefix with
                    {' '}<code className="adm-code">python -m app.security.audit_rebase --apply</code> (keyed rows can never be excluded).
                </div>
            )}

            {valid && chain?.excluded_legacy > 0 && (
                <div className="alert-banner info">
                    {chain.excluded_legacy} pre-HMAC legacy row(s) below #{chain.base_id} are excluded by a signed
                    chain base; everything from #{chain.base_id} on is verified.
                </div>
            )}

            <div className="adm-overview">
                <section className="panel panel--feature adm-feature" aria-label="Verified entries">
                    <div className="panel-body">
                        <div className="adm-feature-head">
                            <span className="metric-label">Entries verified</span>
                            {statusPill}
                        </div>
                        <div className="metric metric--hero">
                            <span className="metric-value tabular">{valid ? (result.total ?? 0) : valid === false ? `#${result.broken_at ?? '?'}` : '…'}</span>
                            <span className="metric-sub">
                                {valid ? 'HMAC-SHA256 chain, oldest to newest'
                                    : valid === false ? 'First entry that failed verification' : 'Run a check to verify the chain'}
                            </span>
                        </div>
                    </div>
                </section>
                <section className="panel panel--flush adm-flush" aria-label="Chain integrity">
                    <div className="panel-body">
                        <div>
                            <h2 className="adm-status-title">{headline}</h2>
                            {result?.message && <p className="adm-status-msg">{result.message}</p>}
                        </div>
                        <div className="kpi-strip">
                            <div className="metric">
                                <span className="metric-label">Tail anchor</span>
                                <span className="adm-text-value is-mono">{chain?.anchor ?? '—'}</span>
                            </div>
                            <div className="metric">
                                <span className="metric-label">Last check</span>
                                <span className="adm-text-value is-mono">
                                    {result?.checked_at ? new Date(result.checked_at).toLocaleTimeString([], { hour12: false }) : '—'}
                                </span>
                                {result?.trigger && <span className="metric-sub">{eventLabel(result.trigger)} check</span>}
                            </div>
                            <div className="metric">
                                <span className="metric-label">Scheduled check</span>
                                <span className="adm-text-value is-mono">
                                    {lastCheck?.next_check_hours != null ? `Every ${lastCheck.next_check_hours} h` : '—'}
                                </span>
                            </div>
                        </div>
                    </div>
                </section>
            </div>

            <section className="panel">
                <div className="panel-body">
                    <div className="panel-head">
                        <div>
                            <h2 className="panel-title">Events</h2>
                            <p className="panel-sub">Newest first. Refreshes every 8 seconds.</p>
                        </div>
                        <div className="adm-filters">
                            <select
                                className="form-select"
                                value={eventType}
                                onChange={(e) => setEventType(e.target.value)}
                                aria-label="Filter by event type"
                            >
                                {EVENT_FILTERS.map((t) => <option key={t} value={t}>{t ? eventLabel(t) : 'All events'}</option>)}
                            </select>
                            <select
                                className="form-select"
                                value={limit}
                                onChange={(e) => setLimit(Number(e.target.value))}
                                aria-label="Rows to show"
                            >
                                {LIMITS.map((n) => <option key={n} value={n}>{n} rows</option>)}
                            </select>
                        </div>
                    </div>

                    {list.length === 0 ? (
                        <div className="empty-state">
                            <span className="empty-state-title">
                                {eventType ? `No ${eventLabel(eventType).toLowerCase()} events` : 'No audit entries yet'}
                            </span>
                            {eventType
                                ? 'Nothing of this type has been recorded. Choose All events to see the full trail.'
                                : 'Sign-ins and admin changes are recorded here as they happen.'}
                        </div>
                    ) : (
                        <div className="table-scroll">
                            <table className="data-table adm-table adm-table--wide">
                                <thead>
                                    <tr>
                                        <th>Time</th>
                                        <th>Event</th>
                                        <th>Description</th>
                                        <th>IP address</th>
                                        <th className="num">Entry</th>
                                        <th>Hash</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {list.map((e) => {
                                        const broken = valid === false && result?.broken_at != null && e.id >= result.broken_at;
                                        return (
                                            <tr key={e.id} className={broken ? 'adm-row-broken' : undefined}>
                                                <td className="mono">{fmtTime(e.timestamp)}</td>
                                                <td><span className={`pill ${pillForEventType(e.event_type)}`}>{eventLabel(e.event_type)}</span></td>
                                                <td>
                                                    {e.description || '—'}
                                                    {e.metadata_encrypted && e.metadata == null && (
                                                        <span className="adm-sub" title="Encrypted with a different AES key">Metadata unreadable with the current key</span>
                                                    )}
                                                    {broken && <span className="adm-sub">After the break: not trusted</span>}
                                                </td>
                                                <td className="mono">{e.ip_address || '—'}</td>
                                                <td className="mono num">#{e.id}</td>
                                                <td className="mono" title={e.current_hash}>
                                                    {e.current_hash ? `${e.current_hash.slice(0, 12)}…` : '—'}
                                                    <span className="adm-sub">{e.hash_alg || 'legacy'}</span>
                                                </td>
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        </div>
                    )}
                </div>
            </section>
        </div>
    );
}

function pillForEventType(type) {
    const t = (type || '').toUpperCase();
    if (t === 'LOGIN' || t === 'ADMIN_LOGIN' || t === 'REFRESH') return 'pill-success';
    if (t === 'LOGOUT') return 'pill-info';
    if (t.includes('FAILED') || t.includes('LOCKED') || t.includes('REUSE') || t.includes('DENIED')
        || t.includes('BLOCKED') || t === 'FIRE_ALERT' || t.includes('DELETE')) return 'pill-danger';
    if (t.startsWith('PIPELINE') || t.startsWith('CAMERA') || t.startsWith('USER')) return 'pill-warn';
    return 'pill-neutral';
}
