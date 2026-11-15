/**
 * Minimal accessible modal used by the Alerts and Captures viewers.
 * Portal to <body>; Esc closes; ←/→ call onPrev/onNext; focus is moved into
 * the dialog on open and restored to the opener on close.
 */

import React, { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';

export default function Modal({
    title,
    subtitle = undefined,
    onClose,
    onPrev = undefined,
    onNext = undefined,
    footer = undefined,
    size = 'lg',
    children,
}) {
    const dialogRef = useRef(null);
    const handlers = useRef({ onClose, onPrev, onNext });
    handlers.current = { onClose, onPrev, onNext };

    useEffect(() => {
        const opener = document.activeElement;
        dialogRef.current?.focus();
        const prevOverflow = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
        const onKey = (e) => {
            const tag = (e.target?.tagName || '').toLowerCase();
            const typing = tag === 'input' || tag === 'textarea' || tag === 'select';
            if (e.key === 'Escape') { e.stopPropagation(); handlers.current.onClose?.(); }
            else if (!typing && e.key === 'ArrowLeft' && handlers.current.onPrev) { e.preventDefault(); handlers.current.onPrev(); }
            else if (!typing && e.key === 'ArrowRight' && handlers.current.onNext) { e.preventDefault(); handlers.current.onNext(); }
        };
        window.addEventListener('keydown', onKey);
        return () => {
            window.removeEventListener('keydown', onKey);
            document.body.style.overflow = prevOverflow;
            if (opener && typeof opener.focus === 'function') opener.focus();
        };
    }, []);

    return createPortal(
        <div className="fp-modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose?.(); }}>
            <div
                ref={dialogRef}
                className={`fp-modal fp-modal--${size}`}
                role="dialog"
                aria-modal="true"
                aria-label={typeof title === 'string' ? title : undefined}
                tabIndex={-1}
            >
                <header className="fp-modal-head">
                    <div className="fp-modal-heading">
                        <h3 className="fp-modal-title">{title}</h3>
                        {subtitle ? <p className="fp-modal-sub">{subtitle}</p> : null}
                    </div>
                    <button type="button" className="fp-icon-btn" onClick={onClose} aria-label="Close (Esc)" title="Close (Esc)">
                        <X size={16} />
                    </button>
                </header>
                <div className="fp-modal-body">{children}</div>
                {footer ? <footer className="fp-modal-foot">{footer}</footer> : null}
            </div>
        </div>,
        document.body,
    );
}
