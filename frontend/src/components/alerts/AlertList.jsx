/**
 * OmniTrack AI — Alert table
 *
 * Ported from VisRax's AlertList, with OmniTrack's columns: it carries a
 * dwell value against its threshold, which is the number that explains why the
 * alert fired at all.
 */

import React from 'react';
import { Camera, Check } from 'lucide-react';

function timeAgo(iso) {
    if (!iso) return '—';
    const then = new Date(iso).getTime();
    const secs = Math.max(0, Math.round((Date.now() - then) / 1000));
    if (secs < 60) return `${secs}s ago`;
    const mins = Math.round(secs / 60);
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.round(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.round(hrs / 24);
    if (days < 7) return `${days}d ago`;
    return new Date(iso).toLocaleDateString();
}

export default function AlertList({ items, onOpen, empty = 'No alerts.' }) {
    if (!items || items.length === 0) {
        return <div className="alerts-empty"><p>{empty}</p></div>;
    }

    return (
        <div className="alerts-table-wrap">
            <table className="alerts-table">
                <thead>
                    <tr>
                        <th>Message</th>
                        <th>Class</th>
                        <th>Region</th>
                        <th>Dwell</th>
                        <th>Job</th>
                        <th>Camera</th>
                        <th>Time</th>
                        <th aria-label="Actions" />
                    </tr>
                </thead>
                <tbody>
                    {items.map((alert) => (
                        <tr
                            key={alert.alert_id}
                            className={alert.acknowledged ? 'is-acked' : undefined}
                        >
                            <td className="alerts-table__msg">
                                {!alert.acknowledged && (
                                    <span className="alerts-table__dot" aria-label="Unacknowledged" />
                                )}
                                {alert.message}
                            </td>
                            <td><span className="badge">{alert.class_name || '—'}</span></td>
                            <td>
                                <span className="badge badge--accent">
                                    {alert.region_name || '—'}
                                </span>
                            </td>
                            <td className="mono alerts-table__dwell">
                                {alert.value != null
                                    ? <>{Math.round(alert.value)}s <em>/ {Math.round(alert.threshold ?? 0)}s</em></>
                                    : '—'}
                            </td>
                            <td className="mono alerts-table__dim">{alert.job_id}</td>
                            <td className="mono alerts-table__dim">
                                {alert.camera_id}
                                {alert.zone ? ` · ${alert.zone}` : ''}
                            </td>
                            <td title={alert.at_ts}>{timeAgo(alert.at_ts)}</td>
                            <td className="alerts-table__actions">
                                {alert.acknowledged && (
                                    <span className="alerts-table__ack" title="Acknowledged">
                                        <Check size={12} aria-hidden />
                                    </span>
                                )}
                                <button
                                    type="button"
                                    className="alerts-table__btn"
                                    onClick={() => onOpen?.(alert)}
                                    aria-label={`View evidence for alert ${alert.alert_id}`}
                                    title="View evidence"
                                >
                                    <Camera size={15} aria-hidden />
                                </button>
                            </td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}
