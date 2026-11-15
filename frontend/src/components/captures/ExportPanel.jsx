/**
 * Data export: list the datasets the server offers (GET /api/export) and
 * download one as CSV or JSON for an optional date range.
 */

import React, { useEffect, useMemo, useState } from 'react';
import { Download, Lock } from 'lucide-react';
import { apiErrorMessage, exportAPI } from '../../services/api';

const FORMATS = [{ value: 'csv', label: 'CSV' }, { value: 'json', label: 'JSON' }];

/** Accept an array, {datasets:[...]} or {name: {...}} and normalise to [{name, rows, allowed, label}]. */
export function normaliseDatasets(raw) {
    let list = raw;
    if (raw && !Array.isArray(raw)) {
        list = Array.isArray(raw.datasets) ? raw.datasets
            : Array.isArray(raw.items) ? raw.items
                : Object.entries(raw).filter(([, v]) => v && typeof v === 'object').map(([name, v]) => ({ name, ...v }));
    }
    if (!Array.isArray(list)) return [];
    return list.map((d) => {
        if (typeof d === 'string') return { name: d, rows: null, allowed: true, label: d };
        const name = d.name ?? d.dataset ?? d.id;
        const rows = d.rows ?? d.row_count ?? d.count ?? null;
        const allowed = d.allowed ?? d.can_export ?? d.permitted ?? true;
        return { name, rows: Number.isFinite(Number(rows)) && rows !== null ? Number(rows) : null, allowed: Boolean(allowed), label: d.label || d.title || name };
    }).filter((d) => d.name);
}

const toIso = (local) => {
    if (!local) return undefined;
    const d = new Date(local);
    return Number.isNaN(d.getTime()) ? undefined : d.toISOString();
};

export default function ExportPanel() {
    const [datasets, setDatasets] = useState(null);
    const [loadError, setLoadError] = useState('');
    const [dataset, setDataset] = useState('');
    const [format, setFormat] = useState('csv');
    const [since, setSince] = useState('');
    const [until, setUntil] = useState('');
    const [busy, setBusy] = useState(false);
    const [result, setResult] = useState(null); // {ok, text}

    useEffect(() => {
        let alive = true;
        exportAPI.datasets()
            .then((res) => {
                if (!alive) return;
                const list = normaliseDatasets(res.data);
                setDatasets(list);
                const first = list.find((d) => d.allowed);
                if (first) setDataset(first.name);
            })
            .catch((err) => {
                if (!alive) return;
                setDatasets([]);
                setLoadError(err?.response?.status === 404
                    ? 'The export service is not available on this server yet.'
                    : apiErrorMessage(err, 'Could not load export datasets'));
            });
        return () => { alive = false; };
    }, []);

    const selected = useMemo(() => (datasets || []).find((d) => d.name === dataset), [datasets, dataset]);
    const rangeInvalid = since && until && new Date(since) > new Date(until);

    const download = async () => {
        if (!dataset || busy || rangeInvalid) return;
        setBusy(true);
        setResult(null);
        try {
            const name = await exportAPI.download(dataset, { format, since: toIso(since), until: toIso(until) });
            setResult({ ok: true, text: `Downloaded ${name}` });
        } catch (err) {
            setResult({ ok: false, text: apiErrorMessage(err, 'Export failed') });
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="ops-export">
            {datasets === null && <p className="ops-muted ops-note">Loading datasets…</p>}
            {loadError && <div className="alert-banner warning">{loadError}</div>}
            {datasets && datasets.length > 0 && (
                <div className="ops-filter-bar ops-export-bar">
                    <div className="form-group">
                        <label className="form-label" htmlFor="export-dataset">Dataset</label>
                        <select id="export-dataset" className="form-select" value={dataset} onChange={(e) => setDataset(e.target.value)}>
                            {datasets.map((d) => (
                                <option key={d.name} value={d.name} disabled={!d.allowed}>
                                    {d.label}{d.rows != null ? ` (${d.rows.toLocaleString()} rows)` : ''}{d.allowed ? '' : ' (admin only)'}
                                </option>
                            ))}
                        </select>
                    </div>
                    <div className="form-group">
                        <label className="form-label" htmlFor="export-format">Format</label>
                        <select id="export-format" className="form-select" value={format} onChange={(e) => setFormat(e.target.value)}>
                            {FORMATS.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
                        </select>
                    </div>
                    <div className="form-group">
                        <label className="form-label" htmlFor="export-since">From (local time)</label>
                        <input id="export-since" type="datetime-local" className="form-input" value={since} onChange={(e) => setSince(e.target.value)} />
                    </div>
                    <div className="form-group">
                        <label className="form-label" htmlFor="export-until">To</label>
                        <input id="export-until" type="datetime-local" className="form-input" value={until} onChange={(e) => setUntil(e.target.value)} />
                    </div>
                    <div className="ops-filter-reset">
                        <button type="button" className="btn btn-primary" onClick={download}
                            disabled={!dataset || busy || rangeInvalid || (selected && !selected.allowed)}>
                            {selected && !selected.allowed ? <Lock size={16} strokeWidth={1.75} /> : <Download size={16} strokeWidth={1.75} />}
                            {busy ? 'Preparing…' : 'Download'}
                        </button>
                    </div>
                </div>
            )}
            {datasets && datasets.length === 0 && !loadError && <p className="ops-muted ops-note">No datasets are available for export.</p>}
            {rangeInvalid && <div className="alert-banner warning">"From" must be before "To".</div>}
            {selected && selected.rows === 0 && <p className="ops-muted ops-note">This dataset has no rows yet, so the file will be empty.</p>}
            {result && <div className={`alert-banner ${result.ok ? 'success' : 'danger'}`}>{result.text}</div>}
        </div>
    );
}
