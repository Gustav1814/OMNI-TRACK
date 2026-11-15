/**
 * KPI card: OSD label row, hero number (via Metric), optional delta and progress meter.
 * `feature` makes it the page's one amber headline card (`.panel--feature` + viewfinder corners).
 * `accent` is accepted for backward compatibility and ignored.
 */
import React from 'react';
import Metric, { toMetricValue } from './ui/Metric';

export default function KPICard({
    icon: Icon,
    label,
    value,
    suffix = undefined,
    // eslint-disable-next-line no-unused-vars
    accent = undefined,
    progress = 0,
    tag = 'Today',
    hint = undefined,
    delta = undefined,
    deltaLabel = undefined,
    loading = false,
    feature = false,
    className = '',
}) {
    const cls = `panel kpi-card${feature ? ' panel--feature vf' : ''} ${className}`.trim();

    if (loading) {
        return (
            <div className={`${cls} kpi-card--loading`} aria-busy="true">
                <div className="kpi-card-top">
                    <span className="ui-skeleton ui-skeleton-line ui-skeleton-line--sm" />
                </div>
                <span className="ui-skeleton ui-skeleton-line ui-skeleton-line--lg" />
                <span className="ui-skeleton ui-skeleton-line ui-skeleton-line--xs" />
            </div>
        );
    }

    const { value: v, decimals } = toMetricValue(value);
    const unit = suffix ? String(suffix).trim() : undefined;
    const numericDelta = delta == null || Number.isNaN(Number(delta)) ? undefined : Number(delta);
    const pct = Math.max(0, Math.min(100, Number(progress) || 0));
    const sub = [deltaLabel && numericDelta !== undefined ? deltaLabel : null, hint].filter(Boolean).join(' · ') || undefined;

    return (
        <div className={cls}>
            <div className="kpi-card-top">
                <span className="kpi-card-label">
                    {Icon ? <Icon size={14} strokeWidth={1.75} aria-hidden /> : null}
                    {label}
                </span>
                {tag ? <span className="osd kpi-card-tag">{tag}</span> : null}
            </div>
            <Metric
                className="kpi-card-metric"
                value={v}
                decimals={decimals}
                unit={unit}
                delta={numericDelta}
                sub={sub}
                size={feature ? 'hero' : undefined}
            />
            {pct > 0 ? (
                <div className="kpi-card-meter" aria-hidden>
                    <span style={/** @type {React.CSSProperties} */ ({ '--value': `${pct}%` })} />
                </div>
            ) : null}
        </div>
    );
}
