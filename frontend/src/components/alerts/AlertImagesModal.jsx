/**
 * OmniTrack AI — Alert evidence modal
 *
 * Ported from VisRax's ImagesModal: framed dark viewer, prev/next through the
 * images, prev/next through the alerts themselves, a View Video action and an
 * inline Acknowledge.
 *
 * One difference from VisRax, which serves one stored snapshot per alert:
 * OmniTrack has no stored snapshot, so the backend resolves the crops of that
 * tracked object around the alert instead. That usually returns several, which
 * makes the image pager genuinely useful — you see the object arrive, settle,
 * and trip the threshold.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import {
    Check, ChevronLeft, ChevronRight, ImageOff, Loader2, Video, X,
} from 'lucide-react';
import { alertsAPI, artifactsAPI } from '../../services/api';
import AlertVideoModal from './AlertVideoModal';

export default function AlertImagesModal({
    alert, alerts = [], onSelect, open, onClose, onAck, acking = false,
}) {
    const [images, setImages] = useState([]);
    const [index, setIndex] = useState(0);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);

    const [videoOpen, setVideoOpen] = useState(false);
    const [video, setVideo] = useState(null);
    const [videoLoading, setVideoLoading] = useState(false);
    const [videoError, setVideoError] = useState(null);

    useEffect(() => {
        if (!open || !alert) return undefined;
        let alive = true;
        setLoading(true);
        setError(null);
        setImages([]);
        setIndex(0);
        setVideo(null);
        setVideoError(null);

        alertsAPI.images(alert.alert_id)
            .then((r) => {
                if (!alive) return;
                const list = r.data?.images ?? [];
                setImages(list);
                if (list.length === 0) {
                    setError('No crops were saved for this object around the alert.');
                }
            })
            .catch((e) => alive && setError(e?.response?.data?.detail || e.message))
            .finally(() => alive && setLoading(false));

        return () => { alive = false; };
    }, [alert, open]);

    const alertIndex = useMemo(
        () => (alert ? alerts.findIndex((a) => a.alert_id === alert.alert_id) : -1),
        [alert, alerts],
    );
    const hasPrevAlert = alertIndex > 0;
    const hasNextAlert = alertIndex >= 0 && alertIndex < alerts.length - 1;

    const goPrevAlert = useCallback(() => {
        if (hasPrevAlert) onSelect?.(alerts[alertIndex - 1]);
    }, [hasPrevAlert, onSelect, alerts, alertIndex]);

    const goNextAlert = useCallback(() => {
        if (hasNextAlert) onSelect?.(alerts[alertIndex + 1]);
    }, [hasNextAlert, onSelect, alerts, alertIndex]);

    const onKey = useCallback((e) => {
        // While the video is up it owns Escape and the arrows.
        if (videoOpen) return;
        if (e.key === 'Escape') onClose();
        else if (e.key === 'ArrowLeft') goPrevAlert();
        else if (e.key === 'ArrowRight') goNextAlert();
    }, [videoOpen, onClose, goPrevAlert, goNextAlert]);

    useEffect(() => {
        if (!open) return undefined;
        document.addEventListener('keydown', onKey);
        const prev = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
        return () => {
            document.removeEventListener('keydown', onKey);
            document.body.style.overflow = prev;
        };
    }, [open, onKey]);

    const openVideo = async () => {
        if (!alert || videoLoading) return;
        setVideoLoading(true);
        setVideoError(null);
        try {
            const r = await alertsAPI.video(alert.alert_id);
            setVideo(r.data);
            setVideoOpen(true);
        } catch (e) {
            setVideoError(e?.response?.data?.detail || e.message);
        } finally {
            setVideoLoading(false);
        }
    };

    if (!open) return null;

    const current = images[index] ?? null;

    return createPortal(
        <div
            className="alerts-modal-backdrop"
            role="dialog"
            aria-modal="true"
            aria-label="Alert evidence"
            onClick={onClose}
        >
            <div className="alert-images" onClick={(e) => e.stopPropagation()}>
                <header className="alert-images__head">
                    <div>
                        <h2 className="alert-images__title">
                            Evidence for: {alert?.class_name ?? '—'}
                        </h2>
                        <p className="alert-images__sub">
                            {alert?.message ?? 'Captures of the tracked object around this alert.'}
                        </p>
                    </div>
                    <div className="alert-images__head-right">
                        <div className="alert-images__meta">
                            <span className="alert-images__meta-item">
                                Object ID <span className="mono">{alert?.track_id ?? '—'}</span>
                            </span>
                            <span className="alert-images__meta-item">
                                Alert ID <span className="mono">{alert?.alert_id ?? '—'}</span>
                            </span>
                            {alertIndex >= 0 && alerts.length > 0 && (
                                <span className="alert-images__meta-item alert-images__stepper">
                                    <button
                                        type="button"
                                        onClick={goPrevAlert}
                                        disabled={!hasPrevAlert}
                                        aria-label="Previous alert"
                                        title="Previous alert (←)"
                                    >
                                        <ChevronLeft size={13} />
                                    </button>
                                    Alert {alertIndex + 1} of {alerts.length}
                                    <button
                                        type="button"
                                        onClick={goNextAlert}
                                        disabled={!hasNextAlert}
                                        aria-label="Next alert"
                                        title="Next alert (→)"
                                    >
                                        <ChevronRight size={13} />
                                    </button>
                                </span>
                            )}
                        </div>
                        <button type="button" className="alert-modal__close" onClick={onClose} aria-label="Close">
                            <X size={16} />
                        </button>
                    </div>
                </header>

                <div className="alert-images__stage">
                    {loading && (
                        <div className="alert-images__status">
                            <Loader2 size={24} className="spin" />
                            <span>Loading evidence…</span>
                        </div>
                    )}

                    {!loading && error && (
                        <div className="alert-images__status">
                            <ImageOff size={24} />
                            <span>{error}</span>
                        </div>
                    )}

                    {current && (
                        <img
                            className="alert-images__img"
                            src={artifactsAPI.fileUrl(current.filename)}
                            alt={`Capture ${index + 1} for alert ${alert?.alert_id}`}
                        />
                    )}

                    {videoError && !videoLoading && !videoOpen && (
                        <div className="alert-images__video-error">{videoError}</div>
                    )}

                    <div className="alert-images__overlay">
                        <button
                            type="button"
                            className="alert-images__video-btn"
                            onClick={() => void openVideo()}
                            disabled={videoLoading}
                        >
                            {videoLoading ? <Loader2 size={13} className="spin" /> : <Video size={13} />}
                            {videoLoading ? 'Loading…' : 'View video'}
                        </button>
                        {alert && (alert.acknowledged ? (
                            <span className="alert-images__ack alert-images__ack--done">
                                <Check size={12} /> Acknowledged
                            </span>
                        ) : (
                            <button
                                type="button"
                                className="alert-images__ack"
                                onClick={() => onAck(alert.alert_id)}
                                disabled={acking}
                            >
                                <Check size={12} /> {acking ? 'Acknowledging…' : 'Acknowledge'}
                            </button>
                        ))}
                    </div>

                    {images.length > 1 && (
                        <>
                            <button
                                type="button"
                                className="alert-images__nav alert-images__nav--prev"
                                onClick={() => setIndex((i) => Math.max(0, i - 1))}
                                disabled={index === 0}
                                aria-label="Previous image"
                            >
                                <ChevronLeft size={20} />
                            </button>
                            <button
                                type="button"
                                className="alert-images__nav alert-images__nav--next"
                                onClick={() => setIndex((i) => Math.min(images.length - 1, i + 1))}
                                disabled={index >= images.length - 1}
                                aria-label="Next image"
                            >
                                <ChevronRight size={20} />
                            </button>
                        </>
                    )}
                </div>

                {images.length > 0 && (
                    <div className="alert-images__counter">
                        {index + 1} / {images.length}
                        {current?.captured_at && (
                            <span className="alert-images__counter-time">
                                {new Date(current.captured_at).toLocaleTimeString()}
                            </span>
                        )}
                    </div>
                )}
            </div>

            <AlertVideoModal
                open={videoOpen}
                videoUrl={video ? artifactsAPI.fileUrl(video.filename) : null}
                posterUrl={video ? artifactsAPI.thumbUrl(video.filename) : null}
                onClose={() => { setVideoOpen(false); setVideo(null); }}
            />
        </div>,
        document.body,
    );
}
