/**
 * OmniTrack AI — floor calibration
 *
 * Click at least 4 floor spots in a camera frame and type where each one is on the
 * store floor plan (metres, one shared origin for all cameras). The backend fits the
 * image -> floor homography; with 2+ calibrated overlapping cameras, a person seen by
 * both is fused into one global ID, so occupancy counts them once.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { RefreshCw, Trash2, X } from 'lucide-react';
import { reidAPI } from '../../services/api';
import { useSnapshot } from './ZoneEditor';

const MIN_POINTS = 4;
const MAX_POINTS = 12;
const RMSE_WARN_M = 0.3; // fusion pairs people within 0.75 m, so a fit worse than this undermines it

const num = (v) => (v === '' || v == null ? NaN : Number(v));
const fmt = (v) => (Number.isFinite(v) ? String(v) : '');

function apiError(e, fallback) {
    const d = e?.response?.data?.detail;
    if (typeof d === 'string') return d;
    if (Array.isArray(d) && d[0]?.msg) return d[0].msg.replace(/^Value error, /, '');
    return fallback;
}

export default function FloorCalibration({ cameras = [], canEdit = false }) {
    const [cameraId, setCameraId] = useState('');
    const [saved, setSaved] = useState({});
    const [fusion, setFusion] = useState(null);
    const [points, setPoints] = useState([]);
    const [frameWh, setFrameWh] = useState(null);
    const [aspect, setAspect] = useState(16 / 9);
    const [busy, setBusy] = useState(false);
    const [message, setMessage] = useState(null);
    const snap = useSnapshot(cameraId);

    useEffect(() => {
        if (cameraId === '' && cameras.length) setCameraId(String(cameras[0].id));
    }, [cameras, cameraId]);

    const loadSaved = useCallback(async () => {
        try {
            const res = await reidAPI.calibrations();
            const rows = res.data?.calibrations || [];
            setSaved(Object.fromEntries(rows.map((r) => [String(r.camera_id), r])));
            setFusion(res.data?.fusion || null);
        } catch (e) {
            setMessage({ tone: 'danger', text: apiError(e, 'Could not load floor calibrations.') });
        }
    }, []);
    useEffect(() => { loadSaved(); }, [loadSaved]);

    const current = saved[String(cameraId)];
    const resetToSaved = useCallback(() => {
        setPoints((current?.points || []).map((p) => ({ u: p.image[0], v: p.image[1], x: fmt(p.floor[0]), y: fmt(p.floor[1]) })));
    }, [current]);
    useEffect(() => { resetToSaved(); setMessage(null); }, [cameraId, resetToSaved]);

    const savedPoints = useMemo(() => JSON.stringify((current?.points || []).map((p) => [p.image, p.floor])), [current]);
    const dirty = JSON.stringify(points.map((p) => [[p.u, p.v], [num(p.x), num(p.y)]])) !== savedPoints;
    const complete = points.length >= MIN_POINTS && points.every((p) => Number.isFinite(num(p.x)) && Number.isFinite(num(p.y)));

    const onCanvasClick = (e) => {
        if (!canEdit || busy || points.length >= MAX_POINTS) return;
        const r = e.currentTarget.getBoundingClientRect();
        const u = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
        const v = Math.min(1, Math.max(0, (e.clientY - r.top) / r.height));
        setPoints((ps) => [...ps, { u, v, x: '', y: '' }]);
    };
    const setCoord = (i, key, value) => setPoints((ps) => ps.map((p, j) => (j === i ? { ...p, [key]: value } : p)));
    const removePoint = (i) => setPoints((ps) => ps.filter((_, j) => j !== i));

    const save = async () => {
        if (!complete || !frameWh) return;
        setBusy(true);
        setMessage(null);
        try {
            const res = await reidAPI.saveCalibration(cameraId, {
                points: points.map((p) => ({ image: [p.u, p.v], floor: [num(p.x), num(p.y)] })),
                frame_wh: frameWh,
            });
            const rmse = res.data?.rmse_m;
            setMessage(rmse == null
                ? { tone: 'info', text: 'Saved. Four points always fit exactly, so add a fifth to measure how accurate the calibration is.' }
                : rmse > RMSE_WARN_M
                    ? { tone: 'warning', text: `Saved, but the points disagree by ${rmse.toFixed(2)} m on average. Check the measurements; fusion needs better than ${RMSE_WARN_M} m.` }
                    : { tone: 'success', text: `Saved. The points agree to within ${rmse.toFixed(2)} m on average.` });
            await loadSaved();
        } catch (e) {
            setMessage({ tone: 'danger', text: apiError(e, 'Could not save the calibration.') });
        } finally {
            setBusy(false);
        }
    };

    const remove = async () => {
        if (!window.confirm('Remove the floor calibration of this camera? Its points are deleted and fusion stops using it.')) return;
        setBusy(true);
        setMessage(null);
        try {
            await reidAPI.deleteCalibration(cameraId);
            setMessage({ tone: 'info', text: 'Calibration removed for this camera.' });
            await loadSaved();
        } catch (e) {
            setMessage({ tone: 'danger', text: apiError(e, 'Could not remove the calibration.') });
        } finally {
            setBusy(false);
        }
    };

    if (!cameras.length) {
        return (
            <div className="empty-state">
                <span className="empty-state-title">No cameras registered</span>
                Add a video feed first, then calibrate its floor here.
            </div>
        );
    }

    return (
        <div className="zone-editor">
            <div>
                <div className="zone-toolbar">
                    <label className="sr-only" htmlFor="calib-camera">Camera</label>
                    <select id="calib-camera" className="form-select" value={cameraId} onChange={(e) => setCameraId(e.target.value)} disabled={busy}>
                        {cameras.map((c) => (
                            <option key={c.id} value={c.id}>
                                {(c.name || `Camera ${c.id}`) + (saved[String(c.id)] ? ' · calibrated' : '')}
                            </option>
                        ))}
                    </select>
                    <button type="button" className="btn btn-secondary btn-xs" onClick={snap.reload} disabled={snap.loading}>
                        <RefreshCw size={14} strokeWidth={1.75} /> {snap.loading ? 'Loading frame…' : 'Refresh frame'}
                    </button>
                </div>

                <div
                    className={`zone-canvas vf vf--on-media ${canEdit ? 'drawing' : ''}`}
                    style={{ aspectRatio: String(aspect) }}
                    onClick={onCanvasClick}
                    role={canEdit ? 'button' : undefined}
                    aria-label={canEdit ? 'Camera frame: click a floor spot to add a calibration point' : 'Camera frame'}
                >
                    {snap.url ? (
                        <img
                            src={snap.url}
                            alt=""
                            draggable={false}
                            onLoad={(e) => {
                                const { naturalWidth: w, naturalHeight: h } = e.currentTarget;
                                if (w && h) { setAspect(w / h); setFrameWh([w, h]); }
                            }}
                        />
                    ) : (
                        <div className="zone-canvas-msg">
                            {snap.loading ? 'Loading a frame from the camera…' : `${snap.error || 'No frame yet.'} Start the feed to calibrate it.`}
                        </div>
                    )}
                    {points.map((p, i) => (
                        <span key={i} className="calib-pin" style={{ left: `${p.u * 100}%`, top: `${p.v * 100}%` }} aria-hidden="true">
                            {i + 1}
                        </span>
                    ))}
                </div>

                <p className="zone-help">
                    Pick spots on the floor you can measure, such as tile corners or the foot of a shelf, spread across the view.
                    Click each one, then type where it is in metres from one fixed corner of the store. Overlapping cameras must
                    share that corner, so the same spot gets the same position in every view.
                </p>
            </div>

            <div className="zone-side">
                {message ? <div className={`alert-banner ${message.tone}`} role="status">{message.text}</div> : null}

                <dl className="adm-kv">
                    <dt>This camera</dt>
                    <dd>
                        {current ? (
                            <>
                                <span className="pill pill-success">Calibrated</span>
                                <span className="tabular calib-meta">
                                    {current.points ? `${current.points.length} points` : 'Matrix'}
                                    {current.rmse_m != null ? ` · ±${current.rmse_m.toFixed(2)} m` : ''}
                                </span>
                            </>
                        ) : <span className="pill pill-neutral">Not calibrated</span>}
                    </dd>
                    <dt>Cross-camera fusion</dt>
                    <dd>{fusion ? (fusion.startsWith('on') ? 'On' : 'Off, needs 2 or more calibrated cameras') : '—'}</dd>
                </dl>

                <div>
                    <div className="zone-list-head">
                        <span className="form-label">Points</span>
                        <span className="count-badge">{points.length} / {MIN_POINTS}+</span>
                    </div>
                    {points.length === 0 ? (
                        <p className="zone-help">
                            {canEdit ? `Click the frame to place point 1. You need at least ${MIN_POINTS}; 5 or more also measure the accuracy.` : 'No points saved for this camera.'}
                        </p>
                    ) : (
                        <ol className="calib-list">
                            {points.map((p, i) => (
                                <li key={i} className="calib-row">
                                    <span className="calib-index" aria-hidden="true">{i + 1}</span>
                                    <label className="sr-only" htmlFor={`calib-x-${i}`}>Point {i + 1} x in metres</label>
                                    <input
                                        id={`calib-x-${i}`} className="form-input" inputMode="decimal" placeholder="x m"
                                        value={p.x} disabled={!canEdit || busy} onChange={(e) => setCoord(i, 'x', e.target.value)}
                                    />
                                    <label className="sr-only" htmlFor={`calib-y-${i}`}>Point {i + 1} y in metres</label>
                                    <input
                                        id={`calib-y-${i}`} className="form-input" inputMode="decimal" placeholder="y m"
                                        value={p.y} disabled={!canEdit || busy} onChange={(e) => setCoord(i, 'y', e.target.value)}
                                    />
                                    {canEdit ? (
                                        <button type="button" className="btn btn-ghost btn-xs" onClick={() => removePoint(i)} disabled={busy} aria-label={`Remove point ${i + 1}`}>
                                            <X size={14} strokeWidth={1.75} />
                                        </button>
                                    ) : null}
                                </li>
                            ))}
                        </ol>
                    )}
                </div>

                {canEdit ? (
                    <div className="calib-actions">
                        <button type="button" className="btn btn-primary btn-sm" onClick={save} disabled={!complete || !dirty || !frameWh || busy}>
                            {busy ? 'Saving…' : 'Save calibration'}
                        </button>
                        {dirty ? (
                            <button type="button" className="btn btn-secondary btn-sm" onClick={resetToSaved} disabled={busy}>Discard changes</button>
                        ) : null}
                        {current && !dirty ? (
                            <button type="button" className="btn btn-ghost btn-sm" onClick={remove} disabled={busy}>
                                <Trash2 size={14} strokeWidth={1.75} /> Remove
                            </button>
                        ) : null}
                    </div>
                ) : null}
            </div>
        </div>
    );
}
