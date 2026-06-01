import React from 'react';
import useGradientColors from '../../hooks/useGradientColors';

function resolveTileRgb(resolved, aRgb, bRgb) {
    switch (resolved) {
        case 'ok':
            return 'var(--tone-success-rgb)';
        case 'warn':
            return 'var(--tone-warn-rgb)';
        case 'sky':
            return bRgb;
        case 'rose':
            return 'var(--tone-danger-rgb)';
        case 'neutral':
            return 'var(--tone-neutral-rgb)';
        case 'coral':
        case 'teal':
        default:
            return aRgb;
    }
}

function resolveTone(ok, tone) {
    if (tone) return tone;
    if (ok === true) return 'ok';
    if (ok === false) return 'warn';
    return 'neutral';
}

export default function StatusTile({
    icon: Icon,
    label,
    value,
    ok,
    tone,
    compact = false,
}) {
    const { aRgb, bRgb } = useGradientColors();
    const resolved = resolveTone(ok, tone);
    const rgb = resolveTileRgb(resolved, aRgb, bRgb);

    return (
        <div
            className={`ui-status-tile ui-status-tile--${resolved}${compact ? ' ui-status-tile--compact' : ''}`}
            style={{ '--tile-rgb': rgb }}
        >
            <div className="ui-status-tile-top">
                {Icon ? (
                    <span className="ui-status-tile-icon">
                        <Icon size={14} strokeWidth={2} />
                    </span>
                ) : null}
                <span className="ui-status-tile-label">{label}</span>
            </div>
            <div className={`ui-status-tile-value ui-status-tile-value--${resolved}`}>
                {String(value)}
            </div>
        </div>
    );
}
