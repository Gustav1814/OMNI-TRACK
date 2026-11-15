/**
 * Alerts filter bar. Options and counts come from /api/alerts/facets, so they
 * never offer a value that cannot match anything.
 *
 *  - Source: chips with counts (AlertSourceChips)
 *  - Status: segmented control (AlertStatusSeg)
 *  - Camera / type / time range: selects (default export)
 */

import React from 'react';
import { RotateCcw } from 'lucide-react';
import { cameraLabel, kindLabel, sourceLabel } from './alertFormat';

export const RANGE_OPTIONS = [
    { value: '1h', label: 'Last hour', hours: 1 },
    { value: '24h', label: 'Last 24 hours', hours: 24 },
    { value: '7d', label: 'Last 7 days', hours: 24 * 7 },
    { value: '30d', label: 'Last 30 days', hours: 24 * 30 },
    { value: 'all', label: 'All time' },
    { value: 'custom', label: 'Custom range…' },
];

export const DEFAULT_FILTERS = {
    source: '', camera_id: '', kind: '', acked: '', range: 'all', since: '', until: '',
};

const STATUS_OPTIONS = [
    { value: '', label: 'All' },
    { value: 'false', label: 'Open' },
    { value: 'true', label: 'Acknowledged' },
];

/** Convert UI filters into API params (ISO times). */
export function filtersToParams(f) {
    const p = {
        source: f.source || undefined,
        camera_id: f.camera_id || undefined,
        kind: f.kind || undefined,
        acked: f.acked || undefined,
    };
    const preset = RANGE_OPTIONS.find((r) => r.value === f.range);
    if (preset?.hours) {
        p.since = new Date(Date.now() - preset.hours * 3600 * 1000).toISOString();
    } else if (f.range === 'custom') {
        const since = f.since ? new Date(f.since) : null;
        const until = f.until ? new Date(f.until) : null;
        if (since && !Number.isNaN(since.getTime())) p.since = since.toISOString();
        if (until && !Number.isNaN(until.getTime())) p.until = until.toISOString();
    }
    return p;
}

const fmtCount = (n) => (n != null ? Number(n).toLocaleString() : null);
const withCount = (label, n) => (n != null ? `${label} (${fmtCount(n)})` : label);

/** Source chips with counts. "All" carries the sum of the source facets. */
export function AlertSourceChips({ value, onChange, facets }) {
    const sources = Object.entries(facets?.sources || {});
    const list = value && !sources.some(([k]) => k === value) ? [[value, null], ...sources] : sources;
    const all = facets ? sources.reduce((s, [, n]) => s + Number(n || 0), 0) : null;
    return (
        <div className="chip-row" role="group" aria-label="Filter by source">
            <button type="button" className="chip" aria-pressed={!value} onClick={() => onChange({ source: '' })}>
                All sources{all != null ? <span className="chip-count">{fmtCount(all)}</span> : null}
            </button>
            {list.map(([k, n]) => (
                <button
                    key={k}
                    type="button"
                    className="chip"
                    aria-pressed={value === k}
                    onClick={() => onChange({ source: value === k ? '' : k })}
                >
                    {sourceLabel(k)}{n != null ? <span className="chip-count">{fmtCount(n)}</span> : null}
                </button>
            ))}
        </div>
    );
}

/** Acknowledgement status as a segmented control; "Open" shows the unacked count. */
export function AlertStatusSeg({ value, onChange, facets }) {
    return (
        <div className="seg" role="group" aria-label="Filter by status">
            {STATUS_OPTIONS.map((o) => (
                <button key={o.value || 'all'} type="button" aria-pressed={value === o.value} onClick={() => onChange({ acked: o.value })}>
                    {o.value === 'false' && facets?.unacked != null ? `${o.label} · ${fmtCount(facets.unacked)}` : o.label}
                </button>
            ))}
        </div>
    );
}

export default function AlertFilters({ filters, onChange, facets, onReset }) {
    const kinds = Object.entries(facets?.kinds || {});
    const cameras = Array.isArray(facets?.cameras) ? facets.cameras : [];
    const dirty = Object.keys(DEFAULT_FILTERS).some((k) => filters[k] !== DEFAULT_FILTERS[k]);
    const kindList = filters.kind && !kinds.some(([k]) => k === filters.kind) ? [[filters.kind, null], ...kinds] : kinds;

    return (
        <div className="ops-filter-bar">
            <div className="form-group">
                <label className="form-label" htmlFor="alerts-camera">Camera</label>
                <select id="alerts-camera" className="form-select" value={filters.camera_id} onChange={(e) => onChange({ camera_id: e.target.value })}>
                    <option value="">All cameras</option>
                    {filters.camera_id && !cameras.some((c) => String(c.id) === String(filters.camera_id)) ? (
                        <option value={filters.camera_id}>{cameraLabel(null, filters.camera_id)}</option>
                    ) : null}
                    {cameras.map((c) => (
                        <option key={c.id} value={String(c.id)}>{withCount(cameraLabel(c.name, c.id), c.n)}</option>
                    ))}
                </select>
            </div>
            <div className="form-group">
                <label className="form-label" htmlFor="alerts-kind">Type</label>
                <select id="alerts-kind" className="form-select" value={filters.kind} onChange={(e) => onChange({ kind: e.target.value })}>
                    <option value="">All types</option>
                    {kindList.map(([k, n]) => (
                        <option key={k} value={k}>{withCount(kindLabel(k), n)}</option>
                    ))}
                </select>
            </div>
            <div className="form-group">
                <label className="form-label" htmlFor="alerts-range">Time range</label>
                <select id="alerts-range" className="form-select" value={filters.range} onChange={(e) => onChange({ range: e.target.value })}>
                    {RANGE_OPTIONS.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
                </select>
            </div>
            {filters.range === 'custom' ? (
                <>
                    <div className="form-group">
                        <label className="form-label" htmlFor="alerts-since">From (local time)</label>
                        <input id="alerts-since" type="datetime-local" className="form-input" value={filters.since} max={filters.until || undefined}
                            onChange={(e) => onChange({ since: e.target.value })} />
                    </div>
                    <div className="form-group">
                        <label className="form-label" htmlFor="alerts-until">To</label>
                        <input id="alerts-until" type="datetime-local" className="form-input" value={filters.until} min={filters.since || undefined}
                            onChange={(e) => onChange({ until: e.target.value })} />
                    </div>
                </>
            ) : null}
            <div className="ops-filter-reset">
                <button type="button" className="btn btn-ghost btn-sm" onClick={onReset} disabled={!dirty}>
                    <RotateCcw size={16} strokeWidth={1.75} /> Reset filters
                </button>
            </div>
        </div>
    );
}
