/**
 * OmniTrack AI — Adversarial Robustness
 * GET /api/security/robustness + POST /api/security/robustness/run
 */

import React, { useState } from 'react';
import { motion } from 'framer-motion';
import { ShieldAlert, PlayCircle, Zap, Target, Activity } from 'lucide-react';
import { systemAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';

export default function SecurityPage() {
    const { data: status, refresh } = useLivePoll(() => systemAPI.robustness(), { intervalMs: 15000 });
    const [params, setParams] = useState({
        sample_size: 4,
        eps_fgsm: 0.03,
        eps_pgd: 0.03,
        pgd_steps: 5,
        image_dir: '',
    });
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState(null);
    const [result, setResult] = useState(null);

    const run = async () => {
        setBusy(true); setError(null); setResult(null);
        try {
            const payload = { ...params };
            if (!payload.image_dir) delete payload.image_dir;
            const res = await systemAPI.runRobustness(payload);
            setResult(res.data);
            refresh();
        } catch (e) {
            setError(e?.response?.data?.detail || e.message);
        } finally { setBusy(false); }
    };

    const last = result || status?.last_eval || null;

    return (
        <div className="page-scroll">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Model Health Check</h1>
                    <p className="page-subtitle">
                        Test how stable people detection remains under noisy and distorted input.
                    </p>
                </div>
            </div>

            <div className="stats-grid">
                <motion.div className="stat-card" whileHover={{ y: -2 }}>
                    <div className={`stat-icon stat-icon-${status?.art_available ? 'emerald' : 'rose'}`}>
                        <ShieldAlert size={18} />
                    </div>
                    <div className="stat-label">Health Engine Ready</div>
                    <div className="stat-value">{status?.art_available ? 'yes' : 'no'}</div>
                </motion.div>
                <Stat
                    icon={Target}
                    label="Baseline Detections"
                    value={Number(last?.avg_person_count_clean ?? 0).toFixed(2)}
                    accent="indigo"
                />
                <Stat
                    icon={Zap}
                    label="Light Distortion Retention"
                    value={last?.detection_retention_fgsm != null
                        ? `${Math.round(last.detection_retention_fgsm * 100)}%`
                        : '—'}
                    accent="amber"
                />
                <Stat
                    icon={Zap}
                    label="Heavy Distortion Retention"
                    value={last?.detection_retention_pgd != null
                        ? `${Math.round(last.detection_retention_pgd * 100)}%`
                        : '—'}
                    accent="rose"
                />
            </div>

            <div className="two-col">
                <div className="card">
                    <div className="card-header">
                        <h3 className="card-title">Run Stability Check</h3>
                        <div className="card-subtitle">Simulates degraded visuals and measures detection consistency</div>
                    </div>

                    {!status?.art_available && (
                        <div className="alert-banner danger" style={{ marginBottom: 10 }}>
                            Optional security toolkit is not installed. Run <code>pip install adversarial-robustness-toolbox[torch]</code>.
                        </div>
                    )}

                    <div style={{ display: 'grid', gap: 10 }}>
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                            <NumInput label="Sample size"
                                value={params.sample_size} min={1} max={32}
                                onChange={(v) => setParams({ ...params, sample_size: v })}
                            />
                            <NumInput label="Light distortion level"
                                value={params.eps_fgsm} step={0.005} min={0} max={0.5}
                                onChange={(v) => setParams({ ...params, eps_fgsm: v })}
                            />
                            <NumInput label="Heavy distortion level"
                                value={params.eps_pgd} step={0.005} min={0} max={0.5}
                                onChange={(v) => setParams({ ...params, eps_pgd: v })}
                            />
                            <NumInput label="Heavy check steps"
                                value={params.pgd_steps} min={1} max={50}
                                onChange={(v) => setParams({ ...params, pgd_steps: v })}
                            />
                        </div>
                        <div>
                            <label className="form-label">Image directory (optional)</label>
                            <input
                                className="form-input"
                                placeholder="Leave empty to use your default footage directory"
                                value={params.image_dir}
                                onChange={(e) => setParams({ ...params, image_dir: e.target.value })}
                            />
                        </div>
                        <button className="btn btn-primary" onClick={run} disabled={busy || !status?.art_available}>
                            <PlayCircle size={14} /> {busy ? 'Running…' : 'Run Health Check'}
                        </button>
                        {error && <div className="alert-banner danger">{error}</div>}
                    </div>
                </div>

                <div className="card">
                    <div className="card-header">
                        <h3 className="card-title">
                            <Activity size={14} style={{ verticalAlign: -2, marginRight: 6 }} />
                            Latest Result
                        </h3>
                        <div className="card-subtitle">
                            {last?.model_path ? `Model: ${last.model_path}` : 'No check has run yet'}
                        </div>
                    </div>
                    {last ? (
                        <div style={{ display: 'grid', gap: 8 }}>
                            <Row label="Samples" value={last.samples ?? last.sample_size} />
                            <Row label="Baseline avg detections" value={Number(last.avg_person_count_clean ?? 0).toFixed(3)} />
                            <Row label="Light distortion avg" value={Number(last.avg_person_count_fgsm ?? 0).toFixed(3)} />
                            <Row label="Heavy distortion avg" value={Number(last.avg_person_count_pgd ?? 0).toFixed(3)} />
                            <Row label="Light distortion retention" value={last.detection_retention_fgsm != null ? `${Math.round(last.detection_retention_fgsm * 100)}%` : '—'} />
                            <Row label="Heavy distortion retention" value={last.detection_retention_pgd != null ? `${Math.round(last.detection_retention_pgd * 100)}%` : '—'} />
                            <Row label="Timestamp" value={last.timestamp ? new Date(last.timestamp).toLocaleString() : '—'} />
                        </div>
                    ) : (
                        <div className="page-empty-hint page-empty-hint--left">
                            Run a health check to see model stability metrics.
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}

function NumInput({ label, value, onChange, step = 1, min, max }) {
    return (
        <div>
            <label className="form-label">{label}</label>
            <input
                className="form-input" type="number"
                value={value}
                step={step} min={min} max={max}
                onChange={(e) => onChange(Number(e.target.value))}
            />
        </div>
    );
}

function Row({ label, value }) {
    return (
        <div style={{
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            padding: '8px 10px', background: 'var(--bg-glass)',
            borderRadius: 10, border: '1px solid var(--border)',
        }}>
            <span style={{ color: 'var(--text-secondary)', fontSize: 13 }}>{label}</span>
            <span style={{ fontWeight: 600, fontSize: 13 }}>{String(value)}</span>
        </div>
    );
}

function Stat({ icon: Icon, label, value, accent = 'indigo' }) {
    return (
        <motion.div className="stat-card" whileHover={{ y: -2 }}>
            <div className={`stat-icon stat-icon-${accent}`}><Icon size={18} /></div>
            <div className="stat-label">{label}</div>
            <div className="stat-value">{value}</div>
        </motion.div>
    );
}
