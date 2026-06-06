/**
 * OmniTrack AI — Video Synopsis (live)
 */

import React, { useEffect, useState } from 'react';
import { Video, Clock, Play, PlayCircle } from 'lucide-react';
import { synopsisAPI, footageAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import StatCard from '../components/ui/StatCard';
import ContentCard from '../components/ui/ContentCard';
import HighlightReelTool from '../components/HighlightReelTool';

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
            } catch { setTimeout(tick, 3000); }
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
                    <h1 className="page-title">Highlights Reel</h1>
                    <p className="page-subtitle">Generate compressed video synopsis files and subject highlight reels</p>
                </div>
            </div>

            <div className="stats-grid">
                <StatCard icon={Video} label="Generated" value={items.length} accent="teal" />
                <StatCard
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
                <StatCard icon={PlayCircle} label="Active Jobs" value={jobStatus?.status === 'running' || jobStatus?.status === 'queued' ? 1 : 0} accent="coral" />
                <StatCard icon={Video} label="Footage in Library" value={(footage || []).length} accent="emerald" />
            </div>

            <div className="two-col">
                <ContentCard
                    title="Generate Synopsis"
                    subtitle="Runs the real VideoSynopsis engine on disk"
                    accent="teal"
                >
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
                </ContentCard>

                <ContentCard
                    title="Library"
                    subtitle={items.length ? `${items.length} synopsis file(s)` : 'No synopses yet'}
                    accent="sky"
                >
                    <div className="ui-feed-list" style={{ maxHeight: 360, overflow: 'auto' }}>
                        {items.map((s) => {
                            const filename = s.output_path?.split(/[\\/]/).pop();
                            return (
                                <div
                                    key={`${s.id}-${s.output_path}`}
                                    className="ui-lane-row"
                                    style={{ gridTemplateColumns: '1fr 100px 100px 80px' }}
                                >
                                    <div style={{ overflow: 'hidden' }}>
                                        <div className="ui-lane-row-title" style={{ whiteSpace: 'nowrap', textOverflow: 'ellipsis', overflow: 'hidden' }}>
                                            {filename || s.output_path}
                                        </div>
                                        <div className="ui-lane-row-sub">cam {s.camera_id}</div>
                                    </div>
                                    <span style={{ fontSize: 12 }}>
                                        {Math.round(s.original_duration || 0)}s → {Math.round(s.synopsis_duration || 0)}s
                                    </span>
                                    <span className="pill pill-info">{Number(s.compression_ratio || 0).toFixed(1)}x</span>
                                    <a
                                        href={synopsisAPI.serveUrl(filename || '')}
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
                </ContentCard>
            </div>

            <HighlightReelTool />
        </div>
    );
}
