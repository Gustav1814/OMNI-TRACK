/**
 * OmniTrack AI — Alert video modal
 *
 * Ported from VisRax's VideoModal. Plays the recorded segment containing the
 * alert; the boxes and timestamps are burned into the clip by the pipeline, so
 * a plain <video> is all this needs.
 */

import React, { useCallback, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';

export default function AlertVideoModal({ videoUrl, posterUrl, open, onClose }) {
    const onKey = useCallback((e) => { if (e.key === 'Escape') onClose(); }, [onClose]);

    useEffect(() => {
        if (!open) return undefined;
        document.addEventListener('keydown', onKey);
        return () => document.removeEventListener('keydown', onKey);
    }, [open, onKey]);

    if (!open) return null;

    return createPortal(
        <div
            className="alerts-modal-backdrop"
            role="dialog"
            aria-modal="true"
            aria-label="Alert video"
            onClick={onClose}
        >
            <div className="alert-video" onClick={(e) => e.stopPropagation()}>
                <header className="alert-video__head">
                    <div>
                        <h2 className="alert-video__title">Video containing frame</h2>
                        <p className="alert-video__sub">
                            The recorded segment covering the moment this alert fired.
                        </p>
                    </div>
                    <button type="button" className="alert-modal__close" onClick={onClose} aria-label="Close">
                        <X size={16} />
                    </button>
                </header>

                <div className="alert-video__stage">
                    {videoUrl ? (
                        <video
                            className="alert-video__player"
                            src={videoUrl}
                            poster={posterUrl || undefined}
                            controls
                            autoPlay
                            playsInline
                        />
                    ) : (
                        <p className="alert-video__empty">No video segment available.</p>
                    )}
                </div>
            </div>
        </div>,
        document.body,
    );
}
