import React from 'react';
import useGradientColors from '../../hooks/useGradientColors';
import { resolveAccentRgb } from '../../lib/gradientPresets';

const ACCENT_ALIAS = {
    indigo: 'teal',
    violet: 'teal',
    gold: 'coral',
    amber: 'coral',
    cyan: 'sky',
};

function resolveAccent(accent) {
    return ACCENT_ALIAS[accent] || accent || 'neutral';
}

export default function RecordCard({
    icon: Icon,
    title,
    children,
    meta,
    accent = 'neutral',
    className = '',
}) {
    const { preset } = useGradientColors();
    const resolved = resolveAccent(accent);
    const rgb = resolved === 'neutral'
        ? '100, 116, 139'
        : resolveAccentRgb(resolved, preset);

    return (
        <div
            className={`ui-record-card ui-record-card--${accent} ${className}`.trim()}
            style={{ '--record-rgb': rgb }}
        >
            {Icon ? (
                <div className="ui-record-card-icon">
                    <Icon size={18} strokeWidth={2} />
                </div>
            ) : null}
            <div className="ui-record-card-body">
                {title ? <div className="ui-record-card-title">{title}</div> : null}
                {children}
                {meta ? <div className="ui-record-card-meta">{meta}</div> : null}
            </div>
        </div>
    );
}
