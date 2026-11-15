/**
 * OmniTrack AI — Evaluation
 * Proposal success criteria vs what the eval/ scripts actually measured.
 * Reads /api/evaluation/summary (eval/criteria.json + eval/results/*.json).
 * A criterion that has not been measured shows "Not measured" — never a number.
 */

import React, { useState } from 'react';
import { RefreshCw, ChevronDown, ChevronRight } from 'lucide-react';
import { evaluationAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import PageHeader from '../components/PageHeader';

const OP_TEXT = { '>': '>', '>=': '≥', '<': '<', '<=': '≤' };

export function formatValue(value, unit) {
    if (value == null || Number.isNaN(Number(value))) return '—';
    const v = Number(value);
    if (unit === 'ratio') return `${(v * 100).toFixed(1)}%`;
    if (unit === 'ms') return `${v.toFixed(1)} ms`;
    if (unit === 'fps') return `${v.toFixed(1)} FPS`;
    if (unit === 'x') return `${v.toFixed(1)}×`;
    if (unit === 'people') return `${v.toFixed(2)} people`;
    return String(Math.round(v * 1000) / 1000);
}

function formatTarget(item) {
    if (item.target == null || !item.op) return 'Reported only';
    return `${OP_TEXT[item.op] || item.op} ${formatValue(item.target, item.unit)}`;
}

function StatusPill({ item }) {
    if (item.status === 'no_data') return <span className="pill pill-neutral">Not measured</span>;
    if (item.passed === true) return <span className="pill pill-success">Pass</span>;
    if (item.passed === false) return <span className="pill pill-danger">Fail</span>;
    return <span className="pill pill-info">Reported</span>;
}

function hardwareText(hw) {
    if (!hw) return '—';
    const dev = hw.gpu || (hw.device && hw.device !== 'cpu' ? hw.device : hw.cpu);
    return [dev, hw.device === 'cpu' && hw.threads_used ? `${hw.threads_used} threads` : null].filter(Boolean).join(' · ') || '—';
}

function Detail({ item }) {
    return (
        <>
            <dl className="adm-kv">
                {item.criterion ? <><dt>Criterion</dt><dd>{item.criterion}</dd></> : null}
                {item.secondary ? (
                    <>
                        <dt>{item.secondary.metric}</dt>
                        <dd className="num">
                            {formatValue(item.secondary.value, 'ratio')}
                            {item.secondary.target != null ? ` (target ${OP_TEXT[item.secondary.op] || item.secondary.op} ${formatValue(item.secondary.target, 'ratio')})` : ''}
                        </dd>
                    </>
                ) : null}
                {item.dataset ? (
                    <>
                        <dt>Dataset</dt>
                        <dd className="num">
                            {item.dataset.name}
                            {item.dataset.split ? ` · ${item.dataset.split}` : ''}
                            {item.dataset.n_items != null ? ` · ${item.dataset.n_items} items` : ''}
                        </dd>
                    </>
                ) : <><dt>Planned dataset</dt><dd>{item.planned_dataset || '—'}</dd></>}
                {item.model?.name ? <><dt>Model</dt><dd>{item.model.name}</dd></> : null}
                <dt>Script</dt><dd><code className="adm-code">{item.script}</code></dd>
            </dl>
            {(item.notes || []).length > 0 && (
                <ul className="adm-detail-notes">
                    {item.notes.map((n) => <li key={n}>{n}</li>)}
                </ul>
            )}
        </>
    );
}

export default function EvaluationPage({ embedded = false }) {
    const { data, error, loading, refresh } = useLivePoll(() => evaluationAPI.summary(), { intervalMs: 60000 });
    const [open, setOpen] = useState(null);
    const items = Array.isArray(data?.criteria) ? data.criteria : [];
    const counts = data?.counts || { total: items.length, measured: 0, passed: 0, failed: 0 };
    const total = counts.total || 0;
    const other = Math.max(0, (counts.measured || 0) - (counts.passed || 0) - (counts.failed || 0));
    const notMeasured = Math.max(0, total - (counts.measured || 0));
    const measuredItems = items.filter((i) => i.status !== 'no_data');
    const allCpu = measuredItems.length > 0
        && measuredItems.every((i) => !i.hardware?.gpu && (i.hardware?.device || 'cpu') === 'cpu');
    const share = (n) => `${total ? (n / total) * 100 : 0}%`;
    const devices = [...new Set(measuredItems.map((i) => i.hardware?.gpu || i.hardware?.cpu).filter(Boolean))];
    const refreshButton = (
        <button type="button" className="btn btn-secondary btn-sm" onClick={refresh}>
            <RefreshCw size={16} strokeWidth={1.75} /> Refresh
        </button>
    );

    return (
        <div className={embedded ? 'adm-embedded' : 'adm-page'}>
            {embedded ? <div className="adm-embed-bar">{refreshButton}</div> : (
                <PageHeader
                    title="Evaluation"
                    subtitle="Proposal targets against measured results. Every number here comes from eval/results."
                >
                    {refreshButton}
                </PageHeader>
            )}

            {items.length > 0 && (
                <section className="panel panel--flush adm-flush" aria-label="Summary">
                    <div className="panel-body">
                        <div className="adm-eval-head">
                            <div className="metric metric--hero">
                                <span className="metric-label">Criteria measured</span>
                                <span className="metric-value tabular">{counts.measured}<small>/{total}</small></span>
                            </div>
                            {allCpu && (
                                <p className="adm-status-msg">
                                    All current results were measured on CPU. Run <code className="adm-code">eval/run_all_pc.ps1</code> on
                                    the GPU PC for the final figures.
                                </p>
                            )}
                        </div>
                        <div>
                            <div className="adm-progress" role="img"
                                aria-label={`${counts.passed} pass, ${counts.failed} fail, ${other} reported, ${notMeasured} not measured`}>
                                <span className="adm-progress-pass" style={{ width: share(counts.passed || 0) }} />
                                <span className="adm-progress-fail" style={{ width: share(counts.failed || 0) }} />
                                <span className="adm-progress-other" style={{ width: share(other) }} />
                            </div>
                            <div className="kpi-strip">
                                <div className="metric">
                                    <span className="metric-label">Pass</span>
                                    <span className="metric-value tabular">{counts.passed || 0}</span>
                                </div>
                                <div className="metric">
                                    <span className="metric-label">Fail</span>
                                    <span className="metric-value tabular">{counts.failed || 0}</span>
                                </div>
                                {other > 0 && (
                                    <div className="metric">
                                        <span className="metric-label">Reported only</span>
                                        <span className="metric-value tabular">{other}</span>
                                    </div>
                                )}
                                <div className="metric">
                                    <span className="metric-label">Not measured</span>
                                    <span className="metric-value tabular">{notMeasured}</span>
                                </div>
                                <div className="metric">
                                    <span className="metric-label">Hardware</span>
                                    <span className="adm-text-value">{measuredItems.length === 0 ? '—' : allCpu ? 'CPU only' : 'GPU'}</span>
                                    {devices.length > 0 && <span className="metric-sub">{devices.join(', ')}</span>}
                                </div>
                            </div>
                        </div>
                    </div>
                </section>
            )}

            <section className="panel">
                <div className="panel-body">
                    <div className="panel-head">
                        <div>
                            <h2 className="panel-title">Success criteria</h2>
                            <p className="panel-sub">Open a row for its dataset, model, script and notes.</p>
                        </div>
                    </div>
                    {items.length === 0 ? (
                        <div className="empty-state">
                            <span className="empty-state-title">
                                {loading ? 'Loading results…' : error ? 'Evaluation results unavailable' : 'No evaluation results yet'}
                            </span>
                            {loading ? null : error
                                ? 'The backend did not return evaluation data. Check that the API is running, then refresh.'
                                : <>Run <code className="adm-code">eval/run_all_pc.ps1</code> from the repo root on the GPU PC. Results are written to eval/results and appear here.</>}
                        </div>
                    ) : (
                        <div className="table-scroll">
                            <table className="data-table adm-table adm-table--wide">
                                <thead>
                                    <tr>
                                        <th>Criterion</th>
                                        <th className="num">Target</th>
                                        <th className="num">Measured</th>
                                        <th>Result</th>
                                        <th>Dataset</th>
                                        <th>Hardware</th>
                                        <th>Date</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {items.map((item) => {
                                        const isOpen = open === item.id;
                                        return (
                                            <React.Fragment key={item.id}>
                                                <tr className="is-clickable" onClick={() => setOpen(isOpen ? null : item.id)}>
                                                    <td>
                                                        <button type="button" className="adm-expand" aria-expanded={isOpen}
                                                            onClick={(e) => { e.stopPropagation(); setOpen(isOpen ? null : item.id); }}>
                                                            {isOpen ? <ChevronDown size={14} strokeWidth={1.75} /> : <ChevronRight size={14} strokeWidth={1.75} />}
                                                            <span>
                                                                <span className="adm-strong">{item.title}</span>
                                                                <span className="adm-sub">{item.metric}</span>
                                                            </span>
                                                        </button>
                                                    </td>
                                                    <td className="num">{formatTarget(item)}</td>
                                                    <td className="num" data-testid={`value-${item.id}`}>{item.status === 'no_data' ? '—' : formatValue(item.value, item.unit)}</td>
                                                    <td><StatusPill item={item} /></td>
                                                    <td>{item.dataset?.name || item.planned_dataset || '—'}</td>
                                                    <td>{hardwareText(item.hardware)}</td>
                                                    <td className="mono">{item.created_at ? new Date(item.created_at).toLocaleDateString() : '—'}</td>
                                                </tr>
                                                {isOpen ? (
                                                    <tr className="adm-detail"><td colSpan={7}><Detail item={item} /></td></tr>
                                                ) : null}
                                            </React.Fragment>
                                        );
                                    })}
                                </tbody>
                            </table>
                        </div>
                    )}
                </div>
            </section>
        </div>
    );
}
