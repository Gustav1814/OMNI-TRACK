/**
 * Full-size viewer for one evidence file with ←/→ through the loaded list.
 */

import React from 'react';
import { Link } from 'react-router-dom';
import { Bell, ChevronLeft, ChevronRight, Download } from 'lucide-react';
import Modal from '../alerts/Modal';
import { EvidenceViewer } from '../alerts/EvidenceMedia';
import { DASH, fmtStoreTime, formatBytes, sourceLabel } from '../alerts/alertFormat';
import { evidenceAPI } from '../../services/api';
import { ExpiryBadge } from './CaptureGrid';

export default function CaptureViewer({ item, index, count, tz, cameraName, onPrev, onNext, onClose }) {
    const camera = cameraName(item.camera_id);
    const ext = item.media_type === 'video/webm' ? 'webm' : item.kind === 'clip' ? 'mp4' : 'jpg';
    const alertLink = `/incidents/alerts?${new URLSearchParams({
        ...(item.source ? { source: item.source } : {}),
        ...(item.camera_id != null ? { camera_id: String(item.camera_id) } : {}),
    }).toString()}`;

    return (
        <Modal
            title={`${item.kind === 'clip' ? 'Clip' : 'Snapshot'} · ${camera}`}
            subtitle={fmtStoreTime(item.created_at, tz)}
            onClose={onClose}
            onPrev={onPrev}
            onNext={onNext}
            footer={(
                <div className="ops-modal-foot">
                    <div className="ops-modal-nav">
                        <button type="button" className="btn btn-secondary btn-sm" onClick={onPrev} disabled={!onPrev} aria-label="Previous capture (←)">
                            <ChevronLeft size={16} strokeWidth={1.75} /> Prev
                        </button>
                        <span className="ops-modal-pos">{index + 1} / {count}</span>
                        <button type="button" className="btn btn-secondary btn-sm" onClick={onNext} disabled={!onNext} aria-label="Next capture (→)">
                            Next <ChevronRight size={16} strokeWidth={1.75} />
                        </button>
                    </div>
                    <div className="ops-modal-nav">
                        <Link to={alertLink} className="btn btn-secondary btn-sm" onClick={onClose}>
                            <Bell size={16} strokeWidth={1.75} /> Related alerts
                        </Link>
                        <a className="btn btn-primary btn-sm" href={evidenceAPI.fileUrl(item.id)}
                            download={`evidence_${item.id}_cam${item.camera_id ?? 'x'}.${ext}`}>
                            <Download size={16} strokeWidth={1.75} /> Download
                        </a>
                    </div>
                </div>
            )}
        >
            <div className="ops-evidence">
                <div className="ops-evidence-media">
                    <EvidenceViewer evidence={[{ id: item.id, kind: item.kind }]} emptyText="File unavailable." />
                </div>
                <div className="ops-evidence-info">
                    <dl className="ops-dl">
                        <dt>Camera</dt><dd>{camera}</dd>
                        <dt>Captured</dt><dd>{fmtStoreTime(item.created_at, tz)}</dd>
                        <dt>From</dt><dd>{sourceLabel(item.source)}{item.source_id != null ? ` #${item.source_id}` : ''}</dd>
                        <dt>Size</dt><dd>{formatBytes(item.bytes)}</dd>
                        <dt>Retention</dt>
                        <dd className="ops-dd-stack">
                            <span>{item.expires_at ? `Deleted ${fmtStoreTime(item.expires_at, tz)}` : DASH}</span>
                            <ExpiryBadge expiresAt={item.expires_at} tz={tz} />
                        </dd>
                    </dl>
                    <p className="ops-evidence-note">Stored encrypted and decrypted only for viewing.{tz ? ` Times in store time (${tz}).` : ''}</p>
                </div>
            </div>
        </Modal>
    );
}
