/**
 * OmniTrack AI — Floor heatmap
 * Where people actually spent time: person-seconds per grid cell from the foot points of
 * confirmed tracks (GET /api/zones/cameras/{id}/heatmap), painted over a clean frame of the
 * same camera. One hue (the amber signal) from transparent to solid; nothing is interpolated
 * beyond the measured cells except display smoothing. Empty windows say so.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { camerasAPI, zonesAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import { useTokens, withAlpha } from '../lib/tokens';
import { DASH, NoData, Panel, Seg, fmtDuration } from './zones/analyticsUi';
import '../styles/heatmap.css';

const WINDOWS = [
    { value: 15, label: '15 min' },
    { value: 60, label: '1 h' },
    { value: 480, label: '8 h' },
    { value: 1440, label: '24 h' },
];
const MAX_ALPHA = 0.82;
const GAMMA = 0.55;      // lifts quiet cells so the walkways stay visible next to the hotspot
const POLL_MS = 15000;

function useCleanFrame(cameraId, refreshKey) {
    const [frame, setFrame] = useState({ src: null, failed: false });
    useEffect(() => {
        if (cameraId == null) return undefined;
        let alive = true;
        let url = null;
        zonesAPI.snapshot(cameraId, { clean: true })
            .then((res) => {
                if (!alive) return;
                url = URL.createObjectURL(res.data);
                setFrame({ src: url, failed: false });
            })
            .catch(() => alive && setFrame({ src: null, failed: true }));
        return () => {
            alive = false;
            if (url) URL.revokeObjectURL(url);
        };
    }, [cameraId, refreshKey]);
    return frame;
}

function paint(canvas, data, hue) {
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const W = Math.max(1, Math.round(rect.width * dpr));
    const H = Math.max(1, Math.round(rect.height * dpr));
    canvas.width = W;
    canvas.height = H;
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, W, H);
    if (!data || data.status !== 'ok' || !data.max) return;
    const [gw, gh] = data.grid;
    const off = document.createElement('canvas');
    off.width = gw;
    off.height = gh;
    const octx = off.getContext('2d');
    data.values.forEach((v, i) => {
        if (v <= 0) return;
        octx.fillStyle = withAlpha(hue, MAX_ALPHA * Math.pow(v / data.max, GAMMA));
        octx.fillRect(i % gw, Math.floor(i / gw), 1, 1);
    });
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    ctx.filter = `blur(${Math.round((W / gw) * 0.55)}px)`;
    ctx.drawImage(off, 0, 0, W, H);
    ctx.filter = 'none';
}

function hotspot(data) {
    if (!data || data.status !== 'ok') return null;
    const [gw, gh] = data.grid;
    const i = data.values.indexOf(data.max);
    if (i < 0) return null;
    return { x: ((i % gw) + 0.5) / gw, y: (Math.floor(i / gw) + 0.5) / gh };
}

export default function FloorHeatmap() {
    const tokens = useTokens();
    const [cameraId, setCameraId] = useState(null);
    const [minutes, setMinutes] = useState(60);
    const [hover, setHover] = useState(null);
    const canvasRef = useRef(null);

    const { data: camData, loading: camLoading } = useLivePoll(() => camerasAPI.list(), { intervalMs: 60000 });
    const cameras = useMemo(() => (Array.isArray(camData) ? camData : camData?.cameras || []), [camData]);
    useEffect(() => {
        if (cameraId == null && cameras.length) {
            setCameraId((cameras.find((c) => c.is_active) || cameras[0]).id);
        }
    }, [cameras, cameraId]);

    const { data, error, refresh } = useLivePoll(
        () => (cameraId == null ? Promise.resolve(null) : zonesAPI.heatmap(cameraId, minutes)),
        { intervalMs: POLL_MS, enabled: cameraId != null },
    );
    useEffect(() => { if (cameraId != null) refresh(); }, [cameraId, minutes, refresh]);

    const [frameKey, setFrameKey] = useState(0);
    useEffect(() => {
        const t = setInterval(() => setFrameKey((k) => k + 1), 60000);
        return () => clearInterval(t);
    }, []);
    const frame = useCleanFrame(cameraId, frameKey);

    const heat = data && data.camera_id === cameraId && data.minutes === minutes ? data : null;
    const hue = tokens.accentFill;

    useEffect(() => {
        const canvas = canvasRef.current;
        paint(canvas, heat, hue);
        if (!canvas || typeof ResizeObserver === 'undefined') return undefined;
        const ro = new ResizeObserver(() => paint(canvas, heat, hue));
        ro.observe(canvas);
        return () => ro.disconnect();
    }, [heat, hue]);

    const peak = hotspot(heat);
    const aspect = heat?.frame_wh?.[0] && heat?.frame_wh?.[1] ? `${heat.frame_wh[0]} / ${heat.frame_wh[1]}` : '16 / 9';
    const camera = cameras.find((c) => c.id === cameraId);

    const onMove = (e) => {
        if (!heat || heat.status !== 'ok') return;
        const r = e.currentTarget.getBoundingClientRect();
        const fx = (e.clientX - r.left) / r.width;
        const fy = (e.clientY - r.top) / r.height;
        const [gw, gh] = heat.grid;
        const cx = Math.min(gw - 1, Math.max(0, Math.floor(fx * gw)));
        const cy = Math.min(gh - 1, Math.max(0, Math.floor(fy * gh)));
        setHover({ x: fx, y: fy, v: heat.values[cy * gw + cx] || 0 });
    };

    if (!camLoading && !cameras.length) {
        return (
            <Panel title="Floor heatmap">
                <NoData title="No cameras yet">
                    Add a camera and run it; the heatmap fills from where tracked people walk and stop.
                </NoData>
                <Link to="/setup" className="btn btn-secondary btn-sm">Add a camera</Link>
            </Panel>
        );
    }

    const windowLabel = WINDOWS.find((w) => w.value === minutes)?.label;
    return (
        <Panel
            className="hm"
            title="Floor heatmap"
            sub="Where people spent time: foot points of tracked people, weighted by seconds present."
            actions={(
                <>
                    <select
                        className="form-select hm-camera"
                        aria-label="Camera"
                        value={cameraId ?? ''}
                        onChange={(e) => { setHover(null); setCameraId(Number(e.target.value)); }}
                    >
                        {cameras.map((c) => <option key={c.id} value={c.id}>{c.name || `Camera ${c.id}`}</option>)}
                    </select>
                    <Seg value={minutes} onChange={(v) => setMinutes(Number(v))} options={WINDOWS} label="Heatmap window" />
                </>
            )}
        >
            <figure
                className="hm-stage vf vf--on-media"
                style={{ aspectRatio: aspect }}
                onMouseMove={onMove}
                onMouseLeave={() => setHover(null)}
            >
                {frame.src ? (
                    <img className="hm-frame" src={frame.src} alt={`Frame from ${camera?.name || 'the camera'}`} />
                ) : (
                    <div className="hm-noframe">{frame.failed ? 'No frame readable from this camera' : 'Reading a frame'}</div>
                )}
                <canvas ref={canvasRef} className="hm-canvas" aria-hidden />
                {peak ? (
                    <span className="hm-peak" style={{ left: `${peak.x * 100}%`, top: `${peak.y * 100}%` }}>
                        <span className="hm-peak-label">Hotspot</span>
                    </span>
                ) : null}
                {hover ? (
                    <span
                        className="hm-tip"
                        role="status"
                        style={{ left: `${hover.x * 100}%`, top: `${hover.y * 100}%` }}
                        data-flip={hover.x > 0.7 ? 'left' : undefined}
                    >
                        <strong>{hover.v > 0 ? fmtDuration(hover.v) : 'No one'}</strong>
                        {hover.v > 0 && heat?.max ? <span>{Math.round((hover.v / heat.max) * 100)} % of hotspot</span> : null}
                    </span>
                ) : null}
                {heat && heat.status === 'no_data' ? (
                    <div className="hm-empty">
                        <strong>No foot traffic in the last {windowLabel}</strong>
                        <span>Run this camera from Live; cells fill as tracked people move through the frame.</span>
                    </div>
                ) : null}
                {error ? <div className="hm-empty"><strong>Heatmap unavailable</strong><span>The API did not answer; retrying.</span></div> : null}
                <figcaption className="osd-strip hm-osd">
                    <span><span className="live-dot is-live" aria-hidden />Heat</span>
                    <strong>{camera?.name || DASH}</strong>
                    <span>{windowLabel}</span>
                </figcaption>
            </figure>

            <div className="hm-foot">
                <dl className="hm-stats">
                    <div><dt>Time on floor</dt><dd>{heat?.status === 'ok' ? fmtDuration(heat.total) : DASH}</dd></div>
                    <div><dt>Hotspot dwell</dt><dd>{heat?.status === 'ok' ? fmtDuration(heat.max) : DASH}</dd></div>
                    <div><dt>Minutes recorded</dt><dd>{heat?.status === 'ok' ? heat.buckets : DASH}</dd></div>
                </dl>
                <div className="hm-scale" aria-label="Colour scale: transparent is no time, solid is the hotspot">
                    <span>0</span>
                    <span className="hm-scale-bar" />
                    <span>{heat?.status === 'ok' ? fmtDuration(heat.max) : 'max'}</span>
                </div>
            </div>
        </Panel>
    );
}
