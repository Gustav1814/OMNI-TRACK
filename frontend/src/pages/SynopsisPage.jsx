/**
 * OmniTrack AI — Video Synopsis (live)
 * • Lists completed synopses
 * • Generates a new synopsis for a camera + source (uploaded clip)
 * • Polls the running job until completion
 */

import React, { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { Video, Clock, Play, PlayCircle } from 'lucide-react';
import { synopsisAPI, footageAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';

export default function SynopsisPage() {
    const { data: list, refresh: refreshList } = useLivePoll(() => synopsisAPI.list(), { intervalMs: 10000 });
    const { data: footage } = useLivePoll(() => footageAPI.list(), { intervalMs: 15000 });

    const [cameraId, setCameraId] = useState(1);
    const [source, setSource] = useState('');
    const [compression, setCompression] = useState(10);
    const [activeJob, setActiveJob] = useState(null);
    const [jobStatus, setJobStatus] = useState(null);
    const [error, setError] = useState(null);
    const [busy, setBusy] = useState(false);

    // Poll job until it finishes
    useEffect(() => {
        if (!activeJob?.job_id || jobStatus?.status === 'completed' || jobStatus?.status === 'failed') {
            return undefined;
        }
        let cancelled = false;
        const tick = async () => {
            try {
                const res = await synopsisAPI.job(activeJob.job_id);
                if (cancelled) return;
                setJobStatus(res.data);
                if (res.data?.status === 'completed') { refreshList(); return; }
                if (res.data?.status === 'failed') return;
                setTimeout(tick, 3000);
            } catch { /* keep polling */ setTimeout(tick, 3000); }
        };
        tick();
        return () => { cancelled = true; };
    }, [activeJob, jobStatus?.status, refreshList]);

    const generate = async () => {
        setBusy(true); setError(null); setJobStatus(null);
        try {
            const res = await synopsisAPI.generate(Number(cameraId) || 1, {
                source: source || undefined,
                compression: Number(compression) || 10,
            });
            setActiveJob(res.data);
            setJobStatus({ status: 'queued' });
        } catch (e) {
            setError(e?.response?.data?.detail || e.message);
        } finally { setBusy(false); }
    };

    const items = Array.isArray(list) ? list : [];

    return (
        <div className="page-scroll">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Video Synopsis</h1>
                    <p className="page-subtitle">Compress hours of footage into minutes</p>
                </div>
            </div>

            <div className="stats-grid">
                <Stat icon={Video} label="Generated" value={items.length} accent="indigo" />
                <Stat
                    icon={Clock}
                    label="Avg Compression"
                    value={
                        items.length
                            ? (items.reduce((a, s) => a + (s.compression_ratio || 0), 0) / items.length).toFixed(1)
                            : 0
                    }
                    suffix="x"
                    accent="cyan"
                />
                <Stat icon={PlayCircle} label="Active Jobs" value={jobStatus?.status === 'running' || jobStatus?.status === 'queued' ? 1 : 0} accent="amber" />
                <Stat icon={Video} label="Footage in Library" value={(footage || []).length} accent="emerald" />
            </div>

            <div className="two-col">
                <div className="card">
                    <div className="card-header">
                        <h3 className="card-title">Generate Synopsis</h3>
                        <div className="card-subtitle">Runs the real VideoSynopsis engine on disk</div>
                    </div>
                    <div style={{ display: 'grid', gap: 10 }}>
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                            <div>
                                <label className="form-label">Camera ID</label>
                                <input
                                    className="form-input" type="number" min={1}
                                    value={cameraId} onChange={(e) => setCameraId(e.target.value)}
                                />
                            </div>
                            <div>
                                <label className="form-label">Compression</label>
                                <input
                                    className="form-input" type="number" min={2} max={40}
                                    value={compression} onChange={(e) => setCompression(e.target.value)}
                                />
                            </div>
                        </div>
                        <div>
                            <label className="form-label">Source clip</label>
                            <select
                                className="form-select"
                                value={source}
                                onChange={(e) => setSource(e.target.value)}
                            >
                                <option value="">Latest recording for this camera</option>
                                {(footage || []).map((f) => (
                                    <option key={f.filename} value={f.filename}>
                                        {f.filename}
                                    </option>
                                ))}
                            </select>
                        </div>
                        <button className="btn btn-primary" onClick={generate} disabled={busy}>
                            <PlayCircle size={14} /> {busy ? 'Queueing…' : 'Generate'}
                        </button>
                        {error && <div className="alert-banner danger">{error}</div>}
                        {jobStatus && (
                            <div className="alert-banner info" style={{ marginTop: 8 }}>
                                <strong>Job #{activeJob?.job_id}:</strong> {jobStatus.status}
                                {jobStatus.compression_ratio != null && (
                                    <> · compressed {Number(jobStatus.compression_ratio).toFixed(1)}x</>
                                )}
                                {jobStatus.error && <div style={{ marginTop: 4 }}>Error: {jobStatus.error}</div>}
                            </div>
                        )}
                    </div>
                </div>

                <div className="card">
                    <div className="card-header">
                        <h3 className="card-title">Library</h3>
                        <div className="card-subtitle">
                            {items.length ? `${items.length} synopsis file(s)` : 'No synopses yet'}
                        </div>
                    </div>
                    <div style={{ display: 'grid', gap: 6, maxHeight: 360, overflow: 'auto' }}>
                        {items.map((s) => {
                            const filename = s.output_path?.split(/[\\/]/).pop();
                            return (
                                <div key={`${s.id}-${s.output_path}`} style={{
                                    display: 'grid',
                                    gridTemplateColumns: '1fr 100px 100px 80px',
                                    gap: 10, alignItems: 'center',
                                    padding: '10px 12px',
                                    background: 'var(--bg-glass)',
                                    border: '1px solid var(--border)', borderRadius: 10,
                                }}>
                                    <div style={{ overflow: 'hidden' }}>
                                        <div style={{ fontWeight: 600, fontSize: 13, whiteSpace: 'nowrap', textOverflow: 'ellipsis', overflow: 'hidden' }}>
                                            {filename || s.output_path}
                                        </div>
                                        <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>cam {s.camera_id}</div>
                                    </div>
                                    <span style={{ fontSize: 12 }}>
                                        {Math.round(s.original_duration || 0)}s → {Math.round(s.synopsis_duration || 0)}s
                                    </span>
                                    <span className="pill pill-info">{Number(s.compression_ratio || 0).toFixed(1)}x</span>
                                    <a
                                        href={footageAPI.serveUrl(filename || '')}
                                        target="_blank" rel="noreferrer"
                                        className="btn btn-secondary btn-xs"
                                        style={{ textDecoration: 'none', justifyContent: 'center' }}
                                    >
                                        <Play size={12} /> Open
                                    </a>
                                </div>
                            );
                        })}
                    </div>
                </div>
            </div>
        </div>
    );
}

function Stat({ icon: Icon, label, value, suffix = '', accent = 'indigo' }) {
    return (
        <motion.div className="stat-card" whileHover={{ y: -2 }}>
            <div className={`stat-icon stat-icon-${accent}`}><Icon size={18} /></div>
            <div className="stat-label">{label}</div>
            <div className="stat-value">{value}{suffix}</div>
        </motion.div>
    );
}
