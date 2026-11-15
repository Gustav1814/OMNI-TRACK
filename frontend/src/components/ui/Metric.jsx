/**
 * Metric: hero number with smaller decimals and unit, optional delta and sub-line.
 * Renders an honest empty value ("—") when `value` is null/undefined — never a placeholder number.
 */
import React from 'react';
import { ArrowUpRight, ArrowDownRight } from 'lucide-react';

function splitNumber(value, decimals) {
    if (typeof value !== 'number' || !Number.isFinite(value)) return { int: String(value), dec: '' };
    const fixed = value.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
    const sep = (1.1).toLocaleString().charAt(1);
    const i = decimals > 0 ? fixed.lastIndexOf(sep) : -1;
    return i < 0 ? { int: fixed, dec: '' } : { int: fixed.slice(0, i), dec: fixed.slice(i) };
}

export default function Metric({
    label, value, unit, decimals = 0, delta, deltaSuffix = '%', sub, size, className = '', as: Tag = 'div',
}) {
    const empty = value === null || value === undefined || (typeof value === 'number' && !Number.isFinite(value));
    const { int, dec } = empty ? { int: '—', dec: '' } : splitNumber(value, decimals);
    const hasDelta = typeof delta === 'number' && Number.isFinite(delta);
    const dir = hasDelta ? (delta > 0 ? 'up' : delta < 0 ? 'down' : 'flat') : null;
    const sizeClass = size ? ` metric--${size}` : '';
    return (
        <Tag className={`metric${sizeClass} ${className}`.trim()}>
            {label && <span className="metric-label">{label}</span>}
            <span className={`metric-value${empty ? ' metric-empty' : ''}`}>
                <span>{int}{dec && <span className="metric-decimal">{dec}</span>}</span>
                {!empty && unit && <span className="metric-unit">{unit}</span>}
                {!empty && hasDelta && (
                    <span className={`delta${dir === 'flat' ? '' : ` delta-${dir}`}`}>
                        {dir === 'up' && <ArrowUpRight size={11} aria-hidden />}
                        {dir === 'down' && <ArrowDownRight size={11} aria-hidden />}
                        {delta > 0 ? '+' : ''}{delta}{deltaSuffix}
                    </span>
                )}
            </span>
            {sub && <span className="metric-sub">{sub}</span>}
        </Tag>
    );
}

/**
 * Normalise a loosely-typed display value (number, "1,234", "12.5", "—", "$40") into Metric props.
 * Returns { value, decimals }. Placeholder dashes and empty strings become null (rendered as "—").
 * Numeric strings keep their own precision; numbers show at most `maxDecimals`.
 */
export function toMetricValue(raw, maxDecimals = 1) {
    if (raw === null || raw === undefined) return { value: null, decimals: 0 };
    if (typeof raw === 'number') {
        if (!Number.isFinite(raw)) return { value: null, decimals: 0 };
        if (Number.isInteger(raw)) return { value: raw, decimals: 0 };
        const places = (String(raw).split('.')[1] || '').length;
        return { value: raw, decimals: Math.min(places, maxDecimals) };
    }
    const s = String(raw).trim();
    if (s === '' || /^[—–-]+$/.test(s) || s.toLowerCase() === 'n/a') return { value: null, decimals: 0 };
    if (/^-?[\d,]+(\.\d+)?$/.test(s)) {
        const n = Number(s.replace(/,/g, ''));
        if (Number.isFinite(n)) return { value: n, decimals: (s.split('.')[1] || '').length };
    }
    return { value: s, decimals: 0 };
}
