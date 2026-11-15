/**
 * Track highlight reels: pick a detection log, extract one tracked subject into
 * a short clip (POST /api/footage/trim-by-track) and inspect the log's movement
 * analytics (heatmap, distances, timelines). Every figure comes from the API.
 */

import React, { useEffect, useMemo, useState } from 'react';
import { AlertCircle, Download, Play, RefreshCw } from 'lucide-react';
import { footageAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import { Metric } from './ui';

export default function HighlightReelTool() {
    const [selectedLog, setSelectedLog] = useState('');
    const [trackIdInput, setTrackIdInput] = useState('');
    const [paddingFrames, setPaddingFrames] = useState('5');
    const [trimming, setTrimming] = useState(false);
    const [trimResult, setTrimResult] = useState(null);
    const [analytics, setAnalytics] = useState(null);
    const [analyticsLoading, setAnalyticsLoading] = useState(false);
    const [error, setError] = useState(null);

    const { data: logs, refresh: refreshLogs } = useLivePoll(
        () => footageAPI.logsList(), { intervalMs: 10000 }
    );

    const handleLogChange = (filename) => {
        setSelectedLog(filename);
        setTrackIdInput('');
        setTrimResult(null);
        setAnalytics(null);
        setError(null);
    };

    useEffect(() => {
        let cancelled = false;
        const loadAnalytics = async () => {
            if (!selectedLog) return;
            setAnalyticsLoading(true);
            setError(null);
            try {
                const res = await footageAPI.logAnalytics(selectedLog, 4, 30);
                if (!cancelled) setAnalytics(res.data);
            } catch (e) {
                if (!cancelled) setError(e?.response?.data?.detail || e.message);
            } finally {
                if (!cancelled) setAnalyticsLoading(false);
            }
        };
        loadAnalytics();
        return () => { cancelled = true; };
    }, [selectedLog]);

    const handleTrim = async () => {
        if (!selectedLog || !trackIdInput) return;
        setTrimming(true);
        setError(null);
        setTrimResult(null);
        try {
            const res = await footageAPI.trimByTrack(
                selectedLog,
                parseInt(trackIdInput),
                parseInt(paddingFrames)
            );
            setTrimResult(res.data);
            await refreshLogs();
        } catch (e) {
            setError(e?.response?.data?.detail || e.message);
        } finally {
            setTrimming(false);
        }
    };

    const logList = Array.isArray(logs) ? logs : [];
    const selectedLogInfo = useMemo(() => logList.find((log) => log.filename === selectedLog), [logList, selectedLog]);
    const ready = selectedLog && analytics && !analyticsLoading;
    const heat = analytics?.heatmap?.cells || [];
    const heatMax = heat.reduce((a, c) => Math.max(a, Number(c.count) || 0), 0);
    const bins = analytics?.heatmap?.bins || 4;
    const distances = (analytics?.distances || []).slice(0, 6);
    const timelines = (analytics?.timelines || []).slice(0, 8);
    const segments = trimResult?.segments || [];

    return (
        <>
            <div className="section-head ops-reel-head">
                <h2 className="section-title">Track highlight reels</h2>
            </div>

            {error && (
                <div className="alert-banner danger" role="alert">
                    <AlertCircle size={16} strokeWidth={1.75} aria-hidden />
                    <span className="ops-banner-text">{typeof error === 'string' ? error : JSON.stringify(error)}</span>
                </div>
            )}

            <div className="ops-split ops-split--rev">
                <section className="panel ops-panel" aria-labelledby="reel-build">
                    <div className="panel-head">
                        <div>
                            <h2 className="panel-title" id="reel-build">Build a reel</h2>
                            <p className="panel-sub">Extract one tracked subject from a detection log.</p>
                        </div>
                    </div>
                    <div className="panel-body ops-col-body">
                        <div className="form-group">
                            <label className="form-label" htmlFor="reel-log">Detection log</label>
                            <select id="reel-log" className="form-select" value={selectedLog} onChange={(e) => handleLogChange(e.target.value)}>
                                <option value="">{logList.length ? 'Select a recorded video…' : 'No detection logs yet'}</option>
                                {logList.map((log) => (
                                    <option key={log.filename} value={log.filename}>
                                        {log.filename} ({log.total_frames} frames)
                                    </option>
                                ))}
                            </select>
                            {selectedLogInfo && (
                                <p className="ops-note ops-muted ops-reel-meta">
                                    Camera {selectedLogInfo.camera_id ?? '—'} · {selectedLogInfo.model || '—'} · {selectedLogInfo.total_frames ?? '—'} frames
                                </p>
                            )}
                        </div>
                        <div className="ops-pair">
                            <div className="form-group">
                                <label className="form-label" htmlFor="reel-track">Track ID</label>
                                <input
                                    id="reel-track"
                                    type="number"
                                    className="form-input"
                                    placeholder="e.g. 12"
                                    value={trackIdInput}
                                    onChange={(e) => setTrackIdInput(e.target.value)}
                                    disabled={!selectedLog}
                                    min={0}
                                />
                            </div>
                            <div className="form-group">
                                <label className="form-label" htmlFor="reel-pad">Padding · <span className="ops-tnum">{paddingFrames}</span> frames</label>
                                <input
                                    id="reel-pad"
                                    type="range"
                                    className="ops-range"
                                    min={0}
                                    max={30}
                                    value={paddingFrames}
                                    onChange={(e) => setPaddingFrames(e.target.value)}
                                />
                            </div>
                        </div>
                        <button
                            type="button"
                            className="btn btn-primary"
                            onClick={handleTrim}
                            disabled={!selectedLog || !trackIdInput || trimming}
                        >
                            {trimming
                                ? <><RefreshCw size={16} strokeWidth={1.75} className="spin" /> Building…</>
                                : <><Play size={16} strokeWidth={1.75} /> Create highlight reel</>}
                        </button>
                    </div>
                </section>

                <section className="panel ops-panel" aria-labelledby="reel-preview">
                    <div className="panel-head">
                        <div>
                            <h2 className="panel-title" id="reel-preview">Reel preview</h2>
                            <p className="panel-sub">
                                {trimResult
                                    ? `${segments.length} segment${segments.length === 1 ? '' : 's'} · ${trimResult.frames_written ?? '—'} frames written`
                                    : 'The generated subject reel plays here.'}
                            </p>
                        </div>
                        {trimResult ? (
                            <a
                                href={footageAPI.serveUrl(trimResult.trimmed_video)}
                                download={trimResult.trimmed_video}
                                className="btn btn-secondary btn-sm"
                            >
                                <Download size={16} strokeWidth={1.75} /> Download
                            </a>
                        ) : null}
                    </div>
                    <div className="panel-body ops-col-body">
                        <div className="ops-reel-body">
                            <div className="ops-syn-stage vf vf--on-media">
                                {trimResult ? (
                                    <video
                                        key={trimResult.trimmed_video}
                                        className="ops-syn-video"
                                        controls
                                        autoPlay
                                        src={footageAPI.serveUrl(trimResult.trimmed_video)}
                                    />
                                ) : (
                                    <div className="ops-syn-idle">
                                        {trimming
                                            ? <><RefreshCw size={16} strokeWidth={1.75} className="spin" aria-hidden /><span>Extracting frames and building the reel…</span></>
                                            : <span>Select a log and a track, then create a reel.</span>}
                                    </div>
                                )}
                            </div>
                            <div className="ops-reel-segs">
                                <div className="form-label">Segments</div>
                                {segments.length ? (
                                    <ol className="ops-reel-seglist">
                                        {segments.map((seg, i) => (
                                            <li key={i}>
                                                <span className="mono">{seg[0]}–{seg[1]}</span>
                                                <span className="ops-muted">{seg[1] - seg[0] + 1} frames</span>
                                            </li>
                                        ))}
                                    </ol>
                                ) : <p className="ops-note ops-muted">—</p>}
                            </div>
                        </div>
                    </div>
                </section>
            </div>

            <section className="panel ops-panel" aria-labelledby="reel-analytics">
                <div className="panel-head">
                    <div>
                        <h2 className="panel-title" id="reel-analytics">Track analytics</h2>
                        <p className="panel-sub">Floor heatmap, movement and timelines from the selected log.</p>
                    </div>
                </div>
                <div className="panel-body ops-col-body">
                    <div className="kpi-strip ops-reel-kpis">
                        <Metric label="Frames" value={ready ? analytics.frame_count ?? null : null} />
                        <Metric label="Tracks" value={ready ? analytics.track_count ?? null : null} />
                        <Metric label="Timelines" value={ready ? (analytics.timelines?.length ?? null) : null} />
                        <Metric label="Fall candidates" value={ready ? (analytics.fall_candidates?.length ?? null) : null} />
                    </div>

                    {!ready ? (
                        <div className="empty-state ops-reel-empty">
                            <div className="empty-state-title">{selectedLog ? 'Loading analytics…' : 'No log selected'}</div>
                            {!selectedLog ? <p>Choose a detection log above to see where people moved.</p> : null}
                        </div>
                    ) : (
                        <div className="ops-reel-grid">
                            <div>
                                <div className="form-label">Heatmap</div>
                                {heat.length ? (
                                    <div className="ops-heat" style={{ '--bins': bins }}>
                                        {heat.map((cell) => {
                                            const v = heatMax ? (Number(cell.count) || 0) / heatMax : 0;
                                            return (
                                                <div
                                                    key={`${cell.row}-${cell.col}`}
                                                    className={`ops-heat-cell ${v > 0.55 ? 'is-hot' : ''}`}
                                                    style={{ '--heat': v }}
                                                    title={`${cell.count} samples`}
                                                >
                                                    {cell.count}
                                                </div>
                                            );
                                        })}
                                    </div>
                                ) : <p className="ops-note ops-muted">No position samples.</p>}
                            </div>
                            <div>
                                <div className="form-label">Top movement</div>
                                {distances.length ? (
                                    <ol className="ops-reel-rows">
                                        {distances.map((row) => (
                                            <li key={row.track_id}>
                                                <span className="mono">{row.track_id}</span>
                                                <span>{Math.round(row.distance_px)} px</span>
                                                <span className="ops-muted">{row.frames_observed} frames</span>
                                            </li>
                                        ))}
                                    </ol>
                                ) : <p className="ops-note ops-muted">No movement samples yet.</p>}
                            </div>
                            <div>
                                <div className="form-label">Track timelines</div>
                                {timelines.length ? (
                                    <ol className="ops-reel-rows">
                                        {timelines.map((row) => (
                                            <li key={row.track_id}>
                                                <span className="mono">{row.track_id}</span>
                                                <span>From frame {row.first_frame}</span>
                                                <span className="ops-muted">{row.duration_frames} frames</span>
                                            </li>
                                        ))}
                                    </ol>
                                ) : <p className="ops-note ops-muted">No track lasted long enough.</p>}
                            </div>
                        </div>
                    )}
                </div>
            </section>
        </>
    );
}
