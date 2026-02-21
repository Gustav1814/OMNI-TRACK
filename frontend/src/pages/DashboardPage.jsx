/**
 * OmniTrack AI — Dashboard Overview Page
 * Fetches overview from API with loading and error states.
 */

import React, { useState, useEffect, useMemo, useCallback } from 'react';
import {
    BarChart, Bar, PieChart, Pie, Cell,
    XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Area, AreaChart
} from 'recharts';
import { Camera, Users, Scan, Flame, ShoppingCart, Clock, Loader2, AlertCircle, Video, Film, Upload, RefreshCw } from 'lucide-react';
import { dashboardAPI, detectionAPI, footageAPI, API_BASE } from '../services/api';

function formatWaitTime(seconds) {
    if (seconds == null || isNaN(seconds)) return '—';
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return s > 0 ? `${m}m ${s}s` : `${m}m`;
}

function formatPeakHour(hour) {
    if (hour == null) return '—';
    if (hour === 0) return '12 AM';
    if (hour < 12) return `${hour} AM`;
    if (hour === 12) return '12 PM';
    return `${hour - 12} PM`;
}

const trafficData = Array.from({ length: 13 }, (_, i) => ({
    hour: `${i + 9}:00`,
    visitors: Math.floor(30 + Math.random() * 80 + (i > 3 && i < 9 ? 40 : 0)),
    avgDwell: Math.floor(200 + Math.random() * 400),
}));

const zoneData = [
    { name: 'Electronics', value: 28 },
    { name: 'Groceries', value: 22 },
    { name: 'Clothing', value: 18 },
    { name: 'Home', value: 15 },
    { name: 'Other', value: 17 },
];
const COLORS = ['#6366f1', '#06b6d4', '#10b981', '#f59e0b', '#8b5cf6'];

const emotionData = [
    { emotion: 'Happy', count: 42, fill: '#10b981' },
    { emotion: 'Neutral', count: 35, fill: '#64748b' },
    { emotion: 'Surprised', count: 12, fill: '#f59e0b' },
    { emotion: 'Sad', count: 6, fill: '#3b82f6' },
    { emotion: 'Angry', count: 5, fill: '#f43f5e' },
];

const chartTooltipStyle = {
    contentStyle: {
        background: '#1e293b',
        border: '1px solid rgba(148,163,184,0.15)',
        borderRadius: '8px',
        fontSize: '12px',
        color: '#f1f5f9',
    },
};

export default function DashboardPage() {
    const [overview, setOverview] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [detectionStatus, setDetectionStatus] = useState(null);
    const [footageList, setFootageList] = useState([]);
    const [uploading, setUploading] = useState(false);
    const [uploadCamId, setUploadCamId] = useState(1);
    const token = useMemo(() => typeof localStorage !== 'undefined' ? localStorage.getItem('omnitrack_token') : null, []);

    const refreshFootage = useCallback(() => {
        footageAPI.list().then((r) => setFootageList(r.data || [])).catch(() => setFootageList([]));
    }, []);
    const handleFootageUpload = (e) => {
        const file = e.target?.files?.[0];
        if (!file) return;
        setUploading(true);
        footageAPI.upload(file, uploadCamId)
            .then(() => { refreshFootage(); e.target.value = ''; })
            .catch(() => {})
            .finally(() => setUploading(false));
    };

    useEffect(() => {
        let cancelled = false;
        setError(null);
        dashboardAPI.overview()
            .then((res) => {
                if (!cancelled) setOverview(res.data);
            })
            .catch((err) => {
                if (!cancelled) setError(err.response?.data?.detail || err.message || 'Failed to load dashboard');
            })
            .finally(() => {
                if (!cancelled) setLoading(false);
            });
        return () => { cancelled = true; };
    }, []);

    useEffect(() => {
        detectionAPI.status().then((r) => setDetectionStatus(r.data)).catch(() => setDetectionStatus(null));
    }, []);
    useEffect(() => {
        refreshFootage();
    }, [refreshFootage]);

    if (loading) {
        return (
            <div className="page-content" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: 360 }}>
                <div style={{ textAlign: 'center', color: 'var(--text-secondary)' }}>
                    <Loader2 size={40} className="animate-spin" style={{ margin: '0 auto 12px', display: 'block' }} />
                    <span>Loading dashboard…</span>
                </div>
            </div>
        );
    }

    if (error) {
        return (
            <div className="page-content" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: 360 }}>
                <div className="chart-card" style={{ maxWidth: 420, textAlign: 'center', padding: 32 }}>
                    <AlertCircle size={48} style={{ color: 'var(--accent-rose)', marginBottom: 16 }} />
                    <h3 style={{ marginBottom: 8 }}>Dashboard unavailable</h3>
                    <p style={{ color: 'var(--text-secondary)', fontSize: 14 }}>{error}</p>
                </div>
            </div>
        );
    }

    const o = overview;
    const vibe = o?.store_vibe || {};
    const stats = [
        { label: 'Active Cameras', value: `${o?.active_cameras ?? 0} / ${o?.total_cameras ?? 0}`, icon: Camera, color: '#6366f1' },
        { label: 'Current Occupancy', value: String(o?.current_occupancy ?? 0), icon: Users, color: '#06b6d4' },
        { label: 'Detections Today', value: (o?.total_detections_today ?? 0).toLocaleString(), icon: Scan, color: '#10b981' },
        { label: 'Fire Alerts', value: String(o?.fire_alerts_today ?? 0), icon: Flame, color: '#f43f5e' },
        { label: 'Avg Wait Time', value: formatWaitTime(o?.avg_checkout_wait), icon: ShoppingCart, color: '#f59e0b' },
        { label: 'Peak Hour', value: formatPeakHour(o?.peak_hour_today), icon: Clock, color: '#8b5cf6' },
    ];

    const vibeBars = [
        { label: 'Sentiment', value: Math.round(vibe.sentiment_score ?? 0), color: '#6366f1' },
        { label: 'Energy', value: Math.round(vibe.energy_score ?? 0), color: '#06b6d4' },
        { label: 'Engagement', value: Math.round(vibe.engagement_score ?? 0), color: '#10b981' },
        { label: 'Foot Traffic', value: Math.round(vibe.foot_traffic_score ?? 0), color: '#f59e0b' },
    ];

    return (
        <div>
            {/* Stat Cards */}
            <div className="stats-grid">
                {stats.map((s, i) => {
                    const Icon = s.icon;
                    return (
                        <div key={i} className="stat-card animate-in">
                            <div className="stat-card-header">
                                <span className="stat-card-label">{s.label}</span>
                                <div className="stat-card-icon" style={{ background: `${s.color}15`, color: s.color }}>
                                    <Icon size={18} />
                                </div>
                            </div>
                            <div className="stat-card-value">{s.value}</div>
                            {o?.top_zone && s.label === 'Peak Hour' && (
                                <div className="stat-card-change positive">Top zone: {o.top_zone}</div>
                            )}
                        </div>
                    );
                })}
            </div>

            {/* Vibe Score + Traffic Chart */}
            <div className="two-col">
                <div className="chart-card animate-in">
                    <div className="chart-card-title">Store Vibe Score</div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 30, padding: '10px 0' }}>
                        <div className="vibe-meter">
                            <div className="vibe-meter-ring">
                                <div className="vibe-meter-inner">
                                    <div className="vibe-score-value">{Math.round(vibe.overall_score ?? 0)}</div>
                                    <div className="vibe-score-label">{vibe.vibe_label || '—'}</div>
                                </div>
                            </div>
                        </div>
                        <div style={{ flex: 1 }}>
                            {vibeBars.map((m) => (
                                <div key={m.label} style={{ marginBottom: 12 }}>
                                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 4 }}>
                                        <span style={{ color: 'var(--text-secondary)' }}>{m.label}</span>
                                        <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{m.value}%</span>
                                    </div>
                                    <div className="progress-bar">
                                        <div className="progress-bar-fill" style={{ width: `${Math.min(100, m.value)}%`, background: m.color }} />
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>
                </div>

                <div className="chart-card animate-in">
                    <div className="chart-card-title">Foot Traffic Today</div>
                    <ResponsiveContainer width="100%" height={240}>
                        <AreaChart data={trafficData}>
                            <defs>
                                <linearGradient id="colorVisitors" x1="0" y1="0" x2="0" y2="1">
                                    <stop offset="5%" stopColor="#6366f1" stopOpacity={0.3} />
                                    <stop offset="95%" stopColor="#6366f1" stopOpacity={0} />
                                </linearGradient>
                            </defs>
                            <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.08)" />
                            <XAxis dataKey="hour" tick={{ fontSize: 11, fill: '#64748b' }} />
                            <YAxis tick={{ fontSize: 11, fill: '#64748b' }} />
                            <Tooltip {...chartTooltipStyle} />
                            <Area type="monotone" dataKey="visitors" stroke="#6366f1" fillOpacity={1} fill="url(#colorVisitors)" strokeWidth={2} />
                        </AreaChart>
                    </ResponsiveContainer>
                </div>
            </div>

            {/* Store cameras — live computer vision (same as deployed in-store) */}
            <div className="chart-card animate-in" style={{ marginBottom: 24 }}>
                <div className="chart-card-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <Video size={20} />
                    Store cameras — live computer vision
                </div>
                <p style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 12 }}>
                    All store feeds with real-time detection, tracking & Re-ID — as when deployed. Use Person Detection to add cameras (from footage or live).
                </p>
                {detectionStatus?.active_cameras?.length > 1 && (
                    <p style={{ fontSize: 11, color: 'var(--accent-primary)', marginBottom: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
                        <span style={{ fontWeight: 600 }}>Global Re-ID:</span> One identity across all cameras — same person on different feeds gets the same ID.
                    </p>
                )}
                {detectionStatus?.active_cameras?.length > 0 ? (
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 16 }}>
                        {detectionStatus.active_cameras.map((camId) => (
                            <div key={camId} style={{ borderRadius: 8, overflow: 'hidden', border: '1px solid var(--border)', background: '#0f172a' }}>
                                <div style={{ padding: '6px 10px', background: 'var(--bg-secondary)', fontSize: 12, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                    <span style={{ fontWeight: 600 }}>Camera {camId}</span>
                                    <span style={{ color: 'var(--accent-rose)', display: 'flex', alignItems: 'center', gap: 4 }}>
                                        <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'currentColor' }} /> LIVE · CV
                                    </span>
                                </div>
                                <div style={{ aspectRatio: '16/9', background: '#000', display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: 180 }}>
                                    {token ? (
                                        <img
                                            src={`${API_BASE}/stream/camera/${camId}/live?token=${encodeURIComponent(token)}`}
                                            alt={`Camera ${camId} live`}
                                            style={{ width: '100%', height: '100%', objectFit: 'contain' }}
                                        />
                                    ) : (
                                        <span style={{ color: 'var(--text-muted)' }}>Log in to view stream</span>
                                    )}
                                </div>
                            </div>
                        ))}
                    </div>
                ) : (
                    <div style={{ textAlign: 'center', padding: 32, color: 'var(--text-secondary)', fontSize: 14 }}>
                        <Camera size={40} style={{ opacity: 0.4, marginBottom: 8 }} />
                        <p>No store cameras live. On <strong>Person Detection</strong>: add your downloaded CCTV as store cameras, then start — they appear here with full CV.</p>
                    </div>
                )}
            </div>

            {/* Stored footage — play recorded clips */}
            <div className="chart-card animate-in" style={{ marginBottom: 24 }}>
                <div className="chart-card-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <Film size={20} />
                    Stored CCTV Footage
                </div>
                <p style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 12 }}>
                    <strong>Prototype:</strong> Download store CCTV (one video per camera), upload here, then run them as live store cameras on Person Detection — full CV on each feed.
                </p>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
                    <button type="button" onClick={refreshFootage} className="btn" style={{ fontSize: 12, padding: '6px 10px', display: 'flex', alignItems: 'center', gap: 6 }}>
                        <RefreshCw size={14} /> Refresh list
                    </button>
                    <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>Camera ID</label>
                    <input
                        type="number"
                        min={1}
                        value={uploadCamId}
                        onChange={(e) => setUploadCamId(Number(e.target.value) || 1)}
                        style={{ width: 72, padding: '6px 8px', background: 'var(--bg-secondary)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text-primary)' }}
                    />
                    <input
                        type="file"
                        accept=".mp4,.avi,.mkv,.webm,.mov"
                        style={{ display: 'none' }}
                        id="footage-upload"
                        onChange={handleFootageUpload}
                    />
                    <button
                        type="button"
                        className="btn btn-primary"
                        style={{ display: 'flex', alignItems: 'center', gap: 6 }}
                        onClick={() => document.getElementById('footage-upload')?.click()}
                        disabled={uploading}
                    >
                        {uploading ? <Loader2 size={16} className="animate-spin" /> : <Upload size={16} />}
                        Upload clip
                    </button>
                </div>
                {footageList.length > 0 ? (
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 12 }}>
                        {footageList.map((item) => (
                            <div key={item.filename} style={{ border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden', background: 'var(--bg-secondary)' }}>
                                <div style={{ aspectRatio: '16/9', background: '#000' }}>
                                    <video
                                        controls
                                        style={{ width: '100%', height: '100%', objectFit: 'contain' }}
                                        src={token ? `${footageAPI.serveUrl(item.filename)}?token=${encodeURIComponent(token)}` : undefined}
                                        preload="metadata"
                                    >
                                        Your browser does not support video.
                                    </video>
                                </div>
                                <div style={{ padding: 8, fontSize: 12 }}>
                                    <div style={{ fontWeight: 500 }}>{item.filename}</div>
                                    <div style={{ color: 'var(--text-secondary)' }}>
                                        {item.camera_id != null && `Cam ${item.camera_id} · `}
                                        {(item.size_bytes / 1024 / 1024).toFixed(2)} MB
                                    </div>
                                </div>
                            </div>
                        ))}
                    </div>
                ) : (
                    <div style={{ textAlign: 'center', padding: 24, color: 'var(--text-secondary)', fontSize: 14 }}>
                        No stored clips yet. Use the Detection page to record or upload footage.
                    </div>
                )}
            </div>

            {/* Zone Distribution + Emotions */}
            <div className="two-col">
                <div className="chart-card animate-in">
                    <div className="chart-card-title">Zone Distribution</div>
                    <ResponsiveContainer width="100%" height={240}>
                        <PieChart>
                            <Pie data={zoneData} cx="50%" cy="50%" innerRadius={65} outerRadius={95} paddingAngle={3} dataKey="value">
                                {zoneData.map((_, i) => <Cell key={i} fill={COLORS[i]} />)}
                            </Pie>
                            <Tooltip {...chartTooltipStyle} />
                        </PieChart>
                    </ResponsiveContainer>
                    <div style={{ display: 'flex', justifyContent: 'center', gap: 16, flexWrap: 'wrap' }}>
                        {zoneData.map((z, i) => (
                            <span key={i} style={{ fontSize: 11, color: 'var(--text-secondary)', display: 'flex', alignItems: 'center', gap: 4 }}>
                                <span style={{ width: 8, height: 8, borderRadius: 2, background: COLORS[i], display: 'inline-block' }} /> {z.name}
                            </span>
                        ))}
                    </div>
                </div>

                <div className="chart-card animate-in">
                    <div className="chart-card-title">Customer Emotions</div>
                    <ResponsiveContainer width="100%" height={260}>
                        <BarChart data={emotionData} layout="vertical">
                            <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.08)" />
                            <XAxis type="number" tick={{ fontSize: 11, fill: '#64748b' }} />
                            <YAxis dataKey="emotion" type="category" tick={{ fontSize: 12, fill: '#94a3b8' }} width={80} />
                            <Tooltip {...chartTooltipStyle} />
                            <Bar dataKey="count" radius={[0, 4, 4, 0]} barSize={18}>
                                {emotionData.map((e, i) => <Cell key={i} fill={e.fill} />)}
                            </Bar>
                        </BarChart>
                    </ResponsiveContainer>
                </div>
            </div>
        </div>
    );
}
