/**
 * OmniTrack AI - Live MJPEG camera tile.
 * Consumes `/api/stream/camera/{id}/live?token=JWT`.
 */

import React, { useEffect, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import { Maximize2, Minimize2, X } from 'lucide-react';
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
    minimized = false,
    maximized = false,
    onMinimize,
    onMaximize,
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

    return (
        <motion.div
            className={`camera-tile ${compact ? 'camera-tile-compact' : ''} ${minimized ? 'camera-tile-minimized' : ''} ${maximized ? 'camera-tile-maximized' : ''}`}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.25 }}
        >
            {minimized ? (
                <div className="camera-minimized-row">
                    <div className="camera-minimized-meta">
                        <span className={`camera-live-badge ${connected ? '' : 'offline'}`}>
                            <span className="dot" />
                            {connected ? 'LIVE' : 'OFFLINE'}
                        </span>
                        <strong>{label || `Camera ${cameraId}`}</strong>
                        {zone && <span className="camera-zone">· {zone}</span>}
                    </div>
                    <div className="camera-minimized-actions">
                        {(detectionCount != null || trackCount != null) && (
                            <span className="camera-stats camera-stats-inline">
                                {detectionCount != null && <span>{detectionCount} dets</span>}
                                {trackCount != null && <span>{trackCount} tracks</span>}
                            </span>
                        )}
                        {onMinimize && (
                            <button className="camera-control" onClick={onMinimize} title="Restore feed" type="button">
                                <Maximize2 size={14} />
                            </button>
                        )}
                        {onClose && (
                            <button className="camera-control camera-control-danger" onClick={onClose} title="Stop feed" type="button">
                                <X size={14} />
                            </button>
                        )}
                    </div>
                </div>
            ) : (
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
                            <button className="btn-secondary btn-xs" onClick={retry} type="button">Retry</button>
                        </div>
                    )}
                    {!loaded && !error && <div className="camera-tile-loading">Connecting...</div>}
                    <div className="camera-tile-overlay">
                        <div className="camera-tile-top">
                            <div className="camera-tile-top-meta">
                                <span className={`camera-live-badge ${connected ? '' : 'offline'}`}>
                                    <span className="dot" />
                                    {connected ? 'LIVE' : 'OFFLINE'}
                                </span>
                                {fps != null && <span className="camera-fps">{Number(fps).toFixed(1)} fps</span>}
                            </div>
                            <span className="camera-control-group">
                                {onMinimize && (
                                    <button className="camera-control" onClick={onMinimize} title="Minimize feed" type="button">
                                        <Minimize2 size={14} />
                                    </button>
                                )}
                                {onMaximize && (
                                    <button className="camera-control" onClick={onMaximize} title={maximized ? 'Restore feed size' : 'Maximize feed'} type="button">
                                        {maximized ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
                                    </button>
                                )}
                                {onClose && (
                                    <button className="camera-control camera-control-danger" onClick={onClose} title="Stop feed" type="button">
                                        <X size={14} />
                                    </button>
                                )}
                            </span>
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
            )}
        </motion.div>
    );
}
