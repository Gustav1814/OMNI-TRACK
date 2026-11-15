/**
 * OmniTrack AI — Zone editor
 *
 * Draw analytics zones on a real frame of the camera:
 *   crowd / shelf / queue / counter -> polygon (click points, click the first
 *   point or press Enter to close), entry_line -> two points.
 * Coordinates are stored normalized (0..1) so they survive resolution changes.
 * Admins can create, edit (name, capacity, lane, direction, shape) and delete;
 * everyone else sees the zones read-only.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
    ArrowLeftRight, Check, Pencil, RefreshCw, Trash2, Undo2, X,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { zonesAPI } from '../../services/api';
import useLivePoll from '../../hooks/useLivePoll';
import { useTokens } from '../../lib/tokens';

const TYPE_LABELS = {
    crowd: 'Crowd area',
    shelf: 'Shelf / display',
    queue: 'Queue',
    counter: 'Counter / till',
    entry_line: 'Entry line',
    restricted: 'Restricted area',
};

/** Fixed type order -> chart series slot, so a zone type keeps its colour everywhere.
 *  Restricted areas wear the danger token instead of a series colour. */
const TYPE_ORDER = Object.keys(TYPE_LABELS).filter((k) => k !== 'restricted');
const typeSlot = (type) => Math.max(0, TYPE_ORDER.indexOf(type));
const swatchClass = (type) => (type === 'restricted' ? 'swatch swatch-danger' : `swatch swatch-${typeSlot(type) + 1}`);

const EMPTY_FORM = { name: '', zone_type: 'crowd', capacity: '', lane: '', in_sign: 1 };

const clamp01 = (v) => Math.min(1, Math.max(0, v));
const round4 = (v) => Math.round(v * 10000) / 10000;
const errText = (e) => {
    const detail = e?.response?.data?.detail;
    if (Array.isArray(detail)) return detail.map((d) => String(d.msg || d).replace(/^Value error, /, '')).join('; ');
    return detail || e?.message || 'Request failed';
};

function polygonArea(pts) {
    let a = 0;
    for (let i = 0; i < pts.length; i += 1) {
        const [x1, y1] = pts[i];
        const [x2, y2] = pts[(i + 1) % pts.length];
        a += x1 * y2 - x2 * y1;
    }
    return Math.abs(a) / 2;
}

/** Drop trailing points that land on the previous one (the two clicks of a double-click). */
function dedupeTail(pts) {
    const out = [...pts];
    while (out.length >= 2) {
        const [ax, ay] = out[out.length - 1];
        const [bx, by] = out[out.length - 2];
        if (Math.hypot(ax - bx, ay - by) > 0.006) break;
        out.pop();
    }
    return out;
}

function centroid(pts) {
    const n = pts.length || 1;
    return [pts.reduce((s, p) => s + p[0], 0) / n, pts.reduce((s, p) => s + p[1], 0) / n];
}

/** Arrow pointing to the "inside" side of an entry line (matches backend sign). */
function lineArrow(points, inSign) {
    const [[ax, ay], [bx, by]] = points;
    const dx = bx - ax;
    const dy = by - ay;
    const len = Math.hypot(dx, dy) || 1;
    const nx = (-dy / len) * inSign;
    const ny = (dx / len) * inSign;
    const mx = (ax + bx) / 2;
    const my = (ay + by) / 2;
    return { x1: mx - nx * 0.04, y1: my - ny * 0.04, x2: mx + nx * 0.06, y2: my + ny * 0.06 };
}

export function useSnapshot(cameraId) {
    const [url, setUrl] = useState(null);
    const [error, setError] = useState(null);
    const [loading, setLoading] = useState(false);
    const [source, setSource] = useState(null);
    const [takenAt, setTakenAt] = useState(null);
    const urlRef = useRef(null);

    const load = useCallback(async () => {
        if (cameraId == null || cameraId === '') return;
        setLoading(true);
        setError(null);
        try {
            const res = await zonesAPI.snapshot(cameraId, { clean: true });
            const next = URL.createObjectURL(res.data);
            if (urlRef.current) URL.revokeObjectURL(urlRef.current);
            urlRef.current = next;
            setUrl(next);
            setSource(res.headers?.['x-snapshot-source'] || null);
            setTakenAt(new Date());
        } catch (e) {
            let msg = 'No frame available for this camera.';
            try {
                const text = await e?.response?.data?.text?.();
                const parsed = text ? JSON.parse(text) : null;
                if (parsed?.detail) msg = parsed.detail;
            } catch { /* keep default */ }
            setError(msg);
            if (urlRef.current) URL.revokeObjectURL(urlRef.current);
            urlRef.current = null;
            setUrl(null);
        } finally {
            setLoading(false);
        }
    }, [cameraId]);

    useEffect(() => { load(); }, [load]);
    useEffect(() => () => { if (urlRef.current) URL.revokeObjectURL(urlRef.current); }, []);
    return { url, error, loading, source, takenAt, reload: load };
}

export default function ZoneEditor({ cameras = [], canEdit = false }) {
    const [cameraId, setCameraId] = useState('');
    const [zones, setZones] = useState([]);
    const [listError, setListError] = useState(null);
    const [selectedId, setSelectedId] = useState(null);
    const [mode, setMode] = useState('idle'); // idle | draw | reshape (adjust points + fill the form)
    const [draft, setDraft] = useState([]); // points being drawn / reshaped
    const [form, setForm] = useState(EMPTY_FORM);
    const [busy, setBusy] = useState(false);
    const [message, setMessage] = useState(null);
    const [aspect, setAspect] = useState(16 / 9);
    const [hover, setHover] = useState(null);
    const svgRef = useRef(null);
    const dragRef = useRef(null);   // { kind: 'vertex', i } | { kind: 'move', start, orig }
    const hoverFrame = useRef(0);
    const t = useTokens();
    const navigate = useNavigate();
    const zoneColor = (type) => (type === 'restricted' ? t.danger : t.series[typeSlot(type)] || t.neutral);

    useEffect(() => {
        if (cameraId === '' && cameras.length) setCameraId(String(cameras[0].id));
    }, [cameras, cameraId]);
    const selectedCamera = cameras.find((c) => String(c.id) === String(cameraId));

    const snap = useSnapshot(cameraId);

    const loadZones = useCallback(async () => {
        if (!cameraId) { setZones([]); return; }
        try {
            const res = await zonesAPI.list(Number(cameraId));
            setZones(Array.isArray(res.data?.zones) ? res.data.zones : (Array.isArray(res.data) ? res.data : []));
            setListError(null);
        } catch (e) {
            setListError(errText(e));
        }
    }, [cameraId]);

    useEffect(() => {
        loadZones();
        setSelectedId(null);
        setMode('idle');
        setDraft([]);
    }, [loadZones]);

    const { data: live } = useLivePoll(() => zonesAPI.live(), { intervalMs: 3000, enabled: Boolean(cameraId) });
    const liveByZone = useMemo(() => {
        const out = {};
        const cam = (live?.cameras || []).find((c) => String(c.camera_id) === String(cameraId));
        if (cam && cam.status === 'ok') for (const z of cam.zones || []) out[z.id] = z;
        return out;
    }, [live, cameraId]);

    const selected = zones.find((z) => z.id === selectedId) || null;
    const isLine = form.zone_type === 'entry_line';
    const minPts = isLine ? 2 : 3;

    // ------------------------------------------------------------ geometry input
    const toNorm = (evt) => {
        const rect = svgRef.current.getBoundingClientRect();
        return [round4(clamp01((evt.clientX - rect.left) / rect.width)), round4(clamp01((evt.clientY - rect.top) / rect.height))];
    };

    const onCanvasClick = (evt) => {
        if (mode !== 'draw' || !svgRef.current) return;
        const pt = toNorm(evt);
        if (isLine) {
            setDraft((prev) => (prev.length >= 2 ? [pt] : [...prev, pt]));
            return;
        }
        if (draft.length >= 3) {
            const [fx, fy] = draft[0];
            if (Math.hypot(fx - pt[0], (fy - pt[1]) / aspect) < 0.02) { finishDraw(); return; }
        }
        if (draft.length < 64) setDraft((prev) => [...prev, pt]);
    };

    const onPointerMove = (evt) => {
        if (!svgRef.current) return;
        const drag = dragRef.current;
        if (drag) {
            const pt = toNorm(evt);
            if (drag.kind === 'vertex') {
                setDraft((prev) => prev.map((p, i) => (i === drag.i ? pt : p)));
            } else {
                // Move the whole shape, kept inside the frame.
                const xs = drag.orig.map((p) => p[0]);
                const ys = drag.orig.map((p) => p[1]);
                const dx = Math.min(1 - Math.max(...xs), Math.max(-Math.min(...xs), pt[0] - drag.start[0]));
                const dy = Math.min(1 - Math.max(...ys), Math.max(-Math.min(...ys), pt[1] - drag.start[1]));
                setDraft(drag.orig.map(([x, y]) => [round4(x + dx), round4(y + dy)]));
            }
            return;
        }
        if (mode !== 'draw') return;
        // One hover update per animation frame keeps the rubber band smooth.
        const pt = toNorm(evt);
        cancelAnimationFrame(hoverFrame.current);
        hoverFrame.current = requestAnimationFrame(() => setHover(pt));
    };
    useEffect(() => () => cancelAnimationFrame(hoverFrame.current), []);

    const endDrag = () => { dragRef.current = null; };

    const startDrag = (evt, drag) => {
        if (mode !== 'reshape') return;
        evt.stopPropagation();
        evt.currentTarget.setPointerCapture?.(evt.pointerId);
        dragRef.current = drag;
    };

    const finishDraw = () => {
        const cleaned = dedupeTail(draft);
        if (cleaned.length !== draft.length) setDraft(cleaned);
        if (cleaned.length < minPts) return;
        cancelAnimationFrame(hoverFrame.current);
        setHover(null);
        setMode('reshape');
    };

    const insertPoint = (evt, afterIdx) => {
        if (mode !== 'reshape' || draft.length >= 64) return;
        const pt = toNorm(evt);
        setDraft((prev) => [...prev.slice(0, afterIdx + 1), pt, ...prev.slice(afterIdx + 1)]);
        startDrag(evt, { kind: 'vertex', i: afterIdx + 1 });
    };

    const removePoint = (evt, idx) => {
        if (mode !== 'reshape') return;   // while drawing, a double-click closes the shape
        evt.stopPropagation();
        if (draft.length <= minPts) return;
        setDraft((prev) => prev.filter((_, i) => i !== idx));
    };

    useEffect(() => {
        const onKey = (e) => {
            if (mode !== 'draw' && mode !== 'reshape') return;
            const tag = e.target?.tagName;
            if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
            if (e.key === 'Escape') cancel();
            if (e.key === 'Enter' && mode === 'draw') finishDraw();
            if ((e.key === 'Backspace' || (e.key === 'z' && (e.ctrlKey || e.metaKey))) && mode === 'draw') {
                e.preventDefault();
                setDraft((prev) => prev.slice(0, -1));
            }
        };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    });

    // ------------------------------------------------------------ actions
    const startNew = () => {
        setSelectedId(null);
        setForm({ ...EMPTY_FORM, zone_type: form.zone_type || 'crowd' });
        setDraft([]);
        setMessage(null);
        setMode('draw');
    };

    const startEdit = (zone) => {
        setSelectedId(zone.id);
        setForm({
            name: zone.name,
            zone_type: zone.zone_type,
            capacity: zone.capacity ?? '',
            lane: zone.lane ?? '',
            in_sign: zone.config?.in_sign === -1 ? -1 : 1,
        });
        setDraft(zone.points.map((p) => [...p]));
        setMessage(null);
        setMode('reshape');
    };

    const cancel = () => {
        setMode('idle');
        setDraft([]);
        setHover(null);
        dragRef.current = null;
    };

    const body = () => {
        const out = {
            name: form.name.trim(),
            zone_type: form.zone_type,
            points: draft,
        };
        if (form.zone_type === 'crowd') out.capacity = Number(form.capacity);
        if (['queue', 'counter'].includes(form.zone_type)) out.lane = form.lane.trim() || null;
        if (form.zone_type === 'entry_line') out.in_sign = form.in_sign;
        return out;
    };

    const validate = () => {
        if (!form.name.trim()) return 'Give the zone a name.';
        if (draft.length < minPts) return isLine ? 'An entry line needs 2 points.' : 'A zone needs at least 3 points.';
        if (!isLine && polygonArea(draft) < 1e-4) return 'The polygon is too small or has no area.';
        if (form.zone_type === 'crowd' && !(Number(form.capacity) >= 1)) return 'Crowd zones need a capacity (people at 100 %).';
        return null;
    };

    const save = async () => {
        const problem = validate();
        if (problem) { setMessage({ tone: 'danger', text: problem }); return; }
        setBusy(true);
        try {
            if (selected) {
                await zonesAPI.update(selected.id, body());
                setMessage({ tone: 'success', text: `Saved "${form.name.trim()}".` });
            } else {
                await zonesAPI.create({ ...body(), camera_id: Number(cameraId) });
                setMessage({ tone: 'success', text: `Created "${form.name.trim()}".` });
            }
            cancel();
            setSelectedId(null);
            await loadZones();
        } catch (e) {
            setMessage({ tone: 'danger', text: errText(e) });
        } finally {
            setBusy(false);
        }
    };

    const remove = async (zone) => {
        if (!window.confirm(`Delete zone "${zone.name}"? Its recorded events are deleted too.`)) return;
        setBusy(true);
        try {
            await zonesAPI.delete(zone.id);
            if (selectedId === zone.id) { cancel(); setSelectedId(null); }
            setMessage({ tone: 'success', text: `Deleted "${zone.name}".` });
            await loadZones();
        } catch (e) {
            setMessage({ tone: 'danger', text: errText(e) });
        } finally {
            setBusy(false);
        }
    };

    // ------------------------------------------------------------ render helpers
    const editingGeometry = mode === 'draw' || mode === 'reshape';
    const draftColor = zoneColor(form.zone_type);

    const renderZone = (z) => {
        if (editingGeometry && z.id === selectedId) return null;
        const color = zoneColor(z.zone_type);
        const lz = liveByZone[z.id];
        const active = z.id === selectedId;
        const alarmed = Boolean(lz?.alert_open || (z.zone_type === 'restricted' && lz?.dwell_alerts_open));
        let label = z.name;
        if (lz) {
            if (z.zone_type === 'entry_line') label += ` · in ${lz.entries ?? 0} / out ${lz.exits ?? 0}`;
            else if (z.zone_type === 'crowd' && lz.pct != null) label += ` · ${lz.count} (${Math.round(lz.pct * 100)} %)`;
            else label += ` · ${lz.count ?? 0}`;
        }
        if (z.zone_type === 'entry_line' && z.points.length === 2) {
            const arr = lineArrow(z.points, z.config?.in_sign === -1 ? -1 : 1);
            return (
                <g key={z.id} onClick={(e) => { if (mode !== 'idle') return; e.stopPropagation(); setSelectedId(z.id); }} className={mode === 'idle' ? 'zone-shape is-pickable' : 'zone-shape'}>
                    <line x1={z.points[0][0]} y1={z.points[0][1]} x2={z.points[1][0]} y2={z.points[1][1]} stroke={color} strokeWidth={active ? 4 : 3} vectorEffect="non-scaling-stroke" />
                    <line {...arr} stroke={color} strokeWidth={2} vectorEffect="non-scaling-stroke" markerEnd="url(#zone-arrow)" />
                    <text x={z.points[0][0]} y={Math.max(0.03, z.points[0][1] - 0.015)} className="zone-label" fontSize={0.028} strokeWidth={0.006}>{label}</text>
                </g>
            );
        }
        const [cx, cy] = centroid(z.points);
        return (
            <g key={z.id} onClick={(e) => { if (mode !== 'idle') return; e.stopPropagation(); setSelectedId(z.id); }} className={mode === 'idle' ? 'zone-shape is-pickable' : 'zone-shape'}>
                <polygon
                    points={z.points.map((p) => p.join(',')).join(' ')}
                    fill={color}
                    fillOpacity={active ? 0.32 : (alarmed ? 0.35 : 0.16)}
                    stroke={alarmed ? t.danger : color}
                    strokeWidth={active ? 3 : 2}
                    vectorEffect="non-scaling-stroke"
                />
                <text x={cx} y={cy} className="zone-label" fontSize={0.028} strokeWidth={0.006} textAnchor="middle">{label}</text>
            </g>
        );
    };

    const renderDraft = () => {
        if (!editingGeometry || draft.length === 0) return null;
        const pts = mode === 'draw' && hover && !(isLine && draft.length >= 2) ? [...draft, hover] : draft;
        return (
            <g>
                {isLine ? (
                    pts.length >= 2 && (
                        <>
                            <line
                                x1={pts[0][0]} y1={pts[0][1]} x2={pts[1][0]} y2={pts[1][1]}
                                stroke={draftColor} strokeWidth={3} vectorEffect="non-scaling-stroke"
                                strokeDasharray={mode === 'draw' ? '6 4' : undefined}
                                className={mode === 'reshape' ? 'zone-draft is-movable' : 'zone-draft'}
                                onPointerDown={(e) => startDrag(e, { kind: 'move', start: toNorm(e), orig: draft.map((q) => [...q]) })}
                            />
                            <line {...lineArrow(pts.slice(0, 2), form.in_sign)} stroke={draftColor} strokeWidth={2} vectorEffect="non-scaling-stroke" markerEnd="url(#zone-arrow)" />
                        </>
                    )
                ) : (
                    <polygon
                        points={pts.map((p) => p.join(',')).join(' ')}
                        fill={draftColor}
                        fillOpacity={0.2}
                        stroke={draftColor}
                        strokeWidth={2}
                        strokeDasharray={mode === 'draw' ? '6 4' : undefined}
                        vectorEffect="non-scaling-stroke"
                        className={mode === 'reshape' ? 'zone-draft is-movable' : 'zone-draft'}
                        onPointerDown={(e) => startDrag(e, { kind: 'move', start: toNorm(e), orig: draft.map((q) => [...q]) })}
                    />
                )}
                {mode === 'reshape' && !isLine && draft.map((p, i) => {
                    const q = draft[(i + 1) % draft.length];
                    return (
                        <ellipse
                            key={`m${i}`}
                            cx={(p[0] + q[0]) / 2}
                            cy={(p[1] + q[1]) / 2}
                            rx={0.006 / aspect}
                            ry={0.006}
                            className="zone-midpoint"
                            vectorEffect="non-scaling-stroke"
                            onPointerDown={(e) => insertPoint(e, i)}
                        >
                            <title>Drag to add a point</title>
                        </ellipse>
                    );
                })}
                {draft.map((p, i) => (
                    <g key={i}>
                        <ellipse
                            cx={p[0]}
                            cy={p[1]}
                            rx={0.009 / aspect}
                            ry={0.009}
                            className={`zone-vertex${i === 0 ? ' zone-vertex--first' : ''}`}
                            fill={i === 0 ? undefined : draftColor}
                            strokeWidth={1}
                            vectorEffect="non-scaling-stroke"
                        />
                        {/* Larger invisible hit area so points are easy to grab. */}
                        <ellipse
                            cx={p[0]}
                            cy={p[1]}
                            rx={0.024 / aspect}
                            ry={0.024}
                            className={`zone-hit${mode === 'reshape' ? ' is-grab' : ''}`}
                            onPointerDown={(e) => startDrag(e, { kind: 'vertex', i })}
                            onDoubleClick={(e) => removePoint(e, i)}
                        />
                    </g>
                ))}
            </g>
        );
    };

    const typeHint = {
        crowd: 'Occupancy vs capacity; levels at 10 / 30 / 60 / 90 %.',
        shelf: 'Visits (3 s or more inside), dwell time, engaged visits (over 10 s).',
        queue: 'Queue length and wait time; pair with a counter by giving both the same lane.',
        counter: 'Service time and throughput for the lane.',
        entry_line: 'Footfall: crossings in the arrow direction count as entries.',
        restricted: 'Raises a high-priority alert, with a snapshot and clip, when someone is inside for 2 s.',
    }[form.zone_type];

    if (!cameras.length) {
        return (
            <div className="empty-state">
                <span className="empty-state-title">No cameras registered</span>
                Add a video feed under Video feeds, then draw its zones here.
                <button type="button" className="btn btn-secondary btn-sm" onClick={() => navigate('/detection')}>Open video feeds</button>
            </div>
        );
    }

    return (
        <div className="zone-editor">
            <div>
                <div className="zone-toolbar">
                    <label className="sr-only" htmlFor="zone-camera">Camera</label>
                    <select id="zone-camera" className="form-select" value={cameraId} onChange={(e) => setCameraId(e.target.value)} disabled={editingGeometry}>
                        {cameras.map((c) => <option key={c.id} value={c.id}>{c.name || `Camera ${c.id}`}</option>)}
                    </select>
                    <button type="button" className="btn btn-secondary btn-xs" onClick={snap.reload} disabled={snap.loading}>
                        <RefreshCw size={14} strokeWidth={1.75} /> {snap.loading ? 'Loading frame…' : 'Refresh frame'}
                    </button>
                </div>

                <div className={`zone-canvas vf vf--on-media ${mode === 'draw' ? 'drawing' : ''}`} style={{ aspectRatio: String(aspect) }}>
                    {snap.url ? (
                        <img
                            src={snap.url}
                            alt="Camera frame"
                            draggable={false}
                            onLoad={(e) => {
                                const { naturalWidth: w, naturalHeight: h } = e.currentTarget;
                                if (w && h) setAspect(w / h);
                            }}
                        />
                    ) : (
                        <div className="zone-canvas-msg">
                            {snap.loading ? 'Loading a frame from the camera…' : `${snap.error || 'No frame yet.'} Zones can still be drawn; start the feed to see the scene.`}
                        </div>
                    )}
                    <svg
                        ref={svgRef}
                        viewBox="0 0 1 1"
                        preserveAspectRatio="none"
                        onClick={onCanvasClick}
                        onPointerMove={onPointerMove}
                        onPointerUp={endDrag}
                        onPointerCancel={endDrag}
                        onPointerLeave={() => { if (!dragRef.current) setHover(null); }}
                        onDoubleClick={() => mode === 'draw' && finishDraw()}
                    >
                        <defs>
                            <marker id="zone-arrow" viewBox="0 0 10 10" refX="6" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
                                <path d="M 0 0 L 10 5 L 0 10 z" className="zone-arrow-head" />
                            </marker>
                        </defs>
                        {zones.map(renderZone)}
                        {renderDraft()}
                    </svg>
                    <div className="osd-strip zone-osd" aria-hidden="true">
                        <span>
                            <i className={`live-dot ${snap.source === 'live' ? 'is-live' : ''}`} />
                            {snap.source === 'live' ? 'Live frame' : snap.url ? 'Frame from source' : 'No frame'}
                        </span>
                        <strong>{selectedCamera?.name || (cameraId ? `Camera ${cameraId}` : '')}</strong>
                        <span>{snap.takenAt ? snap.takenAt.toLocaleTimeString([], { hour12: false }) : ''}</span>
                    </div>
                </div>

                <p className="zone-help">
                    {mode === 'draw'
                        ? (isLine
                            ? 'Click two points for the line. Esc cancels.'
                            : `Click to add points (${draft.length} so far). Click the first point, double-click or press Enter to close. Backspace removes the last point, Esc cancels.`)
                        : mode === 'reshape'
                            ? (isLine
                                ? 'Drag the ends or the line to adjust, then Save.'
                                : 'Drag points to reshape, drag a small dot on an edge to add a point, double-click a point to remove it, or drag the shape to move it. Then Save.')
                            : 'People are placed by the bottom-centre of their box (where they stand), so draw zones on the floor.'}
                </p>
            </div>

            <div className="zone-side">
                {message ? <div className={`alert-banner ${message.tone}`} role="status">{message.text}</div> : null}
                {listError ? <div className="alert-banner danger" role="alert">{listError}</div> : null}

                {canEdit && (editingGeometry ? (
                    <div className="zone-form">
                        <div className="form-group">
                        <label className="form-label" htmlFor="zone-type">Type</label>
                        <select
                            id="zone-type"
                            className="form-select"
                            value={form.zone_type}
                            disabled={Boolean(selected)}
                            onChange={(e) => {
                                const t = e.target.value;
                                setForm((f) => ({ ...f, zone_type: t }));
                                if ((t === 'entry_line') !== isLine) { setDraft([]); setMode('draw'); }
                            }}
                        >
                            {Object.entries(TYPE_LABELS).map(([t, l]) => <option key={t} value={t}>{l}</option>)}
                        </select>
                        <span className="form-hint">{typeHint}</span>
                        </div>
                        <div className="form-group">
                        <label className="form-label" htmlFor="zone-name">Name</label>
                        <input id="zone-name" className="form-input" value={form.name} maxLength={100} placeholder="e.g. Front aisle" onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
                        </div>
                        {form.zone_type === 'crowd' && (
                            <div className="form-group">
                                <label className="form-label" htmlFor="zone-cap">Capacity (people at 100 %)</label>
                                <input id="zone-cap" className="form-input" type="number" min={1} max={10000} value={form.capacity} onChange={(e) => setForm((f) => ({ ...f, capacity: e.target.value }))} />
                            </div>
                        )}
                        {['queue', 'counter'].includes(form.zone_type) && (
                            <div className="form-group">
                                <label className="form-label" htmlFor="zone-lane">Lane</label>
                                <input id="zone-lane" className="form-input" value={form.lane} maxLength={50} placeholder="default" onChange={(e) => setForm((f) => ({ ...f, lane: e.target.value }))} />
                                <span className="form-hint">Give a queue and its counter the same lane.</span>
                            </div>
                        )}
                        {form.zone_type === 'entry_line' && (
                            <div><button type="button" className="btn btn-secondary btn-sm" onClick={() => setForm((f) => ({ ...f, in_sign: -f.in_sign }))}>
                                <ArrowLeftRight size={14} strokeWidth={1.75} /> Flip entry direction
                            </button></div>
                        )}
                        <div className="adm-actions">
                            {mode === 'draw' && !isLine && (
                                <>
                                    <button type="button" className="btn btn-secondary btn-sm" onClick={() => setDraft((p) => p.slice(0, -1))} disabled={!draft.length}>
                                        <Undo2 size={14} strokeWidth={1.75} /> Undo point
                                    </button>
                                    <button type="button" className="btn btn-secondary btn-sm" onClick={finishDraw} disabled={draft.length < minPts}>
                                        <Check size={14} strokeWidth={1.75} /> Close shape
                                    </button>
                                </>
                            )}
                            {selected && mode !== 'draw' && (
                                <button type="button" className="btn btn-secondary btn-sm" onClick={() => { setDraft([]); setMode('draw'); }}>
                                    <Pencil size={14} strokeWidth={1.75} /> Redraw
                                </button>
                            )}
                            <button type="button" className="btn btn-primary btn-sm" onClick={save} disabled={busy || draft.length < minPts}>
                                <Check size={14} strokeWidth={1.75} /> {selected ? 'Save changes' : 'Save zone'}
                            </button>
                            <button type="button" className="btn btn-ghost btn-sm" onClick={() => { cancel(); setSelectedId(null); }}>
                                <X size={14} strokeWidth={1.75} /> Cancel
                            </button>
                        </div>
                    </div>
                ) : (
                    <div className="zone-new">
                        <label className="sr-only" htmlFor="zone-new-type">Zone type</label>
                        <select id="zone-new-type" className="form-select" value={form.zone_type} onChange={(e) => setForm((f) => ({ ...f, zone_type: e.target.value }))}>
                            {Object.entries(TYPE_LABELS).map(([t, l]) => <option key={t} value={t}>{l}</option>)}
                        </select>
                        <button type="button" className="btn btn-primary btn-sm" onClick={startNew} disabled={!cameraId}>
                            <Pencil size={14} strokeWidth={1.75} /> Draw zone
                        </button>
                    </div>
                ))}
                {!canEdit && <p className="zone-help">Only admins can add or change zones.</p>}

                <div>
                <div className="zone-list-head">
                    <span className="osd">Zones on this camera</span>
                    <span className="count-badge">{zones.length}</span>
                </div>
                {zones.length === 0 ? (
                    <div className="empty-state">
                        <span className="empty-state-title">No zones yet</span>
                        Pick a type and draw on the frame. Zone analytics only run where zones exist.
                    </div>
                ) : (
                    <div className="zone-list">
                        {zones.map((z) => {
                            const lz = liveByZone[z.id];
                            return (
                                <div
                                    key={z.id}
                                    className={`zone-list-row ${z.id === selectedId ? 'active' : ''}`}
                                    onClick={() => mode === 'idle' && setSelectedId(z.id === selectedId ? null : z.id)}
                                >
                                    <div>
                                        <div className="zone-list-name">
                                            <span className={swatchClass(z.zone_type)} aria-hidden="true" />
                                            <span>{z.name}</span>
                                        </div>
                                        <div className="zone-sub">
                                            {TYPE_LABELS[z.zone_type]}
                                            {z.capacity ? ` · capacity ${z.capacity}` : ''}
                                            {z.lane ? ` · lane ${z.lane}` : ''}
                                            {lz ? (z.zone_type === 'entry_line' ? ` · in ${lz.entries ?? 0}, out ${lz.exits ?? 0}` : ` · now ${lz.count ?? 0}`) : ''}
                                        </div>
                                    </div>
                                    {canEdit && mode === 'idle' && (
                                        <div className="adm-table-actions">
                                            <button type="button" className="btn btn-ghost btn-xs btn-icon" title="Edit" aria-label={`Edit ${z.name}`} onClick={(e) => { e.stopPropagation(); startEdit(z); }}>
                                                <Pencil size={14} strokeWidth={1.75} />
                                            </button>
                                            <button type="button" className="btn btn-ghost btn-xs btn-icon" title="Delete" aria-label={`Delete ${z.name}`} disabled={busy} onClick={(e) => { e.stopPropagation(); remove(z); }}>
                                                <Trash2 size={14} strokeWidth={1.75} />
                                            </button>
                                        </div>
                                    )}
                                </div>
                            );
                        })}
                    </div>
                )}
                </div>
            </div>
        </div>
    );
}
