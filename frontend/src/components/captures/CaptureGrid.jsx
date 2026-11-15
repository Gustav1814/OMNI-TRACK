/**
 * Evidence grid grouped by store-local day, then by camera.
 */

import React, { useMemo } from 'react';
import { Hourglass } from 'lucide-react';
import { EvidenceThumb } from '../alerts/EvidenceMedia';
import { fmtStoreTime, formatBytes, relTime, sourceLabel, storeDay } from '../alerts/alertFormat';

/** Retention badge: how long until the file is purged. */
export function ExpiryBadge({ expiresAt, tz }) {
    if (!expiresAt) return <span className="pill pill-neutral pill-plain" title="No expiry set">Kept</span>;
    const ms = new Date(expiresAt).getTime() - Date.now();
    if (!Number.isFinite(ms)) return null;
    const tone = ms <= 0 ? 'danger' : ms < 24 * 3600 * 1000 ? 'warn' : 'neutral';
    return (
        <span className={`pill pill-${tone} pill-plain ops-expiry`} title={`Deleted automatically ${fmtStoreTime(expiresAt, tz)}`}>
            <Hourglass size={14} strokeWidth={1.75} aria-hidden /> {ms <= 0 ? 'Expired' : `Expires ${relTime(expiresAt)}`}
        </span>
    );
}

export function groupCaptures(items, tz, cameraName) {
    const days = new Map();
    items.forEach((it) => {
        const { key, label } = storeDay(it.created_at, tz);
        if (!days.has(key)) days.set(key, { key, label, cameras: new Map(), count: 0 });
        const day = days.get(key);
        day.count += 1;
        const camKey = String(it.camera_id ?? 'unknown');
        if (!day.cameras.has(camKey)) day.cameras.set(camKey, { id: it.camera_id, name: cameraName(it.camera_id), items: [] });
        day.cameras.get(camKey).items.push(it);
    });
    // items arrive newest-first; Map keeps insertion order, so days and cameras do too
    return [...days.values()].map((d) => ({ ...d, cameras: [...d.cameras.values()] }));
}

export default function CaptureGrid({ items, tz, cameraName, onOpen }) {
    const groups = useMemo(() => groupCaptures(items, tz, cameraName), [items, tz, cameraName]);
    return (
        <div className="ops-cap-days">
            {groups.map((day) => (
                <section key={day.key} className="ops-cap-day" aria-label={day.label}>
                    <header className="section-head">
                        <h3 className="section-title">{day.label}</h3>
                        <span className="count-badge">{day.count}</span>
                    </header>
                    {day.cameras.map((cam) => (
                        <div key={`${day.key}-${cam.id}`} className="ops-cap-cam">
                            <div className="osd-strip">
                                <span><strong>{cam.name}</strong></span>
                                <span>{cam.items.length} file{cam.items.length === 1 ? '' : 's'}</span>
                            </div>
                            <div className="ops-cap-grid">
                                {cam.items.map((it) => (
                                    <article key={it.id} className="ops-cap">
                                        <div className="ops-cap-media vf vf--on-media">
                                            <EvidenceThumb
                                                item={it}
                                                size="lg"
                                                onClick={() => onOpen(it)}
                                                title={`Open ${it.kind === 'clip' ? 'clip' : 'snapshot'} from ${fmtStoreTime(it.created_at, tz)}`}
                                            />
                                        </div>
                                        <div className="ops-cap-meta">
                                            <div className="ops-cap-row">
                                                <strong className="num">{fmtStoreTime(it.created_at, tz, { date: false })}</strong>
                                                <span className={`pill pill-plain ${it.kind === 'clip' ? 'pill-accent' : 'pill-neutral'}`}>
                                                    {it.kind === 'clip' ? 'Clip' : 'Snapshot'}
                                                </span>
                                            </div>
                                            <div className="ops-cap-row ops-cap-sub">
                                                <span>{sourceLabel(it.source)}</span>
                                                <span className="num">{formatBytes(it.bytes)}</span>
                                            </div>
                                            <ExpiryBadge expiresAt={it.expires_at} tz={tz} />
                                        </div>
                                    </article>
                                ))}
                            </div>
                        </div>
                    ))}
                </section>
            ))}
        </div>
    );
}
