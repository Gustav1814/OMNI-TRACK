/**
 * Alert detail + evidence (snapshot and the 8–10 s clip around the alert).
 * ←/→ step through the alerts on the current page; Esc closes.
 */

import React from 'react';
import { Check, CheckCheck, ChevronLeft, ChevronRight } from 'lucide-react';
import Modal from './Modal';
import { EvidenceViewer } from './EvidenceMedia';
import {
    DASH, cameraLabel, fmtDurationBetween, fmtStoreTime, kindLabel, severityTone, sourceLabel, titleCase,
} from './alertFormat';

function Detail({ label, children }) {
    return (
        <>
            <dt>{label}</dt>
            <dd>{children ?? DASH}</dd>
        </>
    );
}

export default function AlertEvidenceModal({
    alert, index, count, tz, isAdmin, acking, onAck, onPrev, onNext, onClose,
}) {
    const tone = severityTone(alert.severity);
    const duration = alert.ended_at ? fmtDurationBetween(alert.ts, alert.ended_at) : null;
    const value = alert.value;
    const hasValue = value !== null && value !== undefined && value !== '';

    const footer = (
        <div className="ops-modal-foot">
            <div className="ops-modal-nav">
                <button type="button" className="btn btn-secondary btn-sm" onClick={onPrev} disabled={!onPrev} aria-label="Previous alert (←)">
                    <ChevronLeft size={16} strokeWidth={1.75} /> Prev
                </button>
                <span className="ops-modal-pos">{index + 1} / {count}</span>
                <button type="button" className="btn btn-secondary btn-sm" onClick={onNext} disabled={!onNext} aria-label="Next alert (→)">
                    Next <ChevronRight size={16} strokeWidth={1.75} />
                </button>
            </div>
            {alert.acked ? (
                <span className="ops-acked">
                    <CheckCheck size={16} strokeWidth={1.75} aria-hidden /> Acknowledged{alert.acked_by ? ` by ${alert.acked_by}` : ''}
                    {alert.acked_at ? ` · ${fmtStoreTime(alert.acked_at, tz)}` : ''}
                </span>
            ) : isAdmin ? (
                <button type="button" className="btn btn-primary btn-sm" onClick={() => onAck(alert)} disabled={acking}>
                    <Check size={16} strokeWidth={1.75} /> {acking ? 'Saving…' : 'Acknowledge'}
                </button>
            ) : (
                <span className="ops-muted">Only admins can acknowledge alerts.</span>
            )}
        </div>
    );

    return (
        <Modal
            title={<span className="ops-modal-title"><span className={`pill pill-${tone}`}>{alert.severity ? titleCase(alert.severity) : 'Alert'}</span> {kindLabel(alert.kind)}</span>}
            subtitle={`${cameraLabel(alert.camera_name, alert.camera_id)}${alert.zone_name ? ` · ${alert.zone_name}` : ''} · ${fmtStoreTime(alert.ts, tz)}`}
            onClose={onClose}
            onPrev={onPrev}
            onNext={onNext}
            footer={footer}
        >
            <div className="ops-evidence">
                <div className="ops-evidence-media">
                    <EvidenceViewer evidence={Array.isArray(alert.evidence) ? alert.evidence : []} />
                </div>
                <div className="ops-evidence-info">
                    {alert.message ? <p className="ops-evidence-msg">{alert.message}</p> : null}
                    <dl className="ops-dl">
                        <Detail label="Source">{sourceLabel(alert.source)}</Detail>
                        <Detail label="Type">{kindLabel(alert.kind)}</Detail>
                        <Detail label="Camera">{cameraLabel(alert.camera_name, alert.camera_id)}</Detail>
                        <Detail label="Zone">{alert.zone_name || null}</Detail>
                        <Detail label="Raised">{fmtStoreTime(alert.ts, tz)}</Detail>
                        <Detail label="Ended">
                            {alert.ended_at ? `${fmtStoreTime(alert.ended_at, tz)}${duration ? ` (${duration})` : ''}` : 'Still open'}
                        </Detail>
                        {hasValue ? (
                            <Detail label="Measured">
                                {typeof value === 'number' ? value.toLocaleString(undefined, { maximumFractionDigits: 2 }) : String(value)}
                            </Detail>
                        ) : null}
                        <Detail label="Reference"><span className="mono">{`${alert.source} #${alert.id}`}</span></Detail>
                    </dl>
                    {tz ? <p className="ops-evidence-note">Times in store time ({tz}).</p> : null}
                </div>
            </div>
        </Modal>
    );
}
