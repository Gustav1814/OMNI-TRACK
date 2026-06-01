import React from 'react';

export function Skeleton({ className = '', style }) {
    return <div className={`ui-skeleton ${className}`.trim()} style={style} aria-hidden="true" />;
}

export function SkeletonKpiGrid({ count = 6, cols = 6 }) {
    return (
        <div className="ui-skeleton-kpi-grid" style={{ '--sk-cols': cols }}>
            {Array.from({ length: count }).map((_, i) => (
                <div key={i} className="ui-skeleton-kpi">
                    <Skeleton className="ui-skeleton-icon" />
                    <Skeleton className="ui-skeleton-line ui-skeleton-line--sm" />
                    <Skeleton className="ui-skeleton-line ui-skeleton-line--lg" />
                    <Skeleton className="ui-skeleton-bar" />
                </div>
            ))}
        </div>
    );
}

export function SkeletonChart({ height = 280 }) {
    return (
        <div className="ui-skeleton-chart" style={{ height }}>
            <Skeleton className="ui-skeleton-chart-line" />
        </div>
    );
}

export function SkeletonTable({ rows = 5 }) {
    return (
        <div className="ui-skeleton-table">
            <Skeleton className="ui-skeleton-line ui-skeleton-line--head" />
            {Array.from({ length: rows }).map((_, i) => (
                <Skeleton key={i} className="ui-skeleton-line ui-skeleton-line--row" />
            ))}
        </div>
    );
}

export function SkeletonCameraGrid({ count = 2 }) {
    return (
        <div className="ui-skeleton-camera-grid">
            {Array.from({ length: count }).map((_, i) => (
                <Skeleton key={i} className="ui-skeleton-camera" />
            ))}
        </div>
    );
}
