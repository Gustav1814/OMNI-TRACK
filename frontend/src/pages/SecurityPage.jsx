/**
 * OmniTrack AI — Model Health Check (Enhanced)
 * Adversarial robustness testing and model stability metrics
 */

import React, { useState } from 'react';
import { motion } from 'framer-motion';
import { ShieldAlert, PlayCircle, Zap, Target, Activity, ShieldCheck, Cpu, AlertTriangle } from 'lucide-react';
import { systemAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import GlassCard, { StatCard, PageHeader, InfoRow } from '../components/GlassCard';

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

    const getRetentionColor = (value) => {
        if (value === null || value === undefined) return 'indigo';
        if (value >= 0.8) return 'emerald';
        if (value >= 0.6) return 'amber';
        return 'rose';
    };

    return (
        <div className="page-scroll">
            <PageHeader
                title="Model Health Check"
                subtitle="Test model stability under adversarial conditions and visual distortions"
            />

            {/* Stats Grid */}
            <div className="stats-grid-3d">
                <StatCard
                    icon={status?.art_available ? ShieldCheck : ShieldAlert}
                    label="Health Engine"
                    value={status?.art_available ? 'Ready' : 'Not Available'}
                    accent={status?.art_available ? 'emerald' : 'rose'}
                    tag={status?.art_available ? 'Active' : 'Install ART'}
                    progress={status?.art_available ? 100 : 0}
                />
                <StatCard
                    icon={Target}
                    label="Baseline Detections"
                    value={Number(last?.avg_person_count_clean ?? 0).toFixed(2)}
                    accent="indigo"
                    tag="Avg"
                />
                <StatCard
                    icon={Zap}
                    label="Light Distortion"
                    value={last?.detection_retention_fgsm != null
                        ? `${Math.round(last.detection_retention_fgsm * 100)}%`
                        : '—'}
                    accent={getRetentionColor(last?.detection_retention_fgsm)}
                    tag="Retention"
                    progress={last?.detection_retention_fgsm ? last.detection_retention_fgsm * 100 : 0}
                />
                <StatCard
                    icon={Cpu}
                    label="Heavy Distortion"
                    value={last?.detection_retention_pgd != null
                        ? `${Math.round(last.detection_retention_pgd * 100)}%`
                        : '—'}
                    accent={getRetentionColor(last?.detection_retention_pgd)}
                    tag="Retention"
                    progress={last?.detection_retention_pgd ? last.detection_retention_pgd * 100 : 0}
                />
            </div>

            {/* Main Content */}
            <div className="cards-grid-3d cards-grid-3d-2col">
                {/* Configuration Panel */}
                <GlassCard accent="indigo">
                    <div className="card-header-3d">
                        <div>
                            <h3 className="card-title-3d">Run Stability Check</h3>
                            <p className="card-subtitle-3d">
                                Simulates degraded visuals and measures detection consistency
                            </p>
                        </div>
                    </div>

                    {!status?.art_available && (
                        <div className="alert-banner-3d alert-banner-3d-warning" style={{ marginBottom: 16 }}>
                            <AlertTriangle size={18} />
                            <span>
                                Optional security toolkit not installed. Run <code>pip install adversarial-robustness-toolbox[torch]</code>
                            </span>
                        </div>
                    )}

                    <div className="form-grid-3d">
                        <div className="form-row-3d">
                            <NumInput 
                                label="Sample size"
                                value={params.sample_size} 
                                min={1} 
                                max={32}
                                onChange={(v) => setParams({ ...params, sample_size: v })}
                            />
                            <NumInput 
                                label="Light distortion (ε)"
                                value={params.eps_fgsm} 
                                step={0.005} 
                                min={0} 
                                max={0.5}
                                onChange={(v) => setParams({ ...params, eps_fgsm: v })}
                            />
                        </div>
                        <div className="form-row-3d">
                            <NumInput 
                                label="Heavy distortion (ε)"
                                value={params.eps_pgd} 
                                step={0.005} 
                                min={0} 
                                max={0.5}
                                onChange={(v) => setParams({ ...params, eps_pgd: v })}
                            />
                            <NumInput 
                                label="Heavy steps"
                                value={params.pgd_steps} 
                                min={1} 
                                max={50}
                                onChange={(v) => setParams({ ...params, pgd_steps: v })}
                            />
                        </div>
                        <div className="form-field-3d">
                            <label className="form-label-3d">Image directory (optional)</label>
                            <input
                                className="form-input-3d"
                                placeholder="Leave empty to use default footage directory"
                                value={params.image_dir}
                                onChange={(e) => setParams({ ...params, image_dir: e.target.value })}
                            />
                        </div>
                        <button 
                            className="btn-3d btn-3d-primary btn-3d-lg" 
                            onClick={run} 
                            disabled={busy || !status?.art_available}
                            style={{ marginTop: 8 }}
                        >
                            <PlayCircle size={18} />
                            {busy ? 'Running Analysis...' : 'Run Health Check'}
                        </button>
                        
                        {error && (
                            <div className="alert-banner-3d alert-banner-3d-danger" style={{ marginTop: 12 }}>
                                {error}
                            </div>
                        )}
                    </div>
                </GlassCard>

                {/* Results Panel */}
                <GlassCard accent="emerald">
                    <div className="card-header-3d">
                        <div>
                            <h3 className="card-title-3d">
                                <Activity size={16} style={{ display: 'inline', verticalAlign: -2, marginRight: 8 }} />
                                Latest Results
                            </h3>
                            <p className="card-subtitle-3d">
                                {last?.model_path 
                                    ? `Model: ${last.model_path.split('/').pop()}` 
                                    : 'No health check has been run yet'
                                }
                            </p>
                        </div>
                        {last?.timestamp && (
                            <span className="pill-3d pill-3d-info">
                                {new Date(last.timestamp).toLocaleDateString()}
                            </span>
                        )}
                    </div>

                    {last ? (
                        <div className="results-grid-3d">
                            <InfoRow
                                icon={Target}
                                label="Samples Tested"
                                value={last.samples ?? last.sample_size}
                                accent="indigo"
                            />
                            <InfoRow
                                icon={Target}
                                label="Baseline Avg Detections"
                                value={Number(last.avg_person_count_clean ?? 0).toFixed(3)}
                                accent="indigo"
                            />
                            <InfoRow
                                icon={Zap}
                                label="Light Distortion Avg"
                                value={Number(last.avg_person_count_fgsm ?? 0).toFixed(3)}
                                accent="amber"
                            />
                            <InfoRow
                                icon={Cpu}
                                label="Heavy Distortion Avg"
                                value={Number(last.avg_person_count_pgd ?? 0).toFixed(3)}
                                accent="rose"
                            />
                            <InfoRow
                                icon={Activity}
                                label="Light Retention Rate"
                                value={last.detection_retention_fgsm != null 
                                    ? `${Math.round(last.detection_retention_fgsm * 100)}%` 
                                    : '—'}
                                accent={getRetentionColor(last?.detection_retention_fgsm)}
                            />
                            <InfoRow
                                icon={Activity}
                                label="Heavy Retention Rate"
                                value={last.detection_retention_pgd != null 
                                    ? `${Math.round(last.detection_retention_pgd * 100)}%` 
                                    : '—'}
                                accent={getRetentionColor(last?.detection_retention_pgd)}
                            />
                        </div>
                    ) : (
                        <div className="empty-state-3d">
                            <div className="empty-state-icon">
                                <Activity size={28} />
                            </div>
                            <div className="empty-state-title">No Results Yet</div>
                            <div className="empty-state-desc">
                                Run a health check to see how your detection model performs under adversarial conditions.
                            </div>
                        </div>
                    )}
                </GlassCard>
            </div>
        </div>
    );
}

function NumInput({ label, value, onChange, step = 1, min, max }) {
    return (
        <div className="form-field-3d">
            <label className="form-label-3d">{label}</label>
            <input
                className="form-input-3d" 
                type="number"
                value={value}
                step={step} 
                min={min} 
                max={max}
                onChange={(e) => onChange(Number(e.target.value))}
            />
        </div>
    );
}

// Styles injection
const securityStyles = `
.form-grid-3d {
    display: flex;
    flex-direction: column;
    gap: 16px;
}

.form-row-3d {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
}

.form-field-3d {
    display: flex;
    flex-direction: column;
    gap: 6px;
}

.form-label-3d {
    font-size: 0.85rem;
    font-weight: 500;
    color: var(--text-secondary);
}

.form-input-3d {
    padding: 10px 14px;
    border-radius: 10px;
    border: 1px solid var(--border);
    background: var(--bg-glass);
    color: var(--text-primary);
    font-size: 0.9rem;
    transition: all 0.2s ease;
}

.form-input-3d:focus {
    outline: none;
    border-color: var(--accent-primary);
    box-shadow: 0 0 0 3px var(--accent-primary-glow);
}

html.light .form-input-3d {
    background: white;
}

.results-grid-3d {
    display: flex;
    flex-direction: column;
    gap: 8px;
}
`;

const styleSheet = document.createElement('style');
styleSheet.textContent = securityStyles;
document.head.appendChild(styleSheet);
