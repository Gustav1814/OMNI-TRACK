import React from 'react';

/**
 * Ring gauge. `accent` picks the fill token: status names (success/warn/danger) map to status colours,
 * 'neutral' to ink, anything else to the amber accent.
 */
const TONE = { success: 'success', ok: 'success', emerald: 'success', green: 'success', warn: 'warn', warning: 'warn', danger: 'danger', rose: 'danger', red: 'danger', neutral: 'neutral', ink: 'neutral' };

export default function ProgressRing({
    value = 0,
    max = 100,
    size = 88,
    stroke = 6,
    label,
    sublabel,
    accent = 'accent',
}) {
    const pct = Math.max(0, Math.min(100, (value / max) * 100 || 0));
    const r = (size - stroke) / 2;
    const circ = 2 * Math.PI * r;
    const offset = circ - (pct / 100) * circ;
    const tone = TONE[accent] || 'accent';

    return (
        <div className={`ui-ring ui-ring--${tone}`} style={{ width: size, height: size }}>
            <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true">
                <circle className="ui-ring-track" cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={stroke} />
                <circle
                    className="ui-ring-fill"
                    cx={size / 2}
                    cy={size / 2}
                    r={r}
                    fill="none"
                    strokeWidth={stroke}
                    strokeDasharray={circ}
                    strokeDashoffset={offset}
                    strokeLinecap="round"
                    transform={`rotate(-90 ${size / 2} ${size / 2})`}
                />
            </svg>
            <div className="ui-ring-center">
                {label != null ? <span className="ui-ring-value">{label}</span> : null}
                {sublabel ? <span className="ui-ring-sub">{sublabel}</span> : null}
            </div>
        </div>
    );
}
