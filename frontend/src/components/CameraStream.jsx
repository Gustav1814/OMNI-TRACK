/**
 * OmniTrack AI - Live MJPEG camera tile.
 * Consumes `/api/stream/camera/{id}/live?token=JWT`.
 */

import React, { useEffect, useRef, useState } from 'react';
import { Maximize2, Minimize2, RotateCw, X } from 'lucide-react';
import { liveStreamUrl } from '../services/api';

export default function CameraStream({
    cameraId,
    label,
    zone,
    fps,
    connected = true,
    finished = false,
    onClose = undefined,
    compact = false,
    detectionCount = undefined,
    trackCount = undefined,
    minimized = false,
    maximized = false,
    onMinimize = undefined,
    onMaximize = undefined,
}) {
    const [loaded, setLoaded] = useState(false);
    const [error, setError] = useState(false);
    const [url, setUrl] = useState(() => liveStreamUrl(cameraId));
    const imgRef = useRef(null);

    useEffect(() => {
        const nextUrl = liveStreamUrl(cameraId);
        setUrl(`${nextUrl}${nextUrl.includes('?') ? '&' : '?'}t=${Date.now()}`);
        setLoaded(false);
        setError(false);
    }, [cameraId]);

    const retry = () => {
        const nextUrl = liveStreamUrl(cameraId);
        setError(false);
        setLoaded(false);
        setUrl(`${nextUrl}${nextUrl.includes('?') ? '&' : '?'}t=${Date.now()}`);
    };

    const state = finished ? 'ended' : connected ? 'live' : 'offline';
    const stateLabel = finished ? 'Ended' : connected ? 'Rec' : 'Offline';
    const dotClass = state === 'live' ? 'is-live' : state === 'ended' ? '' : 'is-bad';
    const name = label || `Camera ${cameraId}`;
    const hasStats = detectionCount != null || trackCount != null;

    const controls = (restoreTitle) => (
        <span className="camera-control-group">
            {onMinimize && (
                <button className="camera-control" onClick={onMinimize} title={restoreTitle} aria-label={restoreTitle} type="button">
                    {minimized ? <Maximize2 size={14} strokeWidth={1.75} /> : <Minimize2 size={14} strokeWidth={1.75} />}
                </button>
            )}
            {onMaximize && !minimized && (
                <button
                    className="camera-control"
                    onClick={onMaximize}
                    title={maximized ? 'Restore feed size' : 'Maximize feed'}
                    aria-label={maximized ? 'Restore feed size' : 'Maximize feed'}
                    aria-pressed={maximized}
                    type="button"
                >
                    {maximized ? <Minimize2 size={14} strokeWidth={1.75} /> : <Maximize2 size={14} strokeWidth={1.75} />}
                </button>
            )}
            {onClose && (
                <button className="camera-control camera-control-danger" onClick={onClose} title="Stop feed" aria-label="Stop feed" type="button">
                    <X size={14} strokeWidth={1.75} />
                </button>
            )}
        </span>
    );

    return (
        <div
            className={`camera-tile camera-tile--${state} ${compact ? 'camera-tile-compact' : ''} ${minimized ? 'camera-tile-minimized' : ''} ${maximized ? 'camera-tile-maximized' : ''}`.replace(/\s+/g, ' ').trim()}
        >
            {minimized ? (
                <div className="camera-minimized-row">
                    <div className="camera-minimized-meta osd-strip">
                        <span className={`camera-live-badge camera-live-badge--${state}`}>
                            <span className={`live-dot ${dotClass}`} aria-hidden />
                            {stateLabel}
                        </span>
                        <strong>{name}</strong>
                        {zone && <span className="camera-zone">{zone}</span>}
                        {detectionCount != null && <span className="camera-stat">{detectionCount} dets</span>}
                        {trackCount != null && <span className="camera-stat">{trackCount} tracks</span>}
                    </div>
                    {controls('Restore feed')}
                </div>
            ) : (
                <div className="camera-tile-video vf vf--on-media">
                    {!error ? (
                        <img
                            ref={imgRef}
                            src={url}
                            alt={`Live view, ${name}`}
                            onLoad={() => setLoaded(true)}
                            onError={() => setError(true)}
                        />
                    ) : (
                        <div className="camera-tile-error" role="status">
                            <span className="camera-tile-error-title">Stream unavailable</span>
                            <span className="camera-tile-error-sub">The backend did not return frames for this feed.</span>
                            <button className="btn btn-secondary btn-xs" onClick={retry} type="button">
                                <RotateCw size={14} strokeWidth={1.75} aria-hidden />
                                Retry
                            </button>
                        </div>
                    )}
                    {!loaded && !error && <div className="camera-tile-loading" role="status">Connecting…</div>}
                    <div className="camera-tile-overlay">
                        <div className="camera-tile-top">
                            <div className="camera-osd osd-strip">
                                <span className={`camera-live-badge camera-live-badge--${state}`}>
                                    <span className={`live-dot ${dotClass}`} aria-hidden />
                                    {stateLabel}
                                </span>
                                <strong className="camera-label">{name}</strong>
                                {zone && <span className="camera-zone">{zone}</span>}
                            </div>
                            {controls('Minimize feed')}
                        </div>
                        {(fps != null || hasStats) && (
                            <div className="camera-tile-bottom">
                                <div className="camera-osd osd-strip camera-stats">
                                    {fps != null && <span className="camera-fps">{Number(fps).toFixed(1)} fps</span>}
                                    {detectionCount != null && <span>{detectionCount} dets</span>}
                                    {trackCount != null && <span>{trackCount} tracks</span>}
                                </div>
                            </div>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}
