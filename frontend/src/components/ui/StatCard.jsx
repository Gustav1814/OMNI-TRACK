import React from 'react';
import Metric, { toMetricValue } from './Metric';

/**
 * Calm stat panel: label row + number (via Metric) + optional delta.
 * `accent` is accepted for backward compatibility and ignored. `variant="compact"` uses the small size.
 */
export default function StatCard({
    icon: Icon,
    label,
    value,
    suffix = '',
    // eslint-disable-next-line no-unused-vars
    accent = undefined,
    delta = undefined,
    deltaLabel = undefined,
    variant = 'default',
    loading = false,
    className = '',
}) {
    const compact = variant === 'compact';
    const cls = `panel ui-stat ui-stat--${variant} ${className}`.trim();

    if (loading) {
        return (
            <div className={`${cls} ui-stat--loading`} aria-busy="true">
                <span className="ui-skeleton ui-skeleton-line ui-skeleton-line--sm" />
                <span className="ui-skeleton ui-skeleton-line ui-skeleton-line--lg" />
            </div>
        );
    }

    const { value: v, decimals } = toMetricValue(value);
    const numericDelta = delta == null || Number.isNaN(Number(delta)) ? undefined : Number(delta);

    return (
        <div className={cls}>
            <span className="ui-stat-label">
                {Icon ? <Icon size={14} strokeWidth={1.75} aria-hidden /> : null}
                {label}
            </span>
            <Metric
                className="ui-stat-metric"
                value={v}
                decimals={decimals}
                unit={suffix ? String(suffix).trim() : undefined}
                delta={numericDelta}
                sub={numericDelta !== undefined && deltaLabel ? deltaLabel : undefined}
                size={compact ? 'sm' : undefined}
            />
        </div>
    );
}
