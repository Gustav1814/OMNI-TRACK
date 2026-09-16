import { MOVING_DIRECTIONS } from "./types";
const VIDEO_EXT = /\.(mp4|avi|mov|mkv|flv|wmv|webm)$/i;
function isPlaintextUrl(url) {
  if (!url) return false;
  const lower = url.trim().toLowerCase();
  return lower.startsWith("rtsp://") || lower.startsWith("http://") || lower.startsWith("https://") || VIDEO_EXT.test(lower);
}
/**
 * Mirrors the backend's `_resolve_source` (app/routers/detection.py), which
 * accepts rtsp | http | file | webcam rather than the original rtsp | video.
 */
function validateSourceUrl(url, urlType) {
  const s = (url || "").trim();
  if (!s) return "Source URL is required.";
  if (urlType === "rtsp" && !/^rtsp:\/\//i.test(s)) {
    return "RTSP sources must start with rtsp://";
  }
  if (urlType === "http" && !/^https?:\/\//i.test(s)) {
    return "HTTP sources must start with http:// or https://";
  }
  if (urlType === "webcam" && !/^\d{1,2}$/.test(s)) {
    return "A webcam source is a device index, e.g. 0";
  }
  if (urlType === "file") {
    if (/^footage:/i.test(s)) {
      return s.slice("footage:".length).trim()
        ? null
        : "footage: needs a filename, e.g. footage:clip.mp4";
    }
    if (!VIDEO_EXT.test(s)) {
      return "Video files must end in .mp4, .avi, .mov, .mkv, .flv, .wmv or .webm";
    }
  }
  return null;
}
function validateRegion(region) {
  if (!region.name.trim()) return "Every region needs a name.";
  if (region.type === "polygon" && (!region.points || region.points.length < 3)) {
    return `Polygon "${region.name}" needs at least 3 points.`;
  }
  if (region.type === "bounding_box" && !region.coordinates) {
    return `Bounding box "${region.name}" is missing coordinates.`;
  }
  if (region.type === "Line") {
    if (!region.line_points || region.line_points.length !== 2) {
      return `Line "${region.name}" must have exactly 2 points.`;
    }
    if (!region.object_moving_direction || !MOVING_DIRECTIONS.includes(region.object_moving_direction)) {
      return `Line "${region.name}" needs a moving direction (${MOVING_DIRECTIONS.join(", ")}).`;
    }
  }
  return null;
}
function validateRegionsForKpi(regions, kpi) {
  for (const region of regions) {
    const own = validateRegion(region);
    if (own) return own;
    if (kpi && kpi.allowed_region_types.length > 0 && !kpi.allowed_region_types.includes(region.type)) {
      return `Region type "${region.type}" is not allowed for KPI "${kpi.kpi_name}". Allowed: ${kpi.allowed_region_types.join(", ")}.`;
    }
  }
  return null;
}
function validateModelPipeline(modelInfos, kpi) {
  if (modelInfos.length === 0) return "Select at least one model for the pipeline.";
  if (!kpi) return "Select a KPI before configuring models.";
  const stageOf = new Map(kpi.models.map((m) => [m.model_id, m.stage]));
  const allowedIds = kpi.models.map((m) => m.model_id);
  const stage0Ids = kpi.models.filter((m) => m.stage === 0).map((m) => m.model_id);
  const isMultistage = new Set(kpi.models.map((m) => m.stage)).size > 1;
  const pickedIds = modelInfos.map((m) => m.model_id);
  for (const info of modelInfos) {
    if (!allowedIds.includes(info.model_id)) {
      return `Model "${info.model_id}" is not valid for KPI "${kpi.kpi_name}". Allowed: ${allowedIds.join(", ")}.`;
    }
    const stage = stageOf.get(info.model_id) ?? 0;
    if (isMultistage && stage > 0) {
      if (!info.lead_by) {
        return `"${info.model_id}" is a stage ${stage} model and must declare which stage 0 model leads it (lead_by).`;
      }
      if (!pickedIds.includes(info.lead_by)) {
        return `"${info.model_id}" is led by "${info.lead_by}", which is not part of this pipeline.`;
      }
      if (stageOf.get(info.lead_by) !== 0) {
        return `"${info.model_id}" must be led by a stage 0 model; "${info.lead_by}" is stage ${stageOf.get(info.lead_by)}.`;
      }
    }
    if (!isMultistage && info.lead_by) {
      return `KPI "${kpi.kpi_name}" is single-stage \u2014 remove lead_by from "${info.model_id}".`;
    }
  }
  if (isMultistage) {
    if (!pickedIds.some((id) => stage0Ids.includes(id))) {
      return `Multistage KPI "${kpi.kpi_name}" needs at least one stage 0 model (${stage0Ids.join(", ")}).`;
    }
    const sorted = [...modelInfos].sort((a, b) => a.order - b.order);
    for (let i = 0; i < sorted.length - 1; i++) {
      const cur = stageOf.get(sorted[i].model_id) ?? 0;
      const next = stageOf.get(sorted[i + 1].model_id) ?? 0;
      if (next < cur) {
        return `Invalid order: "${sorted[i + 1].model_id}" (stage ${next}) cannot run after "${sorted[i].model_id}" (stage ${cur}). Stage 0 models come first.`;
      }
    }
  }
  return null;
}
function validateRuntime(values) {
  if (!Number.isInteger(values.num_of_frame_per_sec) || values.num_of_frame_per_sec < 1) {
    return "KPI write throttle must be an integer \u2265 1.";
  }
  if (values.redis_job_ttl_seconds < 1) return "Job TTL must be at least 1 second.";
  if (values.redis_detection_log_ttl_seconds < 1) {
    return "Detection log TTL must be at least 1 second.";
  }
  return null;
}
export {
  isPlaintextUrl,
  validateModelPipeline,
  validateRegion,
  validateRegionsForKpi,
  validateRuntime,
  validateSourceUrl
};
