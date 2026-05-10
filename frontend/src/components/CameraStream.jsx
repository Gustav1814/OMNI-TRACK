/**
 * OmniTrack AI — Live MJPEG camera tile
 * Consumes `/api/stream/camera/{id}/live?token=JWT` and renders it with an
 * overlay for camera name, live badge, FPS, and optional close button.
 */

import React, { useRef, useState, useEffect, useCallback } from 'react';
import { motion } from 'framer-motion';
import { Maximize2, Minimize2 } from 'lucide-react';
import { liveStreamUrl } from '../services/api';

export default function CameraStream({
    cameraId,
    label,
    zone,
    fps,
    connected = true,
    onClose,
    compact = false,
    detectionCount,
    trackCount,
}) {
    const [loaded, setLoaded] = useState(false);
    const [error, setError] = useState(false);
    const [url, setUrl] = useState(() => liveStreamUrl(cameraId));
    const [expanded, setExpanded] = useState(false);
    const imgRef = useRef(null);

    // Refresh with a cache-buster when camera id changes or mount.
    useEffect(() => {
        setUrl(`${liveStreamUrl(cameraId)}${liveStreamUrl(cameraId).includes('?') ? '&' : '?'}t=${Date.now()}`);
        setLoaded(false);
        setError(false);
    }, [cameraId]);

    const retry = () => {
        setError(false);
        setLoaded(false);
        setUrl(`${liveStreamUrl(cameraId)}${liveStreamUrl(cameraId).includes('?') ? '&' : '?'}t=${Date.now()}`);
    };

    const toggleExpanded = useCallback((e) => {
        e?.stopPropagation?.();
        setExpanded((v) => !v);
    }, []);

    useEffect(() => {
        if (!expanded) return undefined;
        const onKey = (ev) => { if (ev.key === 'Escape') setExpanded(false); };
        const prevOverflow = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
        window.addEventListener('keydown', onKey);
        return () => {
            window.removeEventListener('keydown', onKey);
            document.body.style.overflow = prevOverflow;
        };
    }, [expanded]);

    return (
        <motion.div
            className={`camera-tile ${compact ? 'camera-tile-compact' : ''} ${expanded ? 'camera-tile-expanded' : ''}`}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.25 }}
            onDoubleClick={toggleExpanded}
        >
            <div className="camera-tile-video">
                {!error ? (
                    <img
                        ref={imgRef}
                        src={url}
                        alt={`Camera ${cameraId}`}
                        onLoad={() => setLoaded(true)}
                        onError={() => setError(true)}
                    />
                ) : (
                    <div className="camera-tile-error">
                        <div>Stream unavailable</div>
                        <button className="btn-secondary btn-xs" onClick={retry}>Retry</button>
                    </div>
                )}
                {!loaded && !error && <div className="camera-tile-loading">Connecting…</div>}
                <div className="camera-tile-overlay">
                    <div className="camera-tile-top">
                        <span className={`camera-live-badge ${connected ? '' : 'offline'}`}>
                            <span className="dot" />
                            {connected ? 'LIVE' : 'OFFLINE'}
                        </span>
                        {fps != null && <span className="camera-fps">{Number(fps).toFixed(1)} fps</span>}
                        <button
                            type="button"
                            className="camera-close"
                            onClick={(e) => { e.stopPropagation(); toggleExpanded(e); }}
                            title={expanded ? 'Collapse (Esc)' : 'Expand'}
                            style={{ pointerEvents: 'auto' }}
                        >
                            {expanded ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
                        </button>
                        {onClose && (
                            <button
                                type="button"
                                className="camera-close"
                                onClick={(e) => { e.stopPropagation(); onClose(e); }}
                                title="Stop"
                                style={{ pointerEvents: 'auto' }}
                            >
                                ×
                            </button>
                        )}
                    </div>
                    <div className="camera-tile-bottom">
                        <div className="camera-label">
                            <strong>{label || `Camera ${cameraId}`}</strong>
                            {zone && <span className="camera-zone">· {zone}</span>}
                        </div>
                        {(detectionCount != null || trackCount != null) && (
                            <div className="camera-stats">
                                {detectionCount != null && <span>{detectionCount} dets</span>}
                                {trackCount != null && <span>{trackCount} tracks</span>}
                            </div>
                        )}
                    </div>
                </div>
            </div>
        </motion.div>
    );
}
