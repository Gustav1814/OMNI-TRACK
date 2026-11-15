/**
 * Formatted number. Kept for API compatibility: it no longer animates
 * (animated tickers fake motion). Renders the real value immediately.
 */
import React from 'react';

// eslint-disable-next-line no-unused-vars
export default function CountUp({ value, duration = undefined, suffix = '', prefix = '', format = (n) => n }) {
    if (typeof value === 'string' && value.includes(',')) return <span className="tabular">{value}</span>;
    const num = typeof value === 'number' ? value : parseFloat(String(value).replace(/[^0-9.-]/g, ''));
    if (!Number.isFinite(num)) return <span className="tabular">—</span>;
    return <span className="tabular">{`${prefix}${format(Math.round(num * 10) / 10)}${suffix}`}</span>;
}
