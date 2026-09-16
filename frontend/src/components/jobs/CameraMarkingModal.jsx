import { Bell, BellRing, BoxSelect, Minus, Pentagon, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { previewUrlFor } from "./jobsApi";
import { DIRECTION_META, RegionCanvas } from "./RegionCanvas";
const TOOL_META = [
  { type: "bounding_box", label: "Box", icon: BoxSelect },
  { type: "polygon", label: "Polygon", icon: Pentagon },
  { type: "Line", label: "Line", icon: Minus }
];
function CameraMarkingModal({ open, onClose, draft, kpi, regions, onChange, onFrameSize }) {
  const [tool, setTool] = useState("bounding_box");
  const allowedTypes = kpi && kpi.allowed_region_types.length > 0 ? kpi.allowed_region_types : ["bounding_box", "polygon", "Line"];
  // NOTE: the <video> deliberately has no crossOrigin attribute. Setting it to
  // "anonymous" makes the browser refuse any source that does not return CORS
  // headers (S3 buckets typically don't), failing with MEDIA_ELEMENT_ERROR. The
  // canvas becomes tainted without it, which is harmless here: RegionCanvas only
  // ever drawImage()s, it never reads pixels back via getImageData/toDataURL.
  //
  // The source repo pulled MJPEG frames from a dedicated stream service. OmniTrack
  // has no such endpoint for an un-started feed, so the preview plays the source
  // directly in a <video>: an http(s) link as-is, a stored clip through
  // /api/footage/serve. RTSP and webcam indices cannot be played by a browser.
  const streamImgRef = useRef(null);
  const previewUrl = useMemo(() => previewUrlFor(draft.url), [draft.url]);
  const unplayableReason = useMemo(() => {
    const src = (draft.url || "").trim();
    if (!src) return "Enter a source URL to preview the feed.";
    if (previewUrl) return null;
    if (/^rtsp:\/\//i.test(src)) {
      return "RTSP streams cannot be played by a browser. Add the feed, then watch it on Video Feeds — you can still draw regions on the grid below.";
    }
    if (/^\d{1,2}$/.test(src)) {
      return "A webcam feed cannot be previewed before the camera is started. You can still draw regions on the grid below.";
    }
    return "This source cannot be previewed in the browser. You can still draw regions on the grid below.";
  }, [draft.url, previewUrl]);
  useEffect(() => {
    if (!open) return undefined;
    const video = streamImgRef.current;
    if (!video || !previewUrl) return undefined;

    // play() must wait for data: calling it straight after load() races the
    // fetch and rejects with AbortError, leaving the preview frozen on frame 0.
    const start = () => {
      video.play().catch(() => {
        /* autoplay blocked — the first frame is still painted onto the canvas */
      });
    };
    video.addEventListener("loadeddata", start);
    video.src = previewUrl;
    video.load();
    if (video.readyState >= 2) start();

    return () => {
      video.removeEventListener("loadeddata", start);
    };
  }, [open, previewUrl]);
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return <div
    className="cmark-backdrop"
    onMouseDown={(e) => {
      if (e.target === e.currentTarget) onClose();
    }}
  >
      <div className="cmark" role="dialog" aria-modal="true" aria-labelledby="cmark-title">
        <header className="cmark__head">
          <div>
            <h2 className="cmark__title" id="cmark-title">Camera Marking</h2>
          </div>
          <button type="button" className="cmark__close" onClick={onClose} aria-label="Close">
            <X size={16} />
          </button>
        </header>

        <div className="cmark__body">
          {
    /* Left — vertical toolbar */
  }
          <div className="cmark-tools" role="toolbar" aria-label="Region tools">
            {TOOL_META.map(({ type, label, icon: Icon }) => {
    const allowed = allowedTypes.includes(type);
    return <button
      key={type}
      type="button"
      className={`cmark-tool${tool === type ? " is-active" : ""}`}
      onClick={() => setTool(type)}
      disabled={!allowed}
      aria-pressed={tool === type}
      title={allowed ? label : `${label} \u2014 not allowed for the selected KPI`}
    >
                  <Icon size={17} aria-hidden />
                </button>;
  })}
          </div>

          {
    /* Middle — video preview + drawing canvas */
  }
          <div className="cmark-video">
            <video
    ref={streamImgRef}
    autoPlay
    /* 2x2 and very nearly transparent rather than 0x0/opacity:0 — Chrome pauses
       a video element that has no rendered box, so play() resolves and the
       preview then sits frozen on frame 0. The canvas is what you actually see. */
    style={{ position: "absolute", width: 2, height: 2, opacity: 0.01, pointerEvents: "none" }}
    muted
    loop
    playsInline
    aria-hidden
  />
            <RegionCanvas
    regions={regions}
    onChange={onChange}
    allowedTypes={allowedTypes}
    streamImgRef={streamImgRef}
    variant="bare"
    tool={tool}
    onToolChange={setTool}
    onFrameSize={onFrameSize}
  />
            {unplayableReason && <p className="cmark-video__empty">{unplayableReason}</p>}
          </div>

          {
    /* Right — saved regions panel */
  }
          <div className="cmark-panel">
            <div className="cmark-panel__head">
              <span className="cmark-panel__title">Saved regions for this camera</span>
              <span className="cmark-panel__count">{regions.length} saved</span>
            </div>

            <div className="cmark-regions">
              {regions.length === 0 && <p className="cmark-regions__empty">No regions drawn yet — use the Box, Polygon, or Line tool on the preview.</p>}
              {regions.map((region, i) => <div key={`${region.name}-${i}`} className="cmark-region">
                  <div className="cmark-region__body">
                    <span className="cmark-region__name">{region.name}</span>
                    <span className="cmark-region__type">
                      {region.type}
                      {region.type === "Line" && region.object_moving_direction && <span className="cmark-region__armed">
                          · In: {DIRECTION_META[region.object_moving_direction]?.label ?? region.object_moving_direction}
                        </span>}
                      {region.tag === "alert" && <span className="cmark-region__armed">· armed</span>}
                    </span>
                  </div>
                  {region.type !== "Line" && <button
    type="button"
    className={`cmark-region__icon-btn${region.tag === "alert" ? " is-armed" : ""}`}
    onClick={() => onChange(
      regions.map(
        (r, j) => j === i ? { ...r, tag: r.tag === "alert" ? null : "alert" } : r
      )
    )}
    aria-pressed={region.tag === "alert"}
    title={region.tag === "alert" ? "Disable alarms for this region" : "Arm alarms for this region"}
  >
                      {region.tag === "alert" ? <BellRing size={14} /> : <Bell size={14} />}
                    </button>}
                  <button
    type="button"
    className="cmark-region__delete"
    onClick={() => onChange(regions.filter((_, j) => j !== i))}
    aria-label={`Delete region ${region.name}`}
  >
                    <Trash2 size={14} />
                  </button>
                </div>)}
            </div>
          </div>
        </div>
      </div>
    </div>;
}
export {
  CameraMarkingModal
};
