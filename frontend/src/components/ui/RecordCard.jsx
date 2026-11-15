import React from 'react';

/**
 * Compact record row (icon, title, body, meta) on a `.raised` surface.
 * `accent` maps to a status edge only for status values (danger/warn/success/info); others are neutral.
 */
const STATUS = { danger: 'danger', rose: 'danger', red: 'danger', warn: 'warn', amber: 'warn', gold: 'warn', coral: 'warn', success: 'success', ok: 'success', emerald: 'success', green: 'success', info: 'info', sky: 'info', cyan: 'info' };

export default function RecordCard({
    icon: Icon,
    title,
    children = undefined,
    meta = undefined,
    accent = 'neutral',
    className = '',
}) {
    const tone = STATUS[accent] || 'neutral';
    return (
        <div className={`raised ui-record-card ui-record-card--${tone} ${className}`.trim()}>
            {Icon ? (
                <span className="ui-record-card-icon" aria-hidden>
                    <Icon size={16} strokeWidth={1.75} />
                </span>
            ) : null}
            <div className="ui-record-card-body">
                {title ? <div className="ui-record-card-title">{title}</div> : null}
                {children}
                {meta ? <div className="ui-record-card-meta">{meta}</div> : null}
            </div>
        </div>
    );
}
