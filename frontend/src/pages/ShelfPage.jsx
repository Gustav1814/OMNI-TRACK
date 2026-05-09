/**
 * OmniTrack AI — Shelf Engagement (live)
 * Interactive zone editor: draw, resize, rename, save zones on live feed.
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { motion } from 'framer-motion';
import { ShoppingBag, Clock, Award, Plus, Trash2, Save, Eye, MousePointer, Hand, PackageOpen, Package } from 'lucide-react';
import {
    BarChart, Bar, ResponsiveContainer, XAxis, YAxis, Tooltip, CartesianGrid,
} from 'recharts';
import { shelfAPI, liveStreamUrl } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';

export default function ShelfPage() {
    const { data: engagement } = useLivePoll(() => shelfAPI.engagement(), { intervalMs: 15000 });
    const { data: topZones } = useLivePoll(() => shelfAPI.topZones(), { intervalMs: 30000 });
    const { data: events } = useLivePoll(() => shelfAPI.events(undefined, 30), { intervalMs: 10000 });
    const { data: itemsByCam } = useLivePoll(() => shelfAPI.items(), { intervalMs: 10000 });
    const [zones, setZones] = useState([]);
    const [cameras, setCameras] = useState([]);
    const [selectedCam, setSelectedCam] = useState('');
    const [error] = useState(null);

    const loadZones = useCallback(async () => {
        try {
            const res = await shelfAPI.listZones();
            setZones(res.data || []);
        } catch (e) {
            console.error('Failed to load zones:', e);
        }
    }, []);

    const loadCameras = useCallback(async () => {
        try {
            const res = await shelfAPI.cameras();
            const cams = res.data || [];
            setCameras(cams);
            if (cams.length > 0 && !selectedCam) {
                setSelectedCam(String(cams[0].id));
            }
        } catch (e) {
            console.error('Failed to load cameras:', e);
        }
    }, [selectedCam]);

    useEffect(() => {
        loadZones();
        loadCameras();
    }, [loadZones, loadCameras]);

    const handleDeleteZone = async (zoneId) => {
        try {
            await shelfAPI.deleteZone(zoneId);
            await loadZones();
        } catch (e) {
            console.error('Failed to delete zone:', e);
        }
    };

    const engagementZones = Array.isArray(engagement) ? engagement : [];
    const chartData = engagementZones.slice(0, 10).map((z) => ({
        name: z.zone_name,
        score: z.engagement_score,
        dwell: z.avg_dwell_time,
    }));

    const topZonesList = Array.isArray(topZones) ? topZones : (topZones?.top_zones || []);

    return (
        <div className="page-scroll">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Shelf Engagement</h1>
                    <p className="page-subtitle">Dwell time · visits · top-selling zones</p>
                </div>
            </div>

            <div className="stats-grid">
                <Stat icon={ShoppingBag} label="Zones Tracked" value={engagementZones.length} accent="indigo" />
                <Stat
                    icon={Clock}
                    label="Avg Dwell"
                    value={engagementZones.length ? (engagementZones.reduce((a, z) => a + (z.avg_dwell_time || 0), 0) / engagementZones.length).toFixed(1) : 0}
                    suffix="s"
                    accent="cyan"
                />
                <Stat
                    icon={Award}
                    label="Top Zone"
                    value={engagementZones[0]?.zone_name || '—'}
                    accent="gold"
                />
                <Stat
                    icon={ShoppingBag}
                    label="Total Visits"
                    value={engagementZones.reduce((a, z) => a + (z.visit_count || 0), 0)}
                    accent="emerald"
                />
            </div>

            <div className="card">
                <div className="card-header">
                    <h3 className="card-title">Engagement Score by Zone</h3>
                    <div className="card-subtitle">Top 10 shelves</div>
                </div>
                {chartData.length === 0 ? (
                    <div className="page-empty-hint">
                        No engagement data yet — run the pipeline on a clip with a shelf camera.
                    </div>
                ) : (
                    <div style={{ height: 340 }}>
                        <ResponsiveContainer>
                            <BarChart data={chartData} layout="vertical">
                                <CartesianGrid stroke="rgba(255,255,255,0.05)" />
                                <XAxis type="number" stroke="#71717a" fontSize={11} />
                                <YAxis dataKey="name" type="category" stroke="#71717a" fontSize={11} width={120} />
                                <Tooltip contentStyle={{ background: '#111', border: '1px solid #222' }} />
                                <Bar dataKey="score" fill="#d4af37" radius={[0, 6, 6, 0]} />
                            </BarChart>
                        </ResponsiveContainer>
                    </div>
                )}
            </div>

            {/* ── Zone Editor ── */}
            <div className="card">
                <div className="card-header">
                    <h3 className="card-title">
                        <Eye size={16} style={{ verticalAlign: -2, marginRight: 6 }} />
                        Zone Editor
                    </h3>
                    <div className="card-subtitle">Select a camera then draw zones on the live feed</div>
                </div>

                {/* Camera selector */}
                <div style={{ display: 'flex', gap: 10, marginBottom: 16, alignItems: 'center' }}>
                    <select
                        value={selectedCam}
                        onChange={(e) => setSelectedCam(e.target.value)}
                        style={{
                            background: 'var(--bg-tertiary)',
                            border: '1px solid var(--border)',
                            borderRadius: 8, padding: '8px 14px',
                            color: 'var(--text-primary)', minWidth: 160, fontSize: 13,
                        }}
                    >
                        <option value="">Select camera…</option>
                        {cameras.map((c) => (
                            <option key={c.id} value={c.id}>
                                Camera {c.id}{c.zone ? ` (${c.zone})` : ''}
                            </option>
                        ))}
                    </select>
                    {error && <span style={{ fontSize: 12, color: '#fca5a5' }}>{error}</span>}
                </div>

                {selectedCam ? (
                    <ZoneEditor
                        cameraId={Number(selectedCam)}
                        zones={zones.filter((z) => z.camera_id === Number(selectedCam))}
                        engagementZones={engagementZones}
                        onZoneAdded={loadZones}
                        onZoneDeleted={handleDeleteZone}
                    />
                ) : (
                    <div style={{ color: 'var(--text-muted)', fontSize: 13, padding: '40px 0', textAlign: 'center' }}>
                        Select a camera above to start drawing zones.
                    </div>
                )}
            </div>

            {/* ── Live engagement table ── */}
            {engagementZones.length > 0 && (
                <div className="card">
                    <div className="card-header">
                        <h3 className="card-title">All Zones — Live Data</h3>
                    </div>
                    <div style={{ display: 'grid', gap: 6 }}>
                        {engagementZones.map((z) => (
                            <div key={z.zone_id} style={{
                                display: 'grid',
                                gridTemplateColumns: '40px 1fr 90px 90px 90px',
                                gap: 10, alignItems: 'center',
                                padding: '10px 14px',
                                background: 'var(--bg-glass)',
                                border: '1px solid var(--border)', borderRadius: 10,
                            }}>
                                <span className="pill pill-info" style={{ justifyContent: 'center' }}>#{z.rank}</span>
                                <span style={{ fontWeight: 600 }}>{z.zone_name}</span>
                                <span style={{ fontSize: 12 }}>{z.visit_count} visits</span>
                                <span style={{ fontSize: 12 }}>{z.avg_dwell_time?.toFixed(1)}s dwell</span>
                                <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--accent-gold)' }}>
                                    {z.engagement_score?.toFixed(1)} score
                                </span>
                            </div>
                        ))}
                    </div>
                </div>
            )}

            {/* ── Live Activity Feed (pick / put-back) ── */}
            <ActivityFeed events={Array.isArray(events) ? events : []} />

            {/* ── Items detected per camera ── */}
            <ItemsSummary itemsByCam={itemsByCam || {}} />
        </div>
    );
}

/* ──────────────────────────────────────────
   Live Activity Feed
   ────────────────────────────────────────── */
function ActivityFeed({ events }) {
    return (
        <div className="card">
            <div className="card-header">
                <h3 className="card-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <Hand size={16} /> Shelf Activity — Pick / Put-back Events
                </h3>
                <div className="card-subtitle">
                    {events.length === 0
                        ? 'Waiting for shelf interactions…'
                        : `${events.length} recent event${events.length === 1 ? '' : 's'}`}
                </div>
            </div>
            {events.length === 0 ? (
                <div style={{ color: 'var(--text-muted)', fontSize: 13, padding: '16px 0' }}>
                    Customers reaching into a shelf zone will appear here.
                </div>
            ) : (
                <div style={{ display: 'grid', gap: 6, maxHeight: 280, overflowY: 'auto' }}>
                    {events.map((e, i) => {
                        const isPick = e.event_type === 'pick';
                        const color = isPick ? '#f59e0b' : '#10b981';
                        const Icon = isPick ? PackageOpen : Package;
                        const ts = new Date((e.timestamp || 0) * 1000);
                        return (
                            <div key={`${e.zone_id}_${e.timestamp}_${i}`} style={{
                                display: 'grid',
                                gridTemplateColumns: '24px 90px 1fr 80px 90px',
                                gap: 10, alignItems: 'center',
                                padding: '8px 12px',
                                background: 'var(--bg-glass)',
                                border: `1px solid ${color}33`, borderRadius: 10,
                            }}>
                                <Icon size={16} color={color} />
                                <span style={{
                                    fontSize: 11, fontWeight: 700, color,
                                    textTransform: 'uppercase', letterSpacing: 0.5,
                                }}>
                                    {isPick ? 'Pick' : 'Put back'}
                                </span>
                                <span style={{ fontWeight: 600 }}>{e.zone_name}</span>
                                <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                                    cam {e.camera_id}{e.track_id != null ? ` · #${e.track_id}` : ''}
                                </span>
                                <span style={{ fontSize: 11, color: 'var(--text-muted)', textAlign: 'right' }}>
                                    {isNaN(ts) ? '—' : ts.toLocaleTimeString()}
                                </span>
                            </div>
                        );
                    })}
                </div>
            )}
        </div>
    );
}

/* ──────────────────────────────────────────
   Items detected per camera (COCO / custom)
   ────────────────────────────────────────── */
function ItemsSummary({ itemsByCam }) {
    const cams = Object.keys(itemsByCam || {});
    if (cams.length === 0) return null;
    /* Aggregate counts by class_name across all cameras for a quick view. */
    const counts = {};
    cams.forEach((cam) => {
        (itemsByCam[cam] || []).forEach((it) => {
            counts[it.class_name] = (counts[it.class_name] || 0) + 1;
        });
    });
    const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 12);
    if (sorted.length === 0) {
        return null;
    }
    return (
        <div className="card">
            <div className="card-header">
                <h3 className="card-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <Package size={16} /> Items Detected (live)
                </h3>
                <div className="card-subtitle">Generic objects + any custom product classes</div>
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                {sorted.map(([name, count]) => (
                    <span
                        key={name}
                        className="pill pill-info"
                        style={{ padding: '6px 12px', fontSize: 12 }}
                    >
                        {name} <strong style={{ marginLeft: 6 }}>{count}</strong>
                    </span>
                ))}
            </div>
        </div>
    );
}

/* ──────────────────────────────────────────
   Zone colour palette
   ────────────────────────────────────────── */
const ZONE_COLORS = [
    'rgba(99,102,241,0.30)',
    'rgba(16,185,129,0.30)',
    'rgba(245,158,11,0.30)',
    'rgba(239,68,68,0.30)',
    'rgba(59,130,246,0.30)',
    'rgba(168,85,247,0.30)',
    'rgba(236,72,153,0.30)',
    'rgba(20,184,166,0.30)',
];
const ZONE_BORDERS = [
    '#6366f1', '#10b981', '#f59e0b', '#ef4444',
    '#3b82f6', '#a855f7', '#ec4899', '#14b8a6',
];

/* ──────────────────────────────────────────
   Interactive Zone Editor
   Modes: 'view' | 'draw'
   ────────────────────────────────────────── */
function ZoneEditor({ cameraId, zones, engagementZones, onZoneAdded, onZoneDeleted }) {
    const svgRef = useRef(null);
    const imgRef = useRef(null);
    const [natSize, setNatSize] = useState({ w: 1920, h: 1080 });
    const [mode, setMode] = useState('view');          // 'view' | 'draw'
    const [drawing, setDrawing] = useState(null);      // { x1,y1,x2,y2 } in video coords
    const [editingLabel, setEditingLabel] = useState(null); // zone_id being renamed
    const [labelDraft, setLabelDraft] = useState('');
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);
    const [resizing, setResizing] = useState(null);    // { zone_id, handle, origBbox, startPt }
    const [dragging, setDragging] = useState(null);    // { zone_id, origBbox, startPt }
    const [localZones, setLocalZones] = useState([]);  // optimistic local edits
    const [unsaved, setUnsaved] = useState(false);     // true when local differs from server

    /* Only sync from server props when there are no unsaved local edits.
       Parent polls engagement every 5s, causing this prop to get a new array
       reference each tick — without this guard, in-progress drawing/dragging
       gets wiped before the user can press Save. */
    useEffect(() => {
        if (!unsaved && !drawing && !resizing && !dragging) {
            setLocalZones(zones);
        }
    }, [zones, unsaved, drawing, resizing, dragging]);

    const onImgLoad = useCallback(() => {
        const el = imgRef.current;
        if (!el) return;
        setNatSize({ w: el.naturalWidth || 1920, h: el.naturalHeight || 1080 });
    }, []);

    /* Convert mouse event → video coordinate space */
    const svgPt = useCallback((e) => {
        const svg = svgRef.current;
        if (!svg) return { x: 0, y: 0 };
        const rect = svg.getBoundingClientRect();
        return {
            x: Math.round(((e.clientX - rect.left) / rect.width) * natSize.w),
            y: Math.round(((e.clientY - rect.top) / rect.height) * natSize.h),
        };
    }, [natSize]);

    /* ── Draw mode handlers ── */
    const onMouseDown = useCallback((e) => {
        if (mode !== 'draw') return;
        e.preventDefault();
        const pt = svgPt(e);
        setDrawing({ x1: pt.x, y1: pt.y, x2: pt.x, y2: pt.y });
    }, [mode, svgPt]);

    const onMouseMove = useCallback((e) => {
        if (drawing) {
            const pt = svgPt(e);
            setDrawing((d) => ({ ...d, x2: pt.x, y2: pt.y }));
            return;
        }
        if (resizing) {
            const pt = svgPt(e);
            const { zone_id, handle, origBbox, startPt } = resizing;
            const dx = pt.x - startPt.x;
            const dy = pt.y - startPt.y;
            let [x1, y1, x2, y2] = origBbox;
            if (handle.includes('w')) x1 = Math.min(x1 + dx, x2 - 20);
            if (handle.includes('e')) x2 = Math.max(x2 + dx, x1 + 20);
            if (handle.includes('n')) y1 = Math.min(y1 + dy, y2 - 20);
            if (handle.includes('s')) y2 = Math.max(y2 + dy, y1 + 20);
            setLocalZones((zz) => zz.map((z) =>
                z.zone_id === zone_id ? { ...z, bbox: [x1, y1, x2, y2] } : z
            ));
            return;
        }
        if (dragging) {
            const pt = svgPt(e);
            const { zone_id, origBbox, startPt } = dragging;
            const dx = pt.x - startPt.x;
            const dy = pt.y - startPt.y;
            const [x1, y1, x2, y2] = origBbox;
            setLocalZones((zz) => zz.map((z) =>
                z.zone_id === zone_id
                    ? { ...z, bbox: [x1 + dx, y1 + dy, x2 + dx, y2 + dy] }
                    : z
            ));
        }
    }, [drawing, resizing, dragging, svgPt]);

    const onMouseUp = useCallback(async (e) => {
        /* Finish drawing a new zone — save immediately */
        if (drawing) {
            const pt = svgPt(e);
            const x1 = Math.min(drawing.x1, pt.x);
            const y1 = Math.min(drawing.y1, pt.y);
            const x2 = Math.max(drawing.x1, pt.x);
            const y2 = Math.max(drawing.y1, pt.y);
            setDrawing(null);
            if (x2 - x1 < 20 || y2 - y1 < 20) return;
            const idx = localZones.length;
            const zoneName = `Camera ${cameraId} - Zone ${idx + 1}`;
            const newZone = {
                zone_id: `cam${cameraId}_zone_${Date.now()}`,
                zone_name: zoneName,
                bbox: [x1, y1, x2, y2],
                camera_id: cameraId,
            };
            setLocalZones((zz) => [...zz, newZone]);
            setUnsaved(true);
            return;
        }
        /* Finish resizing — mark unsaved */
        if (resizing) {
            setResizing(null);
            setUnsaved(true);
            return;
        }
        /* Finish dragging — mark unsaved */
        if (dragging) {
            setDragging(null);
            setUnsaved(true);
        }
    }, [drawing, resizing, dragging, localZones, svgPt, cameraId]);

    /* Save ALL localZones to backend (delete old ones, add new set) */
    const saveAllZones = async () => {
        setSaving(true);
        try {
            /* Remove zones that no longer exist locally */
            const localIds = new Set(localZones.map((z) => z.zone_id));
            for (const z of zones) {
                if (!localIds.has(z.zone_id)) await shelfAPI.deleteZone(z.zone_id).catch(() => {});
            }
            /* Upsert every local zone (delete + re-add to update bbox/name) */
            for (const z of localZones) {
                await shelfAPI.deleteZone(z.zone_id).catch(() => {});
                await shelfAPI.addZone({
                    zone_id: z.zone_id,
                    zone_name: z.zone_name,
                    bbox: z.bbox,
                    camera_id: z.camera_id,
                });
            }
            await onZoneAdded();
            setUnsaved(false);
            setSaved(true);
            setTimeout(() => setSaved(false), 2000);
        } catch (err) {
            console.error('Failed to save zones:', err);
        } finally {
            setSaving(false);
        }
    };

    const startRename = (zone) => {
        setEditingLabel(zone.zone_id);
        setLabelDraft(zone.zone_name);
    };

    const commitRename = async (zone) => {
        if (!labelDraft.trim() || labelDraft === zone.zone_name) {
            setEditingLabel(null);
            return;
        }
        try {
            await shelfAPI.deleteZone(zone.zone_id);
            await shelfAPI.addZone({
                zone_id: zone.zone_id,
                zone_name: labelDraft.trim(),
                bbox: zone.bbox,
                camera_id: zone.camera_id,
            });
            await onZoneAdded();
        } catch (err) {
            console.error('Failed to rename zone:', err);
        } finally {
            setEditingLabel(null);
        }
    };

    const HANDLE_SIZE = 28;
    const handles = ['nw', 'ne', 'sw', 'se'];
    const handlePos = (x1, y1, x2, y2, h) => ({
        nw: [x1, y1], ne: [x2, y1], sw: [x1, y2], se: [x2, y2],
    })[h];
    const handleCursor = { nw: 'nw-resize', ne: 'ne-resize', sw: 'sw-resize', se: 'se-resize' };

    return (
        <div>
            {/* Toolbar */}
            <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center' }}>
                <button
                    onClick={() => setMode('view')}
                    style={{
                        display: 'flex', alignItems: 'center', gap: 6,
                        padding: '7px 14px', borderRadius: 8,
                        border: '1px solid var(--border)',
                        background: mode === 'view' ? 'var(--accent-primary)' : 'var(--bg-glass)',
                        color: mode === 'view' ? '#000' : 'var(--text-primary)',
                        fontWeight: 600, cursor: 'pointer', fontSize: 13,
                    }}
                >
                    <MousePointer size={14} /> View
                </button>
                <button
                    onClick={() => setMode('draw')}
                    style={{
                        display: 'flex', alignItems: 'center', gap: 6,
                        padding: '7px 14px', borderRadius: 8,
                        border: '1px solid var(--border)',
                        background: mode === 'draw' ? '#6366f1' : 'var(--bg-glass)',
                        color: mode === 'draw' ? '#fff' : 'var(--text-primary)',
                        fontWeight: 600, cursor: 'pointer', fontSize: 13,
                    }}
                >
                    <Plus size={14} /> Draw Zone
                </button>
                {unsaved && (
                    <button
                        onClick={saveAllZones}
                        disabled={saving}
                        style={{
                            display: 'flex', alignItems: 'center', gap: 6,
                            padding: '7px 18px', borderRadius: 8,
                            border: 'none',
                            background: saving ? '#555' : '#10b981',
                            color: '#fff', fontWeight: 700,
                            cursor: saving ? 'not-allowed' : 'pointer',
                            fontSize: 13, boxShadow: '0 0 12px rgba(16,185,129,0.5)',
                        }}
                    >
                        <Save size={14} />
                        {saving ? 'Saving…' : 'Save Changes'}
                    </button>
                )}
                {saved && !unsaved && (
                    <span style={{ fontSize: 13, color: '#10b981', fontWeight: 600 }}>✓ Saved</span>
                )}
                <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--text-muted)' }}>
                    {mode === 'draw' ? 'Click & drag on the feed to draw a zone' : 'Drag corners to resize · click label to rename · drag zone to move'}
                </span>
            </div>

            {/* Canvas */}
            <div style={{ position: 'relative', borderRadius: 10, overflow: 'hidden', background: '#000',
                cursor: mode === 'draw' ? 'crosshair' : 'default' }}>
                <img
                    ref={imgRef}
                    src={liveStreamUrl(cameraId)}
                    alt={`Camera ${cameraId} live`}
                    style={{ width: '100%', display: 'block', userSelect: 'none', pointerEvents: 'none' }}
                    onLoad={onImgLoad}
                />

                <svg
                    ref={svgRef}
                    viewBox={`0 0 ${natSize.w} ${natSize.h}`}
                    preserveAspectRatio="none"
                    style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%' }}
                    onMouseDown={onMouseDown}
                    onMouseMove={onMouseMove}
                    onMouseUp={onMouseUp}
                    onMouseLeave={() => { if (drawing) setDrawing(null); }}
                >
                    {/* Existing zones */}
                    {localZones.map((zone, idx) => {
                        const [x1, y1, x2, y2] = zone.bbox || [0, 0, 100, 100];
                        const w = x2 - x1;
                        const h = y2 - y1;
                        const eng = engagementZones.find((e) => e.zone_id === zone.zone_id);
                        const score = eng?.engagement_score ?? null;
                        const color = ZONE_COLORS[idx % ZONE_COLORS.length];
                        const border = ZONE_BORDERS[idx % ZONE_BORDERS.length];
                        const isEditing = editingLabel === zone.zone_id;

                        return (
                            <g key={zone.zone_id}>
                                {/* Zone fill — drag to move */}
                                <rect
                                    x={x1} y={y1} width={w} height={h}
                                    fill={color} stroke={border}
                                    strokeWidth={5} strokeDasharray="18 8"
                                    style={{ cursor: mode === 'view' ? 'move' : 'default' }}
                                    onMouseDown={mode === 'view' ? (e) => {
                                        e.stopPropagation();
                                        setDragging({ zone_id: zone.zone_id, origBbox: [...zone.bbox], startPt: svgPt(e) });
                                    } : undefined}
                                />

                                {/* Label bg + text */}
                                <rect
                                    x={x1 + 8} y={y1 + 8}
                                    width={420} height={score !== null ? 78 : 48}
                                    rx={8} fill="rgba(0,0,0,0.70)"
                                />
                                {isEditing ? (
                                    <foreignObject x={x1 + 14} y={y1 + 12} width={400} height={40}>
                                        <input
                                            autoFocus
                                            value={labelDraft}
                                            onChange={(e) => setLabelDraft(e.target.value)}
                                            onBlur={() => commitRename(zone)}
                                            onKeyDown={(e) => { if (e.key === 'Enter') commitRename(zone); if (e.key === 'Escape') setEditingLabel(null); }}
                                            style={{
                                                width: '100%', background: 'transparent',
                                                border: 'none', borderBottom: `2px solid ${border}`,
                                                color: '#fff', fontSize: 22, fontWeight: 700,
                                                outline: 'none', fontFamily: 'system-ui',
                                            }}
                                        />
                                    </foreignObject>
                                ) : (
                                    <text
                                        x={x1 + 16} y={y1 + 36}
                                        fontSize={26} fontWeight="bold" fill="#fff"
                                        style={{ fontFamily: 'system-ui', cursor: 'text' }}
                                        onDoubleClick={(e) => { e.stopPropagation(); startRename(zone); }}
                                    >
                                        {zone.zone_name}
                                        <tspan fontSize={18} fill={border} dx={10}>(dbl-click to rename)</tspan>
                                    </text>
                                )}
                                {score !== null && (
                                    <text
                                        x={x1 + 16} y={y1 + 66}
                                        fontSize={20} fill={border}
                                        style={{ fontFamily: 'system-ui' }}
                                    >
                                        {`Score: ${score.toFixed(1)}  ·  ${eng?.visit_count ?? 0} visits  ·  ${eng?.avg_dwell_time?.toFixed(1) ?? 0}s`}
                                    </text>
                                )}

                                {/* Delete button (top-right corner) */}
                                <g
                                    style={{ cursor: 'pointer' }}
                                    onClick={(e) => {
                                        e.stopPropagation();
                                        setLocalZones((zz) => zz.filter((z) => z.zone_id !== zone.zone_id));
                                        setUnsaved(true);
                                    }}
                                >
                                    <circle cx={x2 - 20} cy={y1 + 20} r={22} fill="rgba(239,68,68,0.85)" />
                                    <text x={x2 - 20} y={y1 + 27} textAnchor="middle" fontSize={26} fill="#fff"
                                        style={{ fontFamily: 'system-ui', userSelect: 'none' }}>×</text>
                                </g>

                                {/* Resize handles — corners */}
                                {mode === 'view' && handles.map((handle) => {
                                    const [hx, hy] = handlePos(x1, y1, x2, y2, handle);
                                    return (
                                        <rect
                                            key={handle}
                                            x={hx - HANDLE_SIZE / 2} y={hy - HANDLE_SIZE / 2}
                                            width={HANDLE_SIZE} height={HANDLE_SIZE}
                                            rx={4}
                                            fill={border} stroke="#fff" strokeWidth={3}
                                            style={{ cursor: handleCursor[handle] }}
                                            onMouseDown={(e) => {
                                                e.stopPropagation();
                                                setResizing({ zone_id: zone.zone_id, handle, origBbox: [...zone.bbox], startPt: svgPt(e) });
                                            }}
                                        />
                                    );
                                })}
                            </g>
                        );
                    })}

                    {/* Ghost rect while drawing */}
                    {drawing && (() => {
                        const x = Math.min(drawing.x1, drawing.x2);
                        const y = Math.min(drawing.y1, drawing.y2);
                        const w = Math.abs(drawing.x2 - drawing.x1);
                        const h = Math.abs(drawing.y2 - drawing.y1);
                        return (
                            <rect
                                x={x} y={y} width={w} height={h}
                                fill="rgba(99,102,241,0.25)"
                                stroke="#6366f1" strokeWidth={4} strokeDasharray="14 7"
                                pointerEvents="none"
                            />
                        );
                    })()}
                </svg>
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
