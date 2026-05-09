/**
 * OmniTrack AI — Video Trimmer by Track ID
 * Trim recorded videos to show only frames where a specific track_id is visible.
 */

import React, { useState, useMemo } from 'react';
import { motion } from 'framer-motion';
import { Scissors, Film, Clock, Download, Play, RefreshCw, AlertCircle } from 'lucide-react';
import { footageAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';

export default function TrimPage() {
    const [selectedLog, setSelectedLog] = useState('');
    const [trackIdInput, setTrackIdInput] = useState('');
    const [paddingFrames, setPaddingFrames] = useState(5);
    const [trimming, setTrimming] = useState(false);
    const [trimResult, setTrimResult] = useState(null);
    const [error, setError] = useState(null);

    const { data: logs, refresh: refreshLogs } = useLivePoll(
        () => footageAPI.logsList(), { intervalMs: 10000 }
    );

    const handleLogChange = (filename) => {
        setSelectedLog(filename);
        setTrackIdInput('');
        setTrimResult(null);
    };

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
            // Auto-refresh logs to show the new trimmed video
            await refreshLogs();
        } catch (e) {
            setError(e?.response?.data?.detail || e.message);
        } finally {
            setTrimming(false);
        }
    };

    const selectedLogInfo = useMemo(() => {
        return logs?.find(l => l.filename === selectedLog);
    }, [logs, selectedLog]);

    return (
        <div className="page-scroll">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Video Trimmer</h1>
                    <p className="page-subtitle">Extract clips for specific tracked objects using detection logs</p>
                </div>
                <button className="btn btn-secondary btn-xs" onClick={refreshLogs}>
                    <RefreshCw size={12} /> Refresh
                </button>
            </div>

            {error && (
                <div className="alert-banner danger" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <AlertCircle size={16} />
                    {error}
                </div>
            )}

            <div className="two-col">
                {/* Left: Selection Panel */}
                <div className="card">
                    <div className="card-header">
                        <h3 className="card-title"><Scissors size={16} style={{ verticalAlign: -3, marginRight: 6 }} /> Select Track</h3>
                        <div className="card-subtitle">Choose a recorded video and track ID to trim</div>
                    </div>

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
                                    Camera {selectedLogInfo.camera_id} • {selectedLogInfo.model} • {selectedLogInfo.total_frames} frames
                                </div>
                            )}
                        </div>

                        <div>
                            <label className="form-label">Track ID</label>
                            <input
                                type="number"
                                className="form-input"
                                placeholder="Enter track ID (e.g., 1, 2, 3...)"
                                value={trackIdInput}
                                onChange={(e) => setTrackIdInput(e.target.value)}
                                disabled={!selectedLog}
                                min={0}
                            />
                            <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 4 }}>
                                Type the track ID you want to extract from the video
                            </div>
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
                            <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 4 }}>
                                Extra frames before/after each appearance for context
                            </div>
                        </div>

                        <button
                            className="btn btn-primary"
                            onClick={handleTrim}
                            disabled={!selectedLog || !trackIdInput || trimming}
                        >
                            {trimming ? (
                                <><RefreshCw size={14} className="spin" /> Trimming...</>
                            ) : (
                                <><Scissors size={14} /> Trim Video</>
                            )}
                        </button>
                    </div>
                </div>

                {/* Right: Preview / Results */}
                <div className="card">
                    <div className="card-header">
                        <h3 className="card-title"><Play size={16} style={{ verticalAlign: -3, marginRight: 6 }} /> Trim Result</h3>
                        <div className="card-subtitle">Preview and download the trimmed clip</div>
                    </div>

                    {!trimResult && !trimming && (
                        <div className="page-empty-hint">
                            Select a log and track, then click Trim Video to generate the clip.
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
                                Extracting frames and building video...
                            </p>
                        </div>
                    )}

                    {trimResult && (
                        <div style={{ display: 'grid', gap: 16 }}>
                            <div style={{
                                background: 'var(--bg-glass)',
                                borderRadius: 12,
                                padding: 16,
                                border: '1px solid var(--border)'
                            }}>
                                <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>Output File</div>
                                <div style={{ fontWeight: 600, fontSize: 14 }}>{trimResult.trimmed_video}</div>
                            </div>

                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                                <div style={{
                                    background: 'var(--bg-glass)',
                                    borderRadius: 12,
                                    padding: 12,
                                    border: '1px solid var(--border)'
                                }}>
                                    <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>Segments</div>
                                    <div style={{ fontWeight: 600 }}>{trimResult.segments?.length || 0}</div>
                                </div>
                                <div style={{
                                    background: 'var(--bg-glass)',
                                    borderRadius: 12,
                                    padding: 12,
                                    border: '1px solid var(--border)'
                                }}>
                                    <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>Frames Written</div>
                                    <div style={{ fontWeight: 600 }}>{trimResult.frames_written}</div>
                                </div>
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
                                        fontSize: 13
                                    }}>
                                        <Clock size={12} />
                                        <span>Frame {seg[0]} to {seg[1]}</span>
                                        <span style={{ color: 'var(--text-secondary)', marginLeft: 'auto' }}>
                                            {seg[1] - seg[0] + 1} frames
                                        </span>
                                    </div>
                                ))}
                            </div>

                            {/* Inline Video Player */}
                            <div style={{
                                background: '#000',
                                borderRadius: 12,
                                overflow: 'hidden',
                                border: '1px solid var(--border)'
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

                            <div style={{ display: 'flex', gap: 12 }}>
                                <a
                                    href={footageAPI.serveUrl(trimResult.trimmed_video)}
                                    download={trimResult.trimmed_video}
                                    className="btn btn-secondary"
                                    style={{ flex: 1, display: 'inline-flex', justifyContent: 'center' }}
                                >
                                    <Download size={14} /> Download
                                </a>
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
