/**
 * OmniTrack AI — Live MJPEG camera tile
 * Consumes `/api/stream/camera/{id}/live?token=JWT` and renders it with an
 * overlay for camera name, live badge, FPS, and optional close button.
 */

import React, { useRef, useState, useEffect } from 'react';
import { motion } from 'framer-motion';
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

    return (
        <motion.div
            className={`camera-tile ${compact ? 'camera-tile-compact' : ''}`}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.25 }}
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
                        {onClose && (
                            <button className="camera-close" onClick={onClose} title="Stop">×</button>
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
