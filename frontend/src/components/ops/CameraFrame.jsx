/**
 * Real camera imagery for the ops pages (Dashboard hero, Live standby wall):
 * the live MJPEG while the camera is analysed, else its latest snapshot.
 */

import React, { useEffect, useState } from 'react';
import { zonesAPI, liveStreamUrl } from '../../services/api';

/** Ticking store-time clock for the OSD (isolated so the page doesn't re-render every second). */
export function OsdClock({ tz }) {
    const [now, setNow] = useState(() => new Date());
    useEffect(() => {
        const id = setInterval(() => setNow(new Date()), 1000);
        return () => clearInterval(id);
    }, []);
    let text;
    try {
        text = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false, timeZone: tz || undefined });
    } catch {
        text = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
    }
    return <strong className="ops-tnum">{text}</strong>;
}

/**
 * A real camera frame: the live MJPEG when the camera is being analysed,
 * otherwise the camera's latest snapshot (authed blob). Never a placeholder image.
 */
export default function CameraFrame({ camera, live, tz, hero = false, className = '' }) {
    const id = camera?.id;
    const [liveFailed, setLiveFailed] = useState(false);
    const [snap, setSnap] = useState({ id: null, url: null, failed: false });
    const [liveUrl, setLiveUrl] = useState(null);
    const useLive = live && !liveFailed;

    useEffect(() => { setLiveFailed(false); }, [id, live]);
    useEffect(() => {
        if (!useLive || id == null) { setLiveUrl(null); return; }
        const u = liveStreamUrl(id);
        setLiveUrl(`${u}${u.includes('?') ? '&' : '?'}t=${Date.now()}`);
    }, [id, useLive]);
    useEffect(() => {
        if (useLive || id == null) return undefined;
        let cancelled = false;
        let url = null;
        zonesAPI.snapshot(id)
            .then((res) => {
                if (cancelled) return;
                url = URL.createObjectURL(res.data);
                setSnap({ id, url, failed: false });
            })
            .catch(() => { if (!cancelled) setSnap({ id, url: null, failed: true }); });
        return () => { cancelled = true; if (url) URL.revokeObjectURL(url); };
    }, [id, useLive]);

    const src = useLive ? liveUrl : (snap.id === id ? snap.url : null);
    const name = camera?.name || (id != null ? `Camera ${id}` : 'No camera');
    const state = useLive ? 'Rec' : src ? 'Standby' : 'No frame';

    return (
        <div className={`ops-frame vf vf--on-media${hero ? ' ops-frame--hero' : ''}${className ? ` ${className}` : ''}`}>
            {src ? (
                <img
                    className="ops-frame-img"
                    src={src}
                    alt={`${useLive ? 'Live view' : 'Latest frame'} from ${name}`}
                    onError={() => { if (useLive) setLiveFailed(true); }}
                />
            ) : (
                <span className="ops-frame-none">
                    {snap.failed || id == null ? 'No frame recorded yet' : 'Loading frame…'}
                </span>
            )}
            <span className="osd-strip ops-frame-osd">
                <span>
                    <span className={`live-dot ${useLive ? 'is-live' : ''}`} aria-hidden />
                    <strong>{state}</strong>
                </span>
                <span><strong>{name}</strong></span>
                {hero ? <span className="ops-frame-clock"><OsdClock tz={tz} /></span> : null}
            </span>
        </div>
    );
}

