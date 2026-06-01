import React from 'react';
import CountUp from '../CountUp';
import DeltaBadge from './DeltaBadge';
import useGradientColors from '../../hooks/useGradientColors';
import { resolveAccentRgb } from '../../lib/gradientPresets';

const ACCENT_ALIAS = {
    indigo: 'teal',
    violet: 'teal',
    gold: 'coral',
    amber: 'coral',
};

function resolveAccent(accent) {
    return ACCENT_ALIAS[accent] || accent || 'teal';
}

function isNumericValue(value) {
    if (typeof value === 'number') return true;
    if (typeof value === 'string' && /^-?\d+(\.\d+)?$/.test(value.trim())) return true;
    return false;
}

export default function StatCard({
    icon: Icon,
    label,
    value,
    suffix = '',
    accent = 'teal',
    delta,
    deltaLabel,
    variant = 'default',
    loading = false,
    className = '',
}) {
    const { preset } = useGradientColors();
    const resolved = resolveAccent(accent);
    const rgb = resolveAccentRgb(resolved, preset);
    const numeric = isNumericValue(value);

    if (loading) {
        return (
            <div className={`ui-stat ui-stat--${variant} ui-stat--loading ${className}`.trim()} style={{ '--stat-rgb': rgb }}>
                <div className="ui-skeleton ui-skeleton-icon" />
                <div className="ui-skeleton ui-skeleton-line ui-skeleton-line--sm" />
                <div className="ui-skeleton ui-skeleton-line ui-skeleton-line--lg" />
            </div>
        );
    }

    return (
        <div
            className={`ui-stat ui-stat--${variant} ui-stat--${resolved} ${className}`.trim()}
            style={{ '--stat-rgb': rgb }}
        >
            {Icon ? (
                <div className="ui-stat-icon">
                    <Icon size={variant === 'compact' ? 15 : 17} strokeWidth={2} />
                </div>
            ) : null}
            <div className="ui-stat-body">
                <span className="ui-stat-label">{label}</span>
                <div className="ui-stat-value-row">
                    <span className="ui-stat-value">
                        {numeric ? <CountUp value={Number(value)} suffix={suffix} /> : <>{value}{suffix}</>}
                    </span>
                    {delta != null ? <DeltaBadge value={delta} label={deltaLabel} size="xs" /> : null}
                </div>
            </div>
        </div>
    );
}
