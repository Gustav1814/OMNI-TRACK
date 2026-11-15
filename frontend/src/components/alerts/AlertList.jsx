/**
 * Alert rows: severity, store-local time, what happened, where, evidence
 * thumbnails and acknowledgement state. Clicking a row opens the evidence modal.
 */

import React from 'react';
import { Check, CheckCheck, Film } from 'lucide-react';
import { EvidenceThumb } from './EvidenceMedia';
import {
    DASH, cameraLabel, fmtStoreTime, kindLabel, relTime, severityTone, sourceLabel, titleCase,
} from './alertFormat';

export const alertKey = (a) => `${a.source}:${a.id}`;

export default function AlertList({ items, tz, isAdmin, onOpen, onAck, ackingKey = null }) {
    return (
        <div className="ops-alerts" role="list">
            <div className="ops-alert ops-alert--head" aria-hidden>
                <span>Severity</span>
                <span>Time</span>
                <span>Alert</span>
                <span>Where</span>
                <span>Evidence</span>
                <span className="ops-alert-end">Status</span>
            </div>
            {items.map((a) => {
                const key = alertKey(a);
                const tone = severityTone(a.severity);
                const evidence = Array.isArray(a.evidence) ? a.evidence : [];
                const snaps = evidence.filter((e) => e.kind === 'snapshot');
                const clips = evidence.filter((e) => e.kind === 'clip');
                const thumbs = [...snaps.slice(0, 2), ...(snaps.length ? [] : clips.slice(0, 1))];
                return (
                    <div
                        key={key}
                        role="listitem"
                        className={`ops-alert is-${tone} ${a.acked ? 'is-acked' : ''}`}
                        onClick={() => onOpen(a)}
                        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onOpen(a); } }}
                        tabIndex={0}
                        aria-label={`${kindLabel(a.kind)}, ${cameraLabel(a.camera_name, a.camera_id)}. Open evidence`}
                    >
                        <span className="ops-alert-sev">
                            <span className={`pill pill-${tone}`}>{a.severity ? titleCase(a.severity) : 'Alert'}</span>
                        </span>
                        <span className="ops-alert-cell ops-alert-time">
                            <strong>{fmtStoreTime(a.ts, tz, { seconds: false })}</strong>
                            <span>{relTime(a.ts)}</span>
                        </span>
                        <span className="ops-alert-cell ops-alert-what">
                            <strong>{kindLabel(a.kind)}</strong>
                            <span>{a.message || sourceLabel(a.source)}</span>
                        </span>
                        <span className="ops-alert-cell ops-alert-where">
                            <strong>{cameraLabel(a.camera_name, a.camera_id)}</strong>
                            <span>{a.zone_name || sourceLabel(a.source)}</span>
                        </span>
                        <span className="ops-alert-evidence">
                            {evidence.length === 0 ? <span className="ops-muted">{DASH}</span> : (
                                <>
                                    {thumbs.map((e) => <EvidenceThumb key={e.id} item={e} />)}
                                    {clips.length > 0 && snaps.length > 0 ? (
                                        <span className="ops-alert-clips" title={`${clips.length} clip(s)`}>
                                            <Film size={14} strokeWidth={1.75} aria-hidden /> {clips.length}
                                        </span>
                                    ) : null}
                                </>
                            )}
                        </span>
                        <span
                            className="ops-alert-status ops-alert-end"
                            onClick={(e) => e.stopPropagation()}
                            onKeyDown={(e) => e.stopPropagation()}
                            role="presentation"
                        >
                            {a.acked ? (
                                <span className="ops-acked" title={a.acked_at ? `Acknowledged ${fmtStoreTime(a.acked_at, tz)}` : 'Acknowledged'}>
                                    <CheckCheck size={14} strokeWidth={1.75} aria-hidden />
                                    <span>{a.acked_by ? a.acked_by : 'Acknowledged'}</span>
                                </span>
                            ) : isAdmin ? (
                                <button
                                    type="button"
                                    className="btn btn-secondary btn-xs"
                                    onClick={() => onAck(a)}
                                    disabled={ackingKey === key}
                                >
                                    <Check size={14} strokeWidth={1.75} /> {ackingKey === key ? 'Saving…' : 'Acknowledge'}
                                </button>
                            ) : (
                                <span className="pill pill-warn">Open</span>
                            )}
                        </span>
                    </div>
                );
            })}
        </div>
    );
}
