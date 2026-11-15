/**
 * Tape: OmniTrack's dense tick timeline (counts over time, a playhead at "now").
 *
 *   <Tape data={[{ t: 9, label: '09:00', value: 42 }, …]} playhead={idx} axisLabels={['00', '06', '12', '18', '24']} />
 *
 * - `data`: numbers or `{ t, label, value }`. Empty data renders nothing (the caller shows the empty state).
 * - Ticks are thin SVG rects; height is proportional to value. Zero values keep a short stub so the tape reads continuous.
 * - Ticks at or above `thresholds.mid` / `thresholds.high` get `.is-mid` / `.is-high`
 *   (defaults: median and 90th percentile of the non-zero values).
 * - Every tick has a hover tooltip and a <title>; screen readers get a table (or a summary for long series).
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';

function quantile(sorted, q) {
    if (!sorted.length) return 0;
    const pos = (sorted.length - 1) * q;
    const lo = Math.floor(pos);
    const hi = Math.ceil(pos);
    return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
}

function normalise(data) {
    return (Array.isArray(data) ? data : []).map((d, i) => {
        if (typeof d === 'number') return { t: i, label: String(i), value: Number.isFinite(d) ? d : 0 };
        const value = Number(d?.value);
        const t = d?.t ?? i;
        return { t, label: d?.label != null ? String(d.label) : String(t), value: Number.isFinite(value) ? value : 0 };
    });
}

const fmt = (v) => (Number.isInteger(v) ? v.toLocaleString() : v.toLocaleString(undefined, { maximumFractionDigits: 2 }));

export default function Tape({
    data,
    height = 96,
    thresholds = undefined,
    playhead = null,
    axisLabels = undefined,
    ariaLabel = 'Activity timeline',
    className = '',
}) {
    const items = useMemo(() => normalise(data), [data]);
    const wrapRef = useRef(null);
    const [width, setWidth] = useState(0);
    const [hover, setHover] = useState(null);

    useEffect(() => {
        const el = wrapRef.current;
        if (!el) return undefined;
        const measure = () => setWidth(el.clientWidth || 0);
        measure();
        if (typeof ResizeObserver === 'undefined') return undefined;
        const ro = new ResizeObserver(measure);
        ro.observe(el);
        return () => ro.disconnect();
    }, [items.length]);

    const { mid, high, max } = useMemo(() => {
        const nonZero = items.map((d) => d.value).filter((v) => v > 0).sort((a, b) => a - b);
        return {
            max: nonZero.length ? nonZero[nonZero.length - 1] : 0,
            mid: thresholds?.mid ?? quantile(nonZero, 0.5),
            high: thresholds?.high ?? quantile(nonZero, 0.9),
        };
    }, [items, thresholds]);

    if (!items.length) return null;

    const n = items.length;
    const W = width > 0 ? width : n * 4;
    const H = height;
    const step = W / n;
    const tickW = Math.max(1, Math.min(3, step * 0.45));
    const stub = 2;
    const top = 6; // headroom for the playhead cap
    const usable = H - top;
    const heightOf = (v) => (max > 0 && v > 0 ? Math.max(stub + 1, (v / max) * usable) : stub);
    const level = (v) => (v <= 0 ? '' : v >= high ? ' is-high' : v >= mid ? ' is-mid' : '');
    const hasPlayhead = Number.isInteger(playhead) && playhead >= 0 && playhead < n;
    const hovered = hover != null ? items[hover] : null;

    const total = items.reduce((s, d) => s + d.value, 0);
    const peak = items.reduce((p, d) => (d.value > p.value ? d : p), items[0]);

    return (
        <figure className={`tape ui-tape ${className}`.trim()} style={/** @type {React.CSSProperties} */ ({ '--tape-h': `${H}px` })}>
            <div className="ui-tape-plot" ref={wrapRef} onMouseLeave={() => setHover(null)}>
                <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true" focusable="false">
                    <line className="ui-tape-base" x1="0" x2={W} y1={H - 0.5} y2={H - 0.5} />
                    {items.map((d, i) => {
                        const h = heightOf(d.value);
                        const cx = (i + 0.5) * step;
                        return (
                            <rect
                                key={`${d.t}-${i}`}
                                className={`tape-tick${level(d.value)}${hover === i ? ' is-hover' : ''}`}
                                x={cx - tickW / 2}
                                y={H - h}
                                width={tickW}
                                height={h}
                                rx={Math.min(1, tickW / 2)}
                            />
                        );
                    })}
                    {hasPlayhead ? (
                        <g className="ui-tape-playhead">
                            <line className="tape-playhead" x1={(playhead + 0.5) * step} x2={(playhead + 0.5) * step} y1={top - 2} y2={H} />
                            <rect className="ui-tape-playhead-cap" x={(playhead + 0.5) * step - 3} y={0} width={6} height={4} rx={1} />
                        </g>
                    ) : null}
                    {/* full-height hit areas: thin ticks are hard to hover */}
                    {items.map((d, i) => (
                        <rect
                            key={`hit-${d.t}-${i}`}
                            className="ui-tape-hit"
                            x={i * step}
                            y={0}
                            width={step}
                            height={H}
                            onMouseEnter={() => setHover(i)}
                        >
                            <title>{`${d.label}: ${fmt(d.value)}`}</title>
                        </rect>
                    ))}
                </svg>
                {hovered ? (
                    <div
                        className={`ui-tape-tip${(hover + 0.5) / n < 0.12 ? ' is-start' : (hover + 0.5) / n > 0.88 ? ' is-end' : ''}`}
                        role="presentation"
                        style={/** @type {React.CSSProperties} */ ({ '--tip-x': `${((hover + 0.5) / n) * 100}%` })}
                    >
                        <span className="ui-tape-tip-label">{hovered.label}</span>
                        <span className="ui-tape-tip-value">{fmt(hovered.value)}</span>
                    </div>
                ) : null}
            </div>
            {axisLabels?.length ? (
                <div className="tape-axis" aria-hidden="true">
                    {axisLabels.map((l, i) => <span key={`${l}-${i}`}>{l}</span>)}
                </div>
            ) : null}
            {n <= 96 ? (
                <table className="sr-only">
                    <caption>{ariaLabel}</caption>
                    <thead><tr><th scope="col">Time</th><th scope="col">Value</th></tr></thead>
                    <tbody>
                        {items.map((d, i) => (
                            <tr key={`sr-${d.t}-${i}`}><th scope="row">{d.label}</th><td>{fmt(d.value)}{i === playhead ? ' (now)' : ''}</td></tr>
                        ))}
                    </tbody>
                </table>
            ) : (
                <figcaption className="sr-only">
                    {`${ariaLabel}: ${n} points from ${items[0].label} to ${items[n - 1].label}. Total ${fmt(total)}. Peak ${fmt(peak.value)} at ${peak.label}.`}
                </figcaption>
            )}
        </figure>
    );
}
