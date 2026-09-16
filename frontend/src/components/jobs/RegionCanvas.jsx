import {
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  Bell,
  BellRing,
  BoxSelect,
  Minus,
  Pentagon,
  Trash2
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { MOVING_DIRECTIONS } from "./types";
const DEFAULT_W = 1280;
const DEFAULT_H = 720;
function snapToAxis(from, to) {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  return Math.abs(dx) >= Math.abs(dy) ? { x: to.x, y: from.y } : { x: from.x, y: to.y };
}
const DIRECTION_META = {
  up_to_down: { label: "Down", icon: ArrowDown },
  down_to_up: { label: "Up", icon: ArrowUp },
  left_to_right: { label: "Right", icon: ArrowRight },
  right_to_left: { label: "Left", icon: ArrowLeft }
};
const TOOL_META = {
  polygon: { label: "Polygon", icon: Pentagon },
  bounding_box: { label: "Box", icon: BoxSelect },
  Line: { label: "Line", icon: Minus }
};
/* The background element may be an <img> (MJPEG) or a <video> (direct file
   playback). These normalise the two so the canvas can sample either. */
function mediaSize(el) {
  if (!el) return null;
  const w = el.videoWidth ?? el.naturalWidth ?? 0;
  const h = el.videoHeight ?? el.naturalHeight ?? 0;
  return w > 0 && h > 0 ? { w, h } : null;
}
function mediaReady(el) {
  if (!el) return false;
  if (el.tagName === "VIDEO") return el.readyState >= 2; // HAVE_CURRENT_DATA
  return Boolean(el.complete) && (el.naturalWidth ?? 0) > 0;
}
function RegionCanvas({
  regions,
  onChange,
  allowedTypes,
  streamImgRef,
  variant = "panel",
  tool: controlledTool,
  onToolChange,
  onFrameSize
}) {
  const canvasRef = useRef(null);
  const [internalTool, setInternalTool] = useState(allowedTypes[0] ?? null);
  const tool = variant === "bare" ? controlledTool ?? null : internalTool;
  const setTool = (next) => {
    if (variant === "bare") onToolChange?.(next);
    else setInternalTool(next);
  };
  const [draft, setDraft] = useState([]);
  const [dragStart, setDragStart] = useState(null);
  const [hover, setHover] = useState(null);
  const [naming, setNaming] = useState(null);
  const [nameInput, setNameInput] = useState("");
  const [lineDirection, setLineDirection] = useState(MOVING_DIRECTIONS[0]);
  const nameInputRef = useRef(null);
  const [bgReady, setBgReady] = useState(false);
  const [frameW, setFrameW] = useState(DEFAULT_W);
  const [frameH, setFrameH] = useState(DEFAULT_H);
  useEffect(() => {
    if (tool === null || !allowedTypes.includes(tool)) {
      setTool(allowedTypes[0] ?? null);
      setDraft([]);
      setDragStart(null);
    }
  }, [allowedTypes, tool]);
  useEffect(() => {
    if (naming && nameInputRef.current) {
      nameInputRef.current.focus();
    }
  }, [naming]);
  useEffect(() => {
    const el = streamImgRef?.current;
    if (!el) return undefined;
    const sync = () => {
      const size = mediaSize(el);
      if (size) {
        setFrameW(size.w);
        setFrameH(size.h);
        // Regions are stored in these coordinates; the backend needs them to
        // rescale onto whatever resolution the pipeline actually decodes.
        onFrameSize?.(size.w, size.h);
      }
      setBgReady(mediaReady(el));
    };
    const handleError = () => setBgReady(false);
    sync();
    // "load" covers <img>; the rest cover <video> arriving/seeking/looping.
    const events = ["load", "loadedmetadata", "loadeddata", "canplay", "playing", "seeked"];
    events.forEach((e) => el.addEventListener(e, sync));
    el.addEventListener("error", handleError);
    return () => {
      events.forEach((e) => el.removeEventListener(e, sync));
      el.removeEventListener("error", handleError);
    };
  }, [streamImgRef, onFrameSize]);
  const toFrame = (event) => {
    const rect = event.currentTarget.getBoundingClientRect();
    return {
      x: Math.round(Math.min(Math.max(0, (event.clientX - rect.left) / rect.width * frameW), frameW)),
      y: Math.round(Math.min(Math.max(0, (event.clientY - rect.top) / rect.height * frameH), frameH))
    };
  };
  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    ctx.clearRect(0, 0, frameW, frameH);
    const bgImg = streamImgRef?.current;
    if (bgImg && mediaReady(bgImg) && mediaSize(bgImg)) {
      ctx.drawImage(bgImg, 0, 0, frameW, frameH);
    } else {
      ctx.fillStyle = "#0c0c10";
      ctx.fillRect(0, 0, frameW, frameH);
      ctx.strokeStyle = "rgba(255, 255, 255, 0.06)";
      ctx.lineWidth = 1;
      for (let x = 0; x <= frameW; x += 64) {
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, frameH);
        ctx.stroke();
      }
      for (let y = 0; y <= frameH; y += 64) {
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(frameW, y);
        ctx.stroke();
      }
    }
    const palette = ["#7c8cff", "#c084fc", "#34d399", "#fb923c", "#fb7185"];
    regions.forEach((region, i) => {
      const color = palette[i % palette.length];
      ctx.strokeStyle = color;
      ctx.fillStyle = `${color}22`;
      ctx.lineWidth = 3;
      ctx.setLineDash([]);
      if (region.type === "polygon" && region.points?.length) {
        ctx.lineWidth = 4.2;
        ctx.beginPath();
        region.points.forEach((p, j) => j === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y));
        ctx.closePath();
        ctx.fill();
        ctx.stroke();
        labelAt(ctx, region.name, region.points[0], color);
        if (region.tag === "alert") armedBadge(ctx, region.points[0], color);
      }
      if (region.type === "bounding_box" && region.coordinates) {
        const c = region.coordinates;
        ctx.fillStyle = `${color}22`;
        ctx.fillRect(c.x_min, c.y_min, c.x_max - c.x_min, c.y_max - c.y_min);
        ctx.strokeRect(c.x_min, c.y_min, c.x_max - c.x_min, c.y_max - c.y_min);
        labelAt(ctx, region.name, { x: c.x_min, y: c.y_min }, color);
        if (region.tag === "alert") armedBadge(ctx, { x: c.x_min, y: c.y_min }, color);
      }
      if (region.type === "Line" && region.line_points?.length === 2) {
        const [a, b] = region.line_points;
        ctx.setLineDash([2, 7]);
        ctx.lineCap = "round";
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.lineCap = "butt";
        drawArrow(ctx, a, b, region.object_moving_direction ?? "", color);
        labelAt(ctx, region.name, a, color);
      }
    });
    if (naming) {
      ctx.strokeStyle = "#a5b0ff";
      ctx.lineWidth = 3;
      if (naming.type === "polygon" && naming.points?.length) {
        ctx.lineWidth = 4.2;
        ctx.setLineDash([]);
        ctx.fillStyle = "rgba(124, 140, 255, 0.12)";
        ctx.beginPath();
        naming.points.forEach((p, j) => j === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y));
        ctx.closePath();
        ctx.fill();
        ctx.stroke();
        naming.points.forEach((p) => dot(ctx, p, "#a5b0ff"));
      }
      if (naming.type === "bounding_box" && naming.coordinates) {
        const c = naming.coordinates;
        ctx.setLineDash([]);
        ctx.fillStyle = "rgba(124, 140, 255, 0.12)";
        ctx.fillRect(c.x_min, c.y_min, c.x_max - c.x_min, c.y_max - c.y_min);
        ctx.strokeRect(c.x_min, c.y_min, c.x_max - c.x_min, c.y_max - c.y_min);
      }
      if (naming.type === "Line" && naming.line_points?.length === 2) {
        const [a, b] = naming.line_points;
        ctx.setLineDash([2, 7]);
        ctx.lineCap = "round";
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.lineCap = "butt";
        dot(ctx, a, "#a5b0ff");
        dot(ctx, b, "#a5b0ff");
      }
    } else {
      ctx.strokeStyle = "#a5b0ff";
      ctx.setLineDash([5, 3]);
      ctx.lineWidth = 3;
      if (tool === "polygon" && draft.length) {
        ctx.lineWidth = 4.2;
        ctx.beginPath();
        draft.forEach((p, j) => j === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y));
        if (hover) ctx.lineTo(hover.x, hover.y);
        ctx.stroke();
        draft.forEach((p) => dot(ctx, p, "#a5b0ff"));
      }
      if (tool === "Line" && draft.length === 1) {
        const previewEnd = hover ? snapToAxis(draft[0], hover) : null;
        ctx.beginPath();
        ctx.moveTo(draft[0].x, draft[0].y);
        if (previewEnd) ctx.lineTo(previewEnd.x, previewEnd.y);
        ctx.stroke();
        dot(ctx, draft[0], "#a5b0ff");
      }
      if (tool === "bounding_box" && dragStart && hover) {
        ctx.fillStyle = "rgba(124, 140, 255, 0.12)";
        const x = Math.min(dragStart.x, hover.x);
        const y = Math.min(dragStart.y, hover.y);
        const w = Math.abs(hover.x - dragStart.x);
        const h = Math.abs(hover.y - dragStart.y);
        ctx.fillRect(x, y, w, h);
        ctx.strokeRect(x, y, w, h);
      }
    }
    ctx.setLineDash([]);
  }, [regions, draft, dragStart, hover, tool, naming, streamImgRef, bgReady, frameW, frameH]);
  useEffect(() => {
    draw();
  }, [draw]);
  // A <video> only paints one frame per draw() call, so drive a RAF loop while
  // it is actually playing; an <img> needs no loop and keeps the old behaviour.
  useEffect(() => {
    const el = streamImgRef?.current;
    if (!el || el.tagName !== "VIDEO") return undefined;
    let raf = 0;
    const tick = () => {
      if (!el.paused && !el.ended) draw();
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [draw, streamImgRef]);
  const nextName = (type) => {
    const base = type === "bounding_box" ? "box" : type === "Line" ? "line" : "zone";
    let n = 1;
    while (regions.some((r) => r.name === `${base}-${n}`)) n += 1;
    return `${base}-${n}`;
  };
  const openNaming = (data) => {
    setNaming(data);
    setNameInput(nextName(data.type));
    if (data.type === "Line" && data.line_points?.length === 2) {
      const [a, b] = data.line_points;
      setLineDirection(a.y === b.y ? "up_to_down" : "left_to_right");
    }
  };
  const confirmName = () => {
    if (!naming) return;
    const name = nameInput.trim() || nextName(naming.type);
    const region = {
      ...naming,
      name,
      tag: null,
      ...naming.type === "Line" ? { object_moving_direction: lineDirection } : {}
    };
    onChange([...regions, region]);
    setNaming(null);
    setNameInput("");
    setDraft([]);
  };
  const toggleArmAlerts = (index) => {
    const next = regions.map(
      (r, i) => i === index ? { ...r, tag: r.tag === "alert" ? null : "alert" } : r
    );
    onChange(next);
  };
  const onPointerDown = (event) => {
    if (naming) return;
    const p = toFrame(event);
    if (tool === "bounding_box") {
      setDragStart(p);
      return;
    }
    if (tool === "polygon") {
      setDraft((d) => [...d, p]);
      return;
    }
    if (tool === "Line") {
      if (draft.length === 0) {
        setDraft([p]);
      } else {
        openNaming({
          type: "Line",
          line_points: [draft[0], snapToAxis(draft[0], p)]
        });
      }
    }
  };
  const onPointerUp = (event) => {
    if (naming) return;
    if (tool !== "bounding_box" || !dragStart) return;
    const p = toFrame(event);
    const coords = {
      x_min: Math.min(dragStart.x, p.x),
      y_min: Math.min(dragStart.y, p.y),
      x_max: Math.max(dragStart.x, p.x),
      y_max: Math.max(dragStart.y, p.y)
    };
    if (coords.x_max - coords.x_min > 8 && coords.y_max - coords.y_min > 8) {
      openNaming({ type: "bounding_box", coordinates: coords });
    }
    setDragStart(null);
  };
  const onKeyDown = (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      if (naming) {
        confirmName();
      } else if (tool === "polygon" && draft.length >= 3) {
        openNaming({ type: "polygon", points: [...draft] });
      }
    }
    if (e.key === "Escape") {
      setDraft([]);
      setDragStart(null);
      setNaming(null);
    }
    if (e.key === "Backspace" && !naming) {
      setDraft((d) => d.slice(0, -1));
    }
  };
  return <div className={`region-canvas-root${variant === "bare" ? " region-canvas-root--bare" : ""}`}>
      <div className="region-canvas-main">
        {variant === "panel" && <div className="canvas-toolbar" role="toolbar" aria-label="Region tools">
            {allowedTypes.map((type) => {
    const meta = TOOL_META[type];
    return <button
      key={type}
      type="button"
      className={`chip${tool === type ? " active" : ""}`}
      onClick={() => {
        setTool(type);
        setDraft([]);
        setDragStart(null);
      }}
      aria-pressed={tool === type}
    >
                  <meta.icon size={13} aria-hidden />
                  {meta.label}
                </button>;
  })}
            <span style={{ flex: 1 }} />
            <span className="field__hint" style={{ margin: 0, fontSize: "var(--fs-xs)" }}>
              {tool === "polygon" && "Click to add points, Enter to confirm."}
              {tool === "bounding_box" && "Click & drag, release to confirm."}
              {tool === "Line" && "Click the start point, then the end point, and set a direction."}
            </span>
          </div>}

        <div className="canvas-wrap" onKeyDown={onKeyDown} tabIndex={0}>
          <canvas
    ref={canvasRef}
    width={frameW}
    height={frameH}
    onPointerDown={onPointerDown}
    onPointerUp={onPointerUp}
    onPointerMove={(e) => setHover(toFrame(e))}
    onPointerLeave={() => setHover(null)}
    aria-label="Region drawing canvas"
  />

          {naming && <div className="region-naming-popup" role="dialog" aria-label="Name region">
            <span className="region-naming-popup__label">Region name</span>
            <input
    ref={nameInputRef}
    className="input"
    value={nameInput}
    onChange={(e) => setNameInput(e.target.value)}
    onKeyDown={(e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        confirmName();
      }
      if (e.key === "Escape") {
        setNaming(null);
        setDraft([]);
      }
    }}
    placeholder={nextName(naming.type)}
  />
            {naming.type === "Line" && naming.line_points?.length === 2 && <div className="region-line-direction">
                <span className="field__hint" style={{ margin: 0, fontSize: "var(--fs-xs)", whiteSpace: "nowrap" }}>
                  Crossing this way = In
                </span>
                <div className="region-line-direction__options" role="radiogroup" aria-label="Line direction">
                  {(naming.line_points[0].y === naming.line_points[1].y ? ["up_to_down", "down_to_up"] : ["left_to_right", "right_to_left"]).map((value) => {
    const meta = DIRECTION_META[value];
    return <button
      key={value}
      type="button"
      className={`region-line-direction__btn${lineDirection === value ? " active" : ""}`}
      onClick={() => setLineDirection(value)}
      aria-pressed={lineDirection === value}
      title={`${meta.label} = In, opposite = Out`}
    >
                        <meta.icon size={14} aria-hidden />
                        {meta.label}
                      </button>;
  })}
                </div>
              </div>}
            <div className="region-naming-popup__actions">
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => {
    setNaming(null);
    setDraft([]);
  }}>Cancel</button>
              <button type="button" className="btn btn--primary btn--sm" onClick={confirmName}>Confirm</button>
            </div>
          </div>}
        </div>
      </div>

      {variant === "panel" && <div className="region-sidebar">
          <span className="field__label" style={{ marginBottom: "var(--sp-2)" }}>Regions ({regions.length})</span>
          {regions.length === 0 && <p className="field__hint">No regions drawn yet.</p>}
          {regions.map((region, i) => {
    const palette = ["#7c8cff", "#c084fc", "#34d399", "#fb923c", "#fb7185"];
    const color = palette[i % palette.length];
    return <div key={`${region.name}-${i}`} className="region-sidebar__item" style={{ borderLeftColor: color }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="cell-strong" style={{ fontSize: "var(--fs-sm)" }}>{region.name}</div>
                  <div style={{ fontSize: "var(--fs-xs)", color: "var(--text-3)" }}>
                    {region.type}{region.tag === "alert" && <span style={{ color: "var(--danger)" }}> · armed</span>}
                  </div>
                </div>
                {region.type !== "Line" && <button
      type="button"
      className={`btn btn--quiet btn--sm btn--icon${region.tag === "alert" ? " is-armed" : ""}`}
      onClick={() => toggleArmAlerts(i)}
      aria-pressed={region.tag === "alert"}
      title={region.tag === "alert" ? "Disable alerts for this region" : "Arm alerts for this region"}
    >
                    {region.tag === "alert" ? <BellRing size={14} /> : <Bell size={14} />}
                  </button>}
                <button
      type="button"
      className="btn btn--quiet btn--sm btn--icon"
      onClick={() => onChange(regions.filter((_, j) => j !== i))}
      aria-label={`Delete region ${region.name}`}
    >
                  <Trash2 size={14} />
                </button>
              </div>;
  })}
        </div>}
    </div>;
}
function labelAt(ctx, text, p, color) {
  ctx.save();
  ctx.font = "600 17px 'JetBrains Mono', monospace";
  const w = ctx.measureText(text).width + 14;
  ctx.fillStyle = color;
  ctx.fillRect(p.x, Math.max(0, p.y - 26), w, 24);
  ctx.fillStyle = "#0b0b1e";
  ctx.fillText(text, p.x + 7, Math.max(17, p.y - 8));
  ctx.restore();
}
function armedBadge(ctx, p, color) {
  ctx.save();
  const text = "ALERT";
  ctx.font = "700 13px 'JetBrains Mono', monospace";
  const w = ctx.measureText(text).width + 14;
  const x = p.x;
  const y = Math.max(0, p.y - 50);
  ctx.fillStyle = color;
  ctx.fillRect(x, y, w, 22);
  ctx.fillStyle = "#fff";
  ctx.fillText(text, x + 7, y + 15);
  ctx.restore();
}
function dot(ctx, p, color) {
  ctx.save();
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.arc(p.x, p.y, 5, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}
function drawArrow(ctx, a, b, direction, color) {
  const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len = Math.hypot(dx, dy) || 1;
  const flip = direction === "down_to_up" || direction === "right_to_left" ? -1 : 1;
  const nx = -dy / len * 42 * flip;
  const ny = dx / len * 42 * flip;
  const tip = { x: mid.x + nx, y: mid.y + ny };
  ctx.save();
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = 2.5;
  ctx.setLineDash([]);
  ctx.beginPath();
  ctx.moveTo(mid.x, mid.y);
  ctx.lineTo(tip.x, tip.y);
  ctx.stroke();
  const angle = Math.atan2(tip.y - mid.y, tip.x - mid.x);
  ctx.beginPath();
  ctx.moveTo(tip.x, tip.y);
  ctx.lineTo(tip.x - 11 * Math.cos(angle - 0.45), tip.y - 11 * Math.sin(angle - 0.45));
  ctx.lineTo(tip.x - 11 * Math.cos(angle + 0.45), tip.y - 11 * Math.sin(angle + 0.45));
  ctx.closePath();
  ctx.fill();
  ctx.restore();
}
export {
  DIRECTION_META,
  RegionCanvas
};
