/**
 * Shared formatting + small UI bits for the zone-analytics pages.
 * Everything renders "—" for null: a missing measurement is never shown as 0.
 */

import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { ArrowUpRight } from 'lucide-react';
import Metric from '../ui/Metric';
import { zonesAPI } from '../../services/api';

export const DASH = '—';

export const isNum = (v) => typeof v === 'number' && Number.isFinite(v);

export function fmtNum(v, digits = 0) {
    if (!isNum(v)) return DASH;
    return v.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}

export function fmtPct(fraction, digits = 0) {
    if (!isNum(fraction)) return DASH;
    return `${(fraction * 100).toFixed(digits)} %`;
}

export function fmtDuration(seconds) {
    if (!isNum(seconds)) return DASH;
    const s = Math.round(seconds);
    if (s < 60) return `${s} s`;
    if (s < 3600) {
        const m = Math.floor(s / 60);
        const r = s % 60;
        return r ? `${m} min ${r} s` : `${m} min`;
    }
    const h = Math.floor(s / 3600);
    const m = Math.round((s % 3600) / 60);
    return m ? `${h} h ${m} min` : `${h} h`;
}

/** Duration as Metric parts: { value, unit, decimals }. Null stays null ("—"). */
export function durationMetric(seconds) {
    if (!isNum(seconds)) return { value: null, unit: undefined, decimals: 0 };
    if (seconds < 60) return { value: Math.round(seconds), unit: 's', decimals: 0 };
    if (seconds < 3600) return { value: seconds / 60, unit: 'min', decimals: 1 };
    return { value: seconds / 3600, unit: 'h', decimals: 1 };
}

/** Fraction as Metric parts (0.42 -> 42 %). */
export function pctMetric(fraction, decimals = 0) {
    return isNum(fraction) ? { value: fraction * 100, unit: '%', decimals } : { value: null, unit: undefined, decimals };
}

export const hourLabel = (h) => (isNum(h) ? `${String(h).padStart(2, '0')}:00` : DASH);

export function fmtTime(iso, withDate = false) {
    if (!iso) return DASH;
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return DASH;
    return withDate
        ? d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
        : d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
}

export const unwrap = (data) => (data && typeof data === 'object' ? data : null);

export const WINDOWS = [
    { value: '', label: 'Today' },
    { value: '24', label: '24 h' },
    { value: '168', label: '7 days' },
];

/** Segmented pills: real buttons with aria-pressed. Active = ink fill (base .seg). */
export function Seg({ value, onChange, options, label }) {
    return (
        <div className="seg" role="group" aria-label={label}>
            {options.map((o) => (
                <button
                    key={String(o.value)}
                    type="button"
                    aria-pressed={String(o.value) === String(value)}
                    onClick={() => onChange(o.value)}
                >
                    {o.label}
                </button>
            ))}
        </div>
    );
}

export function WindowSelect({ value, onChange, options = WINDOWS }) {
    return <Seg value={value} onChange={onChange} options={options} label="Time window" />;
}

/** Page header: huge title, one plain sentence, actions on the right. */
export function AnalyticsHeader({ title, subtitle, children }) {
    return (
        <header className="page-header">
            <div>
                <h1 className="page-title">{title}</h1>
                {subtitle ? <p className="page-subtitle">{subtitle}</p> : null}
            </div>
            {children ? <div className="page-header-actions">{children}</div> : null}
        </header>
    );
}

/**
 * Page shell. Standalone: page wrapper + header. Embedded in a hub: just the
 * sections, with the page's filters in a compact bar at the top.
 */
export function AnalyticsPage({ embedded = false, title, subtitle, controls, children }) {
    if (embedded) {
        return (
            <div className="an-embed" role="group" aria-label={title}>
                {controls ? <div className="an-embed-bar">{controls}</div> : null}
                {children}
            </div>
        );
    }
    return (
        <div className="an-page">
            <AnalyticsHeader title={title} subtitle={subtitle}>{controls}</AnalyticsHeader>
            {children}
        </div>
    );
}

/** Standard panel with title, optional one-line sub and actions. */
export function Panel({ title, sub, actions, children, className = '', ...rest }) {
    return (
        <section className={`panel an-panel ${className}`.trim()} {...rest}>
            <div className="panel-body">
                {(title || actions) ? (
                    <div className="panel-head">
                        <div>
                            {title ? <h2 className="panel-title">{title}</h2> : null}
                            {sub ? <p className="panel-sub">{sub}</p> : null}
                        </div>
                        {actions ? <div className="an-actions">{actions}</div> : null}
                    </div>
                ) : null}
                {children}
            </div>
        </section>
    );
}

/**
 * Flush top section (no card, hairline on top): the page's ONE giant number on the
 * left, an optional visual on the right (a real camera frame), and the hairline KPI
 * strip. Without an aside the strip sits to the right of the hero.
 */
export function HeroRow({ hero, lead, aside, children }) {
    return (
        <section className={`panel panel--flush an-top${aside ? ' an-top--aside' : ''}`} aria-label={hero.label}>
            <div className="panel-body an-top-body">
                <div className="an-hero">
                    {lead}
                    <Metric label={hero.label} value={hero.value} unit={hero.unit} decimals={hero.decimals || 0} size="hero" />
                    {hero.foot ? <p className="an-hero-foot">{hero.foot}</p> : null}
                </div>
                {aside ? <div className="an-top-aside">{aside}</div> : null}
                {children ? <div className="kpi-strip an-top-strip">{children}</div> : null}
            </div>
        </section>
    );
}

/**
 * The one amber card per page. Amber only while it holds a measured value; with no
 * value it renders as a plain card, so the accent never frames an empty "—".
 */
export function FeatureCard({ label, value, unit, decimals = 0, sub, to, toLabel, children }) {
    const has = value !== null && value !== undefined && value !== '';
    return (
        <section className={`panel an-feature vf${has ? ' panel--feature' : ''}`} aria-label={label}>
            <div className="panel-body">
                <div className="an-feature-head">
                    <span className="metric-label">{label}</span>
                    {to ? (
                        <Link to={to} className="corner-link" aria-label={toLabel || `Open ${label}`}>
                            <ArrowUpRight size={16} strokeWidth={1.75} aria-hidden />
                        </Link>
                    ) : null}
                </div>
                <Metric value={has ? value : null} unit={unit} decimals={decimals} sub={sub} className="an-feature-metric" />
                {children}
            </div>
        </section>
    );
}

/**
 * A real frame from a camera: GET /api/zones/cameras/{id}/snapshot (authed blob),
 * refreshed every few seconds while the camera is analysed. Viewfinder brackets and an
 * OSD strip on top. When no frame can be read it says so; nothing is substituted.
 */
export function CameraFrame({ camera, live = false, refreshMs = 5000 }) {
    const [src, setSrc] = useState(null);
    const [source, setSource] = useState(null);
    const [failed, setFailed] = useState(false);
    const [at, setAt] = useState(null);
    const id = camera?.id;

    useEffect(() => {
        if (id == null) return undefined;
        let alive = true;
        let url = null;
        const load = async () => {
            try {
                const res = await zonesAPI.snapshot(id);
                if (!alive) return;
                const next = URL.createObjectURL(res.data);
                if (url) URL.revokeObjectURL(url);
                url = next;
                setSrc(next);
                setSource(res.headers?.['x-snapshot-source'] || null);
                setAt(new Date());
                setFailed(false);
            } catch {
                if (alive) setFailed(true);
            }
        };
        load();
        const timer = live ? setInterval(load, refreshMs) : null;
        return () => {
            alive = false;
            if (timer) clearInterval(timer);
            if (url) URL.revokeObjectURL(url);
        };
    }, [id, live, refreshMs]);

    if (id == null) return null;
    const isLive = live && source !== 'source';
    return (
        <figure className="an-frame vf vf--on-media">
            {src && !failed ? (
                <img src={src} alt={`Latest frame from ${camera.name || `camera ${id}`}`} />
            ) : (
                <div className="an-frame-empty">{failed ? 'No frame could be read from this camera' : 'Reading a frame'}</div>
            )}
            <figcaption className="osd-strip an-frame-osd">
                <span><span className={`live-dot${isLive ? ' is-live' : ''}`} aria-hidden />{isLive ? 'Rec' : 'Still'}</span>
                <strong>{camera.name || `Camera ${id}`}</strong>
                <span>{at ? at.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' }) : DASH}</span>
            </figcaption>
        </figure>
    );
}

/** How a metric is defined (from the API's `definitions`). */
export function Definitions({ items }) {
    const entries = Object.entries(items || {});
    if (!entries.length) return null;
    return (
        <details className="an-defs">
            <summary>How these numbers are measured</summary>
            <dl>
                {entries.map(([k, v]) => (
                    <React.Fragment key={k}>
                        <dt>{k.replace(/_/g, ' ')}</dt>
                        <dd>{typeof v === 'string' ? v : JSON.stringify(v)}</dd>
                    </React.Fragment>
                ))}
            </dl>
        </details>
    );
}

/** Honest empty state: what is missing, why, and the next step. */
export function NoData({ title, children, setupLink = false, size }) {
    return (
        <div className={`empty-state an-empty${size === 'sm' ? ' an-empty--sm' : ''}`}>
            <div className="empty-state-title">{title}</div>
            {children ? <p>{children}</p> : null}
            {setupLink ? (
                <Link to="/setup/zones" className="btn btn-secondary btn-sm">Draw zones in setup</Link>
            ) : null}
        </div>
    );
}
