/**
 * OmniTrack AI — Security
 *
 * 1. The access controls this deployment enforces (GET /api/security/posture).
 * 2. Adversarial robustness of the real person detector: ART FGSM + PGD
 *    (L-inf, untargeted) on a COCO val2017 person subset, scored as person
 *    AP@0.5 — GET /api/security/robustness, POST /api/security/robustness/run (admin).
 * The audit-chain status lives on the Activity log only. Nothing here is
 * simulated: with no report on disk the page says so.
 *
 * `embedded` renders the content without the page header and page wrapper,
 * for the System hub.
 */

import React, { useState } from 'react';
import { Play } from 'lucide-react';
import { apiErrorMessage, systemAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import useUserRole from '../hooks/useUserRole';
import PageHeader from '../components/PageHeader';

const pct = (v) => (v == null || Number.isNaN(v) ? '—' : `${(v * 100).toFixed(1)}%`);
const ap = (v) => (v == null || Number.isNaN(v) ? '—' : Number(v).toFixed(3));

// Read from GET /api/security/posture: the values this backend actually runs with.
function postureItems(p) {
    if (!p) return [];
    const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
    return [
        { label: 'Access token', value: `${p.access_token_minutes} min`, sub: `Signed JWT (${p.jwt_algorithm})` },
        { label: 'Refresh token', value: plural(p.refresh_token_days, 'day', 'days'), sub: 'Rotated; reuse revokes the session' },
        { label: 'Lockout', value: plural(p.login_max_failures, 'failure', 'failures'), sub: `${p.login_lockout_minutes} min lock, plus a per-IP rate limit` },
        { label: 'At rest', value: p.encryption_at_rest || 'Not configured', sub: 'Face crops, snapshots, audit metadata' },
    ];
}

export default function SecurityPage({ embedded = false }) {
    const { canAdmin } = useUserRole();
    const { data: status, refresh } = useLivePoll(() => systemAPI.robustness(), { intervalMs: 10000 });
    const { data: posture, error: postureError } = useLivePoll(() => systemAPI.securityPosture(), { intervalMs: 60000 });
    const [params, setParams] = useState({ images: 10, eps255: 8, pgd_steps: 10 });
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState(null);

    const job = status?.job || {};
    const running = job.status === 'running' || job.status === 'queued';
    const report = status?.report?.status === 'completed' ? status.report : null;

    const run = async () => {
        setBusy(true); setError(null);
        try {
            await systemAPI.runRobustness({ images: params.images, eps: params.eps255 / 255, pgd_steps: params.pgd_steps });
            refresh();
        } catch (e) {
            setError(apiErrorMessage(e, 'Could not start the evaluation'));
        } finally { setBusy(false); }
    };

    return (
        <div className={embedded ? 'adm-embedded' : 'adm-page'}>
            {!embedded && (
                <PageHeader
                    title="Security"
                    subtitle="How access is controlled and how well the person detector holds up under attack."
                />
            )}

            <section className="panel panel--flush adm-flush" aria-label="Access controls">
                <div className="panel-body">
                    <div>
                        <h2 className="adm-status-title">Deny by default. Every change needs the admin role.</h2>
                        <p className="adm-status-msg">The backend checks the role on every request; the navigation only mirrors it.</p>
                    </div>
                    {postureError && !posture ? (
                        <p className="adm-status-msg">Could not read the live security settings from the API.</p>
                    ) : null}
                    <div className="kpi-strip">
                        {postureItems(posture).map((m) => (
                            <div className="metric" key={m.label}>
                                <span className="metric-label">{m.label}</span>
                                <span className="adm-text-value">{m.value}</span>
                                <span className="metric-sub">{m.sub}</span>
                            </div>
                        ))}
                    </div>
                </div>
            </section>

            <div className="adm-grid-wide">
                <section className="panel">
                    <div className="panel-body">
                        <div className="panel-head">
                            <div>
                                <h2 className="panel-title">Adversarial robustness</h2>
                                <p className="panel-sub">{status?.method || 'ART FGSM and PGD against the deployed person detector.'}</p>
                            </div>
                            {report && <span className="count-badge">{new Date(report.created_at).toLocaleDateString()}</span>}
                        </div>
                        {!report ? (
                            <div className="empty-state">
                                <span className="empty-state-title">No robustness report yet</span>
                                {status?.report?.reason ? `${status.report.reason}. ` : ''}
                                {canAdmin
                                    ? 'Run an evaluation to measure person AP@0.5 on clean and attacked COCO images.'
                                    : 'An administrator can run one from this page.'}
                            </div>
                        ) : (
                            <>
                                <div className="kpi-strip">
                                    <div className="metric">
                                        <span className="metric-label">Clean AP@0.5</span>
                                        <span className="adm-text-value">{ap(report.clean?.ap50)}</span>
                                        <span className="metric-sub">Recall {pct(report.clean?.recall)}</span>
                                    </div>
                                    <div className="metric">
                                        <span className="metric-label">FGSM {report.fgsm?.linf_255}/255</span>
                                        <span className="adm-text-value">{ap(report.fgsm?.ap50)}</span>
                                        <span className="metric-sub">Drop {report.fgsm?.drop?.rel_pct ?? '—'}%</span>
                                    </div>
                                    <div className="metric">
                                        <span className="metric-label">PGD-{report.params?.pgd_steps} {report.pgd?.linf_255}/255</span>
                                        <span className="adm-text-value">{ap(report.pgd?.ap50)}</span>
                                        <span className="metric-sub">Drop {report.pgd?.drop?.rel_pct ?? '—'}%</span>
                                    </div>
                                </div>
                                <dl className="adm-kv adm-kv--spaced">
                                    <dt>Model</dt><dd>{`${report.model} at ${report.imgsz} px on ${report.device}`}</dd>
                                    <dt>Dataset</dt>
                                    <dd className="num">{`${report.dataset?.images} COCO val2017 images, ${report.dataset?.person_boxes} person boxes`}</dd>
                                    <dt>Metric</dt><dd>{report.metric}</dd>
                                    {Object.entries(report.defences || {}).map(([name, d]) => (
                                        <React.Fragment key={name}>
                                            <dt>Defence: {name.replace('_', ' ')}</dt>
                                            <dd className="num">{`Clean ${ap(d.clean?.ap50)} · FGSM ${ap(d.fgsm?.ap50)} · PGD ${ap(d.pgd?.ap50)}`}</dd>
                                        </React.Fragment>
                                    ))}
                                    <dt>Run</dt>
                                    <dd className="num">{`ART ${report.art_version}, ${report.timing?.total_seconds} s, ${new Date(report.created_at).toLocaleString()}`}</dd>
                                </dl>
                                {(report.notes || []).length > 0 && (
                                    <ul className="adm-detail-notes">
                                        {report.notes.map((n) => <li key={n}>{n}</li>)}
                                    </ul>
                                )}
                            </>
                        )}
                    </div>
                </section>

                <section className="panel">
                    <div className="panel-body">
                        <div className="panel-head">
                            <div>
                                <h2 className="panel-title">Run an evaluation</h2>
                                <p className="panel-sub">Admins only. A CPU run takes several minutes.</p>
                            </div>
                        </div>
                        {status && !status.art_available && (
                            <div className="alert-banner danger" role="alert">
                                The adversarial-robustness-toolbox package is not installed in the backend environment.
                            </div>
                        )}
                        <div className="adm-form-stack">
                            <NumInput id="rb-images" label="COCO images" value={params.images} min={2} max={200}
                                onChange={(v) => setParams({ ...params, images: v })} />
                            <NumInput id="rb-eps" label="Epsilon (out of 255)" value={params.eps255} min={1} max={25}
                                onChange={(v) => setParams({ ...params, eps255: v })} />
                            <NumInput id="rb-steps" label="PGD steps" value={params.pgd_steps} min={1} max={50}
                                onChange={(v) => setParams({ ...params, pgd_steps: v })} />
                            <div>
                                <button type="button" className="btn btn-primary btn-sm" onClick={run}
                                    disabled={!canAdmin || busy || running || !status?.art_available}>
                                    <Play size={16} strokeWidth={1.75} />
                                    {running ? `Evaluation ${job.status}…` : busy ? 'Starting…' : 'Run evaluation'}
                                </button>
                            </div>
                            {job.status === 'failed' && <div className="alert-banner danger" role="alert">Last run failed: {job.error}</div>}
                            {error && <div className="alert-banner danger" role="alert">{error}</div>}
                        </div>
                    </div>
                </section>
            </div>
        </div>
    );
}

function NumInput({ id, label, value, onChange, min, max }) {
    return (
        <div className="form-group">
            <label className="form-label" htmlFor={id}>{label}</label>
            <input id={id} className="form-input num" type="number" value={value} min={min} max={max}
                onChange={(e) => onChange(Math.max(min, Math.min(max, Number(e.target.value) || min)))} />
            <span className="form-hint">{min} to {max}</span>
        </div>
    );
}
