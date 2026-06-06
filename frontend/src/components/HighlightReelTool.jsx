import React, { useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import { AlertCircle, Clock, Download, Film, Play, RefreshCw } from 'lucide-react';
import { footageAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import ContentCard from './ui/ContentCard';

export default function HighlightReelTool() {
    const [selectedLog, setSelectedLog] = useState('');
    const [trackIdInput, setTrackIdInput] = useState('');
    const [paddingFrames, setPaddingFrames] = useState(5);
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

    React.useEffect(() => {
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

    const selectedLogInfo = useMemo(() => {
        return logs?.find((log) => log.filename === selectedLog);
    }, [logs, selectedLog]);

    return (
        <div style={{ display: 'grid', gap: 16 }}>
            {error && (
                <div className="alert-banner danger" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <AlertCircle size={16} />
                    {error}
                </div>
            )}

            <div className="two-col">
                <ContentCard
                    title={<> <Film size={16} style={{ verticalAlign: -3, marginRight: 6 }} /> Build Highlight Reel </>}
                    subtitle="Choose a detection log and extract one tracked subject into a short reel"
                    accent="teal"
                >
                    <div style={{ display: 'grid', gap: 16 }}>
                        <div>
                            <label className="form-label">Detection Log</label>
                            <select
                                className="form-select"
                                value={selectedLog}
                                onChange={(e) => handleLogChange(e.target.value)}
                            >
                                <option value="">Select a recorded video...</option>
                                {(logs || []).map((log) => (
                                    <option key={log.filename} value={log.filename}>
                                        {log.filename} ({log.total_frames} frames)
                                    </option>
                                ))}
                            </select>
                            {selectedLogInfo && (
                                <div style={{ marginTop: 8, fontSize: 12, color: 'var(--text-secondary)' }}>
                                    <Film size={12} style={{ verticalAlign: -1, marginRight: 4 }} />
                                    Camera {selectedLogInfo.camera_id} - {selectedLogInfo.model} - {selectedLogInfo.total_frames} frames
                                </div>
                            )}
                        </div>

                        <div>
                            <label className="form-label">Track ID</label>
                            <input
                                type="number"
                                className="form-input"
                                placeholder="Enter track ID"
                                value={trackIdInput}
                                onChange={(e) => setTrackIdInput(e.target.value)}
                                disabled={!selectedLog}
                                min={0}
                            />
                        </div>

                        <div>
                            <label className="form-label">Padding Frames</label>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                                <input
                                    type="range"
                                    min={0}
                                    max={30}
                                    value={paddingFrames}
                                    onChange={(e) => setPaddingFrames(e.target.value)}
                                    style={{ flex: 1 }}
                                />
                                <span style={{ fontSize: 14, fontWeight: 500, minWidth: 30 }}>{paddingFrames}</span>
                            </div>
                        </div>

                        <button
                            className="btn btn-primary"
                            onClick={handleTrim}
                            disabled={!selectedLog || !trackIdInput || trimming}
                        >
                            {trimming ? (
                                <><RefreshCw size={14} className="spin" /> Building...</>
                            ) : (
                                <><Play size={14} /> Create Highlight Reel</>
                            )}
                        </button>
                    </div>
                </ContentCard>

                <ContentCard
                    title={<> <Play size={16} style={{ verticalAlign: -3, marginRight: 6 }} /> Reel Preview </>}
                    subtitle="Preview and download the generated subject reel"
                    accent="sky"
                >
                    {!trimResult && !trimming && (
                        <div className="page-empty-hint">
                            Select a log and track, then create a highlight reel.
                        </div>
                    )}

                    {trimming && (
                        <div style={{ textAlign: 'center', padding: 40 }}>
                            <motion.div
                                animate={{ rotate: 360 }}
                                transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
                            >
                                <RefreshCw size={40} style={{ opacity: 0.5 }} />
                            </motion.div>
                            <p style={{ marginTop: 16, color: 'var(--text-secondary)' }}>
                                Extracting frames and building reel...
                            </p>
                        </div>
                    )}

                    {trimResult && (
                        <div style={{ display: 'grid', gap: 16 }}>
                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                                <Metric label="Segments" value={trimResult.segments?.length || 0} />
                                <Metric label="Frames Written" value={trimResult.frames_written || 0} />
                            </div>

                            <div>
                                <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 8 }}>Segments</div>
                                {trimResult.segments?.map((seg, i) => (
                                    <div key={i} style={{
                                        display: 'flex',
                                        alignItems: 'center',
                                        gap: 8,
                                        padding: '8px 12px',
                                        background: 'var(--bg-glass)',
                                        borderRadius: 8,
                                        marginBottom: 6,
                                        fontSize: 13,
                                    }}>
                                        <Clock size={12} />
                                        <span>Frame {seg[0]} to {seg[1]}</span>
                                        <span style={{ color: 'var(--text-secondary)', marginLeft: 'auto' }}>
                                            {seg[1] - seg[0] + 1} frames
                                        </span>
                                    </div>
                                ))}
                            </div>

                            <div style={{
                                background: '#000',
                                borderRadius: 12,
                                overflow: 'hidden',
                                border: '1px solid var(--border)',
                            }}>
                                <video
                                    key={trimResult.trimmed_video}
                                    controls
                                    autoPlay
                                    style={{ width: '100%', display: 'block' }}
                                    src={footageAPI.serveUrl(trimResult.trimmed_video)}
                                >
                                    Your browser does not support the video tag.
                                </video>
                            </div>

                            <a
                                href={footageAPI.serveUrl(trimResult.trimmed_video)}
                                download={trimResult.trimmed_video}
                                className="btn btn-secondary"
                                style={{ display: 'inline-flex', justifyContent: 'center', textDecoration: 'none' }}
                            >
                                <Download size={14} /> Download Reel
                            </a>
                        </div>
                    )}
                </ContentCard>
            </div>

            <ContentCard
                title="Retail Analytics"
                subtitle="Heatmap, timelines, traffic counts, movement distance, and fall candidates from the selected log"
                accent="violet"
            >
                {!selectedLog && <div className="page-empty-hint">Select a detection log to view analytics.</div>}
                {selectedLog && analyticsLoading && <div className="page-empty-hint">Loading analytics...</div>}
                {selectedLog && analytics && !analyticsLoading && (
                    <div style={{ display: 'grid', gap: 16 }}>
                        <div className="ui-status-grid">
                            <Metric label="Frames" value={analytics.frame_count || 0} />
                            <Metric label="Tracks" value={analytics.track_count || 0} />
                            <Metric label="Timelines" value={analytics.timelines?.length || 0} />
                            <Metric label="Fall candidates" value={analytics.fall_candidates?.length || 0} />
                        </div>

                        <div className="two-col" style={{ gap: 16 }}>
                            <div>
                                <div className="form-label">Heatmap</div>
                                <div
                                    style={{
                                        display: 'grid',
                                        gridTemplateColumns: `repeat(${analytics.heatmap?.bins || 4}, minmax(34px, 1fr))`,
                                        gap: 6,
                                    }}
                                >
                                    {(analytics.heatmap?.cells || []).map((cell) => {
                                        const alpha = Math.min(0.95, 0.12 + (cell.share || 0) * 6);
                                        return (
                                            <div
                                                key={`${cell.row}-${cell.col}`}
                                                title={`${cell.count} samples`}
                                                style={{
                                                    aspectRatio: '1',
                                                    borderRadius: 6,
                                                    border: '1px solid var(--border)',
                                                    background: `rgba(20, 184, 166, ${alpha})`,
                                                    display: 'grid',
                                                    placeItems: 'center',
                                                    fontSize: 12,
                                                    fontWeight: 700,
                                                }}
                                            >
                                                {cell.count}
                                            </div>
                                        );
                                    })}
                                </div>
                            </div>

                            <div>
                                <div className="form-label">Top Movement</div>
                                <div className="ui-feed-list" style={{ maxHeight: 220, overflow: 'auto' }}>
                                    {(analytics.distances || []).slice(0, 6).map((row) => (
                                        <div key={row.track_id} className="ui-lane-row" style={{ gridTemplateColumns: '1fr 90px 90px' }}>
                                            <span className="ui-lane-row-title">{row.track_id}</span>
                                            <span>{Math.round(row.distance_px)} px</span>
                                            <span>{row.frames_observed} frames</span>
                                        </div>
                                    ))}
                                    {(!analytics.distances || analytics.distances.length === 0) && (
                                        <div className="page-empty-hint">No movement samples yet.</div>
                                    )}
                                </div>
                            </div>
                        </div>

                        <div>
                            <div className="form-label">Track Timelines</div>
                            <div className="ui-feed-list" style={{ maxHeight: 220, overflow: 'auto' }}>
                                {(analytics.timelines || []).slice(0, 8).map((row) => (
                                    <div key={row.track_id} className="ui-lane-row" style={{ gridTemplateColumns: '1fr 110px 110px' }}>
                                        <span className="ui-lane-row-title">{row.track_id}</span>
                                        <span>Frame {row.first_frame}</span>
                                        <span>{row.duration_frames} frames</span>
                                    </div>
                                ))}
                                {(!analytics.timelines || analytics.timelines.length === 0) && (
                                    <div className="page-empty-hint">No tracks lasted long enough for timeline display.</div>
                                )}
                            </div>
                        </div>
                    </div>
                )}
            </ContentCard>
        </div>
    );
}

function Metric({ label, value }) {
    return (
        <div style={{
            padding: 12,
            border: '1px solid var(--border)',
            borderRadius: 8,
            background: 'var(--bg-glass)',
        }}>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>{label}</div>
            <div style={{ fontWeight: 700, fontSize: 18 }}>{value}</div>
        </div>
    );
}
