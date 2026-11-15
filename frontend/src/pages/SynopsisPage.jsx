/**
 * OmniTrack AI — Video Synopsis
 *
 * Streams a recorded clip through background modelling + object-tube
 * extraction and re-packs the tubes into a shorter WebM (GET /api/synopsis/sources,
 * POST /api/synopsis/generate [admin], GET /api/synopsis/jobs/{id}, GET /api/synopsis/).
 * Every number shown comes from the engine's metrics sidecar; unknown = "—".
 */

import React, { useEffect, useMemo, useState } from 'react';
import { Play, PlayCircle, X } from 'lucide-react';
import { apiErrorMessage, synopsisAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import useUserRole from '../hooks/useUserRole';
import { Metric } from '../components/ui';
import HighlightReelTool from '../components/HighlightReelTool';

const STAGES = { queued: 'Queued', background: 'Learning background', tubes: 'Extracting object tubes',
    schedule: 'Scheduling tubes', render: 'Rendering synopsis', done: 'Done' };
const secs = (v) => (v == null ? '—' : v >= 90 ? `${(v / 60).toFixed(1)} min` : `${Math.round(v)} s`);
const mb = (b) => (b == null ? '' : `${(b / 1048576).toFixed(1)} MB`);
const ratio = (v) => (v == null ? '—' : `${Number(v).toFixed(1)}×`);
const fmtCreated = (ts) => (ts ? new Date(ts * 1000).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
}) : '—');

export default function SynopsisPage() {
    const { canOperate } = useUserRole();
    const { data: list, refresh: refreshList } = useLivePoll(() => synopsisAPI.list(), { intervalMs: 15000 });
    const { data: sources } = useLivePoll(() => synopsisAPI.sources(), { intervalMs: 60000 });

    const [cameraId, setCameraId] = useState('1');
    const [source, setSource] = useState('');
    const [compression, setCompression] = useState('10');
    const [job, setJob] = useState(null);
    const [error, setError] = useState(null);
    const [busy, setBusy] = useState(false);
    const [playing, setPlaying] = useState(null);
    const [cued, setCued] = useState(false);   // newest synopsis loaded into the player once, paused

    const jobActive = job && (job.status === 'queued' || job.status === 'running');

    useEffect(() => {
        if (!jobActive) return undefined;
        let cancelled = false;
        let timer;
        const tick = async () => {
            try {
                const { data } = await synopsisAPI.job(job.job_id);
                if (cancelled) return;
                setJob(data);
                if (data.status === 'queued' || data.status === 'running') timer = setTimeout(tick, 2000);
                else refreshList();
            } catch (e) {
                if (!cancelled) {
                    if (e?.response?.status === 404) setJob((j) => ({ ...j, status: 'failed', error: 'Job no longer exists (server restarted?)' }));
                    else timer = setTimeout(tick, 4000);
                }
            }
        };
        timer = setTimeout(tick, 1000);
        return () => { cancelled = true; clearTimeout(timer); };
    }, [job?.job_id, jobActive, refreshList]); // eslint-disable-line react-hooks/exhaustive-deps

    const generate = async () => {
        setBusy(true); setError(null);
        try {
            const { data } = await synopsisAPI.generate(Number(cameraId) || 1, {
                source: source || undefined,
                compression: Math.min(100, Math.max(1, Number(compression) || 10)),
            });
            setJob(data);
        } catch (e) {
            setError(apiErrorMessage(e, 'Could not start the synopsis'));
        } finally { setBusy(false); }
    };

    const loaded = Array.isArray(list);
    const items = loaded ? list : [];
    useEffect(() => {
        if (!cued && !playing && items.length) { setPlaying({ ...items[0], cuedOnly: true }); setCued(true); }
    }, [cued, playing, items]);
    const srcList = Array.isArray(sources) ? sources : [];
    const ratios = items.map((s) => s.metrics?.compression_ratio ?? s.compression_ratio).filter((v) => v != null);
    const avgRatio = ratios.length ? ratios.reduce((a, b) => a + b, 0) / ratios.length : null;
    const savedSeconds = useMemo(() => items.reduce((a, s) => {
        const m = s.metrics || s;
        return m.original_duration != null && m.synopsis_duration != null ? a + (m.original_duration - m.synopsis_duration) : a;
    }, 0), [items]);
    const jobLabel = !job ? 'Idle' : jobActive ? 'Generating' : job.status === 'completed' ? 'Last job done'
        : job.status === 'failed' ? 'Last job failed' : 'Idle';

    return (
        <div className="page-scroll">
            <header className="page-header">
                <div>
                    <h1 className="page-title">Video synopsis</h1>
                    <div className="osd-strip ops-head-osd" aria-label="Synopsis status">
                        <span>
                            <span className={`live-dot ${jobActive ? 'is-warn' : job?.status === 'failed' ? '' : 'is-ok'}`} aria-hidden />
                            <strong>{jobLabel}</strong>
                        </span>
                    </div>
                </div>
            </header>

            <section className="ops-top" aria-label="Synopsis summary">
                {avgRatio != null ? (
                    <div className="panel panel--feature vf ops-feature">
                        <div className="panel-body ops-feature-body">
                            <span className="osd">Average compression</span>
                            <Metric size="hero" value={avgRatio} decimals={1} unit="×" />
                            <div className="osd-strip ops-feature-osd">
                                <span>Across <strong>{ratios.length}</strong> synops{ratios.length === 1 ? 'is' : 'es'}</span>
                            </div>
                        </div>
                    </div>
                ) : (
                    <div className="panel ops-feature">
                        <div className="panel-body ops-feature-body">
                            <div className="empty-state ops-fill">
                                <span className="empty-state-title">{loaded ? 'No synopsis yet' : 'Reading the library'}</span>
                                <span>{loaded ? 'Generate one from a recorded clip to see how much viewing time it saves.' : 'Loading synopses.'}</span>
                            </div>
                        </div>
                    </div>
                )}
                <div className="ops-top-kpis">
                    <div className="kpi-strip">
                        <Metric label="Synopses" value={loaded ? items.length : null} sub="In the library" />
                        <Metric label="Viewing time saved" value={items.length ? savedSeconds / 60 : null} decimals={1} unit="min" sub="Original minus synopsis" />
                        <Metric label="Source clips" value={Array.isArray(sources) ? srcList.length : null} sub="Recordings available" />
                    </div>
                </div>
            </section>

            <div className="ops-split">
                <section className="panel ops-panel" aria-labelledby="syn-player">
                    <div className="panel-head">
                        <div className="ops-min0">
                            <h2 className="panel-title ops-ellipsis" id="syn-player">{playing ? 'Now showing' : 'Player'}</h2>
                            <p className="panel-sub ops-ellipsis">
                                {playing ? (playing.source || playing.filename) : 'Every person and object that appeared, re-timed into one short clip.'}
                            </p>
                        </div>
                        {playing ? (
                            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setPlaying(null)}>
                                <X size={16} strokeWidth={1.75} /> Close
                            </button>
                        ) : null}
                    </div>
                    <div className="panel-body ops-col-body">
                        <div className="ops-syn-stage vf vf--on-media">
                            {playing ? (
                                <video
                                    key={`${playing.filename}-${playing.cuedOnly ? "cue" : "play"}`}
                                    className="ops-syn-video"
                                    src={synopsisAPI.serveUrl(playing.filename)}
                                    controls muted preload="metadata" autoPlay={!playing.cuedOnly}
                                />
                            ) : (
                                <div className="ops-syn-idle">
                                    <PlayCircle size={16} strokeWidth={1.75} aria-hidden />
                                    <span>{items.length ? 'Choose a synopsis from the library' : 'No synopsis to play yet'}</span>
                                </div>
                            )}
                        </div>
                        <Metrics m={playing ? (playing.metrics || playing) : {}} />
                    </div>
                </section>

                <section className="panel ops-panel" aria-labelledby="syn-generate">
                    <div className="panel-head">
                        <div>
                            <h2 className="panel-title" id="syn-generate">Generate</h2>
                            <p className="panel-sub">Runs the streaming synopsis engine on the server.</p>
                        </div>
                    </div>
                    <div className="panel-body ops-col-body">
                        <div className="form-group">
                            <label className="form-label" htmlFor="syn-source">Source clip</label>
                            <select id="syn-source" className="form-select" value={source} onChange={(e) => setSource(e.target.value)}>
                                <option value="">Latest recording of camera {cameraId || 1}</option>
                                {srcList.map((f) => (
                                    <option key={f.source} value={f.source}>{f.source}{f.size_bytes ? ` (${mb(f.size_bytes)})` : ''}</option>
                                ))}
                            </select>
                        </div>
                        <div className="ops-pair">
                            <div className="form-group">
                                <label className="form-label" htmlFor="syn-camera">Camera ID</label>
                                <input id="syn-camera" className="form-input" type="number" min={0}
                                    value={cameraId} onChange={(e) => setCameraId(e.target.value)} />
                            </div>
                            <div className="form-group">
                                <label className="form-label" htmlFor="syn-compression">Target compression (×)</label>
                                <input id="syn-compression" className="form-input" type="number" min={1} max={100}
                                    value={compression} onChange={(e) => setCompression(e.target.value)} />
                            </div>
                        </div>
                        <button type="button" className="btn btn-primary" onClick={generate} disabled={!canOperate || busy || jobActive}>
                            <PlayCircle size={16} strokeWidth={1.75} /> {busy ? 'Queueing…' : jobActive ? 'Generating…' : 'Generate synopsis'}
                        </button>
                        {!canOperate && <p className="ops-note ops-muted">Only administrators can generate synopses.</p>}
                        {error && <div className="alert-banner danger">{error}</div>}
                        {job && <JobStatus job={job} onPlay={() => setPlaying(items.find((s) => s.filename === job.filename) || job)} />}
                    </div>
                </section>
            </div>

            <section className="panel ops-panel" aria-labelledby="syn-library">
                <div className="panel-head">
                    <div>
                        <h2 className="panel-title" id="syn-library">Library</h2>
                        <p className="panel-sub">Generated synopses, newest first.</p>
                    </div>
                    <span className="count-badge">{items.length}</span>
                </div>
                <div className="panel-body ops-col-body">
                    {items.length === 0 ? (
                        <div className="empty-state ops-syn-empty">
                            <div className="empty-state-title">{loaded ? 'No synopses yet' : 'Loading library…'}</div>
                            {loaded ? <p>Generate one from a recorded clip and it will appear here.</p> : null}
                        </div>
                    ) : (
                        <div className="table-scroll">
                            <table className="data-table ops-syn-table">
                                <thead>
                                    <tr>
                                        <th>Source</th>
                                        <th>Camera</th>
                                        <th>Created</th>
                                        <th className="num">Duration</th>
                                        <th className="num">Tubes</th>
                                        <th className="num">Compression</th>
                                        <th aria-label="Actions" />
                                    </tr>
                                </thead>
                                <tbody>
                                    {items.map((s) => {
                                        const m = s.metrics || s;
                                        const active = playing?.filename === s.filename;
                                        return (
                                            <tr key={s.filename} className={active ? 'is-selected' : ''}>
                                                <td className="ops-syn-src" title={s.filename}>{s.source || s.filename}</td>
                                                <td className="mono">{s.camera_id ?? '—'}</td>
                                                <td className="ops-tnum">{fmtCreated(s.created_ts)}</td>
                                                <td className="num">{secs(m.original_duration)} → {secs(m.synopsis_duration)}</td>
                                                <td className="num">{m.tubes_kept ?? '—'}</td>
                                                <td className="num">{ratio(m.compression_ratio)}</td>
                                                <td className="num">
                                                    <button type="button" className="btn btn-secondary btn-xs" onClick={() => setPlaying(s)} aria-pressed={active}>
                                                        <Play size={14} strokeWidth={1.75} /> {active ? 'Playing' : 'Play'}
                                                    </button>
                                                </td>
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        </div>
                    )}
                </div>
            </section>

            <HighlightReelTool />
        </div>
    );
}

function JobStatus({ job, onPlay }) {
    const pct = Math.round((job.progress || 0) * 100);
    const failed = job.status === 'failed';
    const done = job.status === 'completed';
    const idle = job.status === 'no_activity';
    if (failed) {
        return (
            <div className="alert-banner danger">
                <span className="ops-banner-text"><strong>Job #{job.job_id} failed.</strong> {job.error || ''}</span>
            </div>
        );
    }
    if (idle) {
        return (
            <div className="alert-banner info">
                <span className="ops-banner-text"><strong>Job #{job.job_id}:</strong> no moving objects found in this clip.</span>
            </div>
        );
    }
    if (done) {
        return (
            <div className="alert-banner success">
                <span className="ops-banner-text">
                    <strong>Job #{job.job_id} done.</strong> {secs(job.original_duration)} → {secs(job.synopsis_duration)} ({ratio(job.compression_ratio)})
                </span>
                <span className="ops-banner-actions">
                    <button type="button" className="btn btn-secondary btn-xs" onClick={onPlay}><Play size={14} strokeWidth={1.75} /> Play</button>
                </span>
            </div>
        );
    }
    return (
        <div className="meter meter--accent ops-syn-job" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}
            aria-label={`Job ${job.job_id} progress`}>
            <div className="meter-head">
                <span>Job #{job.job_id} · {STAGES[job.stage] || job.stage || job.status}</span>
                <strong>{pct}%</strong>
            </div>
            <div className="meter-track"><div className="meter-fill" style={{ '--value': `${pct}%` }} /></div>
            {job.error ? <p className="ops-note ops-muted">{job.error}</p> : null}
        </div>
    );
}

function Metrics({ m }) {
    const rows = [
        ['Original', m.original_duration != null ? `${secs(m.original_duration)} · ${m.original_frames ?? '—'} frames` : null],
        ['Synopsis', m.synopsis_duration != null ? `${secs(m.synopsis_duration)} · ${m.synopsis_frames ?? '—'} frames` : null],
        ['Compression', m.compression_ratio != null ? `${ratio(m.compression_ratio)} (target ${m.compression_target != null ? ratio(m.compression_target) : '—'})` : null],
        ['Object tubes', m.tubes_kept != null ? `${m.tubes_kept} kept of ${m.tubes_detected ?? '—'} detected` : null],
        ['Tubes preserved', m.tubes_preserved_pct != null ? `${m.tubes_preserved_pct}% · ${m.tubes_dropped ?? 0} dropped` : null],
        ['Processing', m.processing_seconds != null ? `${m.processing_seconds} s${m.codec ? ` · ${m.codec}` : ''}` : null],
    ];
    return (
        <dl className="ops-syn-metrics">
            {rows.map(([k, v]) => (
                <div key={k}>
                    <dt>{k}</dt>
                    <dd>{v ?? '—'}</dd>
                </div>
            ))}
        </dl>
    );
}
