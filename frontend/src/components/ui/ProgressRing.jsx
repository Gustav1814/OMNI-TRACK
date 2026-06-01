import React from 'react';

export default function ProgressRing({
    value = 0,
    max = 100,
    size = 88,
    stroke = 6,
    label,
    sublabel,
    accent = 'teal',
}) {
    const pct = Math.max(0, Math.min(100, (value / max) * 100));
    const r = (size - stroke) / 2;
    const circ = 2 * Math.PI * r;
    const offset = circ - (pct / 100) * circ;

    return (
        <div className={`ui-ring ui-ring--${accent}`} style={{ width: size, height: size }}>
            <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
                <circle
                    className="ui-ring-track"
                    cx={size / 2}
                    cy={size / 2}
                    r={r}
                    fill="none"
                    strokeWidth={stroke}
                />
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
