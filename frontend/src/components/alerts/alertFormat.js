/**
 * Shared formatting for the Alerts and Captures pages.
 *
 * Times are shown in the STORE's time zone (settings.STORE_TIMEZONE), not the
 * browser's, so "14:05" means the same thing to everyone looking at the store.
 * The zone comes from the API response when it carries `timezone`; otherwise it
 * is read once per session from an analytics endpoint that reports it.
 */

import { useEffect, useState } from 'react';
import { peakHoursAPI } from '../../services/api';

export const DASH = '—';

let tzPromise = null;
let tzCached = null;

function isValidTz(tz) {
    if (!tz || typeof tz !== 'string') return false;
    try { new Intl.DateTimeFormat(undefined, { timeZone: tz }); return true; } catch { return false; }
}

function loadStoreTz() {
    if (!tzPromise) {
        tzPromise = peakHoursAPI.weekly(1)
            .then((r) => (isValidTz(r?.data?.timezone) ? r.data.timezone : null))
            .catch(() => null)
            .then((tz) => { tzCached = tz; return tz; });
    }
    return tzPromise;
}

/** Store IANA time zone; `hint` (e.g. response.timezone) wins when valid. */
export function useStoreTimezone(hint) {
    const [tz, setTz] = useState(isValidTz(hint) ? hint : tzCached);
    useEffect(() => {
        if (isValidTz(hint)) { tzCached = hint; setTz(hint); return undefined; }
        let alive = true;
        loadStoreTz().then((v) => { if (alive && v) setTz(v); });
        return () => { alive = false; };
    }, [hint]);
    return tz; // null => browser local time
}

const parse = (iso) => {
    if (!iso) return null;
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? null : d;
};

export function fmtStoreTime(iso, tz, { date = true, seconds = true } = {}) {
    const d = parse(iso);
    if (!d) return DASH;
    return d.toLocaleString(undefined, {
        ...(tz ? { timeZone: tz } : {}),
        ...(date ? { month: 'short', day: 'numeric' } : {}),
        hour: '2-digit',
        minute: '2-digit',
        ...(seconds ? { second: '2-digit' } : {}),
    });
}

/** Stable day key (YYYY-MM-DD) and a readable label, both in the store zone. */
export function storeDay(iso, tz) {
    const d = parse(iso);
    if (!d) return { key: 'unknown', label: 'Unknown date' };
    const parts = new Intl.DateTimeFormat('en-CA', {
        ...(tz ? { timeZone: tz } : {}), year: 'numeric', month: '2-digit', day: '2-digit',
    }).formatToParts(d);
    const get = (t) => parts.find((p) => p.type === t)?.value;
    const key = `${get('year')}-${get('month')}-${get('day')}`;
    const today = storeDayKey(new Date(), tz);
    const yesterday = storeDayKey(new Date(Date.now() - 86400000), tz);
    const label = key === today ? 'Today' : key === yesterday ? 'Yesterday'
        : d.toLocaleDateString(undefined, {
            ...(tz ? { timeZone: tz } : {}), weekday: 'long', month: 'long', day: 'numeric', year: 'numeric',
        });
    return { key, label };
}

function storeDayKey(d, tz) {
    return new Intl.DateTimeFormat('en-CA', {
        ...(tz ? { timeZone: tz } : {}), year: 'numeric', month: '2-digit', day: '2-digit',
    }).format(d);
}

export function relTime(iso, now = Date.now()) {
    const d = parse(iso);
    if (!d) return '';
    const s = Math.round((now - d.getTime()) / 1000);
    const abs = Math.abs(s);
    const fmt = (n, u) => (s >= 0 ? `${n} ${u} ago` : `in ${n} ${u}`);
    if (abs < 45) return s >= 0 ? 'just now' : 'in a moment';
    if (abs < 3600) return fmt(Math.round(abs / 60), 'min');
    if (abs < 86400) return fmt(Math.round(abs / 3600), 'h');
    return fmt(Math.round(abs / 86400), abs < 172800 ? 'day' : 'days');
}

export function fmtDurationBetween(fromIso, toIso) {
    const a = parse(fromIso);
    const b = parse(toIso);
    if (!a || !b) return null;
    const s = Math.max(0, Math.round((b - a) / 1000));
    if (s < 60) return `${s} s`;
    if (s < 3600) return `${Math.floor(s / 60)} min ${s % 60 ? `${s % 60} s` : ''}`.trim();
    return `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min`;
}

export function formatBytes(n) {
    const v = Number(n);
    if (!Number.isFinite(v) || v <= 0) return DASH;
    if (v < 1024) return `${v} B`;
    if (v < 1024 * 1024) return `${Math.round(v / 1024)} KB`;
    return `${(v / (1024 * 1024)).toFixed(1)} MB`;
}

export const SOURCE_LABEL = {
    zone_event: 'Zone alert',
    fire_incident: 'Fire / smoke',
    humanless: 'Smart store',
};

export const sourceLabel = (s) => SOURCE_LABEL[s] || titleCase(s);

export function titleCase(value) {
    if (value == null || value === '') return DASH;
    return String(value).replace(/[_-]+/g, ' ').replace(/\b\w/g, (m) => m.toUpperCase());
}

const KIND_LABEL = {
    overcrowding: 'Overcrowding',
    crowd: 'Overcrowding',
    long_queue: 'Long queue',
    queue: 'Long queue',
    dwell_overrun: 'Dwell overrun',
    intrusion: 'Restricted area',
    fire: 'Fire',
    smoke: 'Smoke',
};

export const kindLabel = (k) => KIND_LABEL[k] || titleCase(k);

/** Map any backend severity string onto the three Pearl pill tones. */
export function severityTone(sev) {
    const s = String(sev || '').toLowerCase();
    if (['critical', 'high', 'severe', 'danger', 'emergency', 'fire'].includes(s)) return 'danger';
    if (['warning', 'warn', 'medium', 'moderate'].includes(s)) return 'warn';
    return 'info';
}

/** Camera display name with id fallback. */
export const cameraLabel = (name, id) => (name ? String(name) : (id != null ? `Camera ${id}` : DASH));

/** Fired on window after an alert is acknowledged so the sidebar badge refreshes at once. */
export const ALERTS_CHANGED_EVENT = 'omnitrack-alerts-changed';
export const notifyAlertsChanged = () => {
    if (typeof window !== 'undefined') window.dispatchEvent(new Event(ALERTS_CHANGED_EVENT));
};
