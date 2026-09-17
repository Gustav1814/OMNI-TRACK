import { URL_TYPES } from "./types";
const emptyDraft = {
  job_id: "",
  camera_id: "",
  location_id: "",
  url: "",
  url_type: "rtsp",
  loop: false,
  enable_reid: true,
  kpi_name: "",
  model_infos: [],
  regions: [],
  frame_width: 0,
  frame_height: 0,
  num_of_frame_per_sec: 1,
  redis_job_ttl_seconds: 86400,
  redis_detection_log_ttl_seconds: 300
};
function draftFromConfig(raw) {
  return {
    job_id: raw.job_id ?? "",
    camera_id: raw.camera_id ?? "",
    location_id: raw.location_id ?? "",
    url: raw.url ?? "",
    url_type: URL_TYPES.includes(raw.url_type ?? "") ? raw.url_type : "rtsp",
    kpi_name: raw.kpi_name ?? "",
    model_infos: Array.isArray(raw.model_infos) ? raw.model_infos : [],
    regions: Array.isArray(raw.regions) ? raw.regions : [],
    num_of_frame_per_sec: typeof raw.num_of_frame_per_sec === "number" ? raw.num_of_frame_per_sec : 1,
    redis_job_ttl_seconds: typeof raw.redis_job_ttl_seconds === "number" ? raw.redis_job_ttl_seconds : 86400,
    redis_detection_log_ttl_seconds: typeof raw.redis_detection_log_ttl_seconds === "number" ? raw.redis_detection_log_ttl_seconds : 300
  };
}
function normalizeModelOrder(modelInfos, kpi) {
  const stageOf = new Map((kpi?.models ?? []).map((m) => [m.model_id, m.stage]));
  return [...modelInfos].sort((a, b) => (stageOf.get(a.model_id) ?? 0) - (stageOf.get(b.model_id) ?? 0)).map((m, i) => ({ ...m, order: i }));
}
function computeTags(modelInfos) {
  const allTags = /* @__PURE__ */ new Set();
  const snapshotTags = /* @__PURE__ */ new Set();
  for (const mi of modelInfos) {
    for (const oc of mi.object_classes ?? []) {
      if (oc.detection) allTags.add(oc.class_name);
      if (oc.snapshot) snapshotTags.add(oc.class_name);
    }
  }
  return { tag: [...allTags].join(","), snapshots_at_tag: [...snapshotTags].join(",") };
}
function buildRegisterRequest(draft, kpi) {
  const { tag, snapshots_at_tag } = computeTags(draft.model_infos);
  return {
    job_id: draft.job_id.trim() || null,
    camera_id: draft.camera_id.trim(),
    location_id: draft.location_id.trim(),
    kpi_name: draft.kpi_name,
    url: draft.url.trim(),
    url_type: draft.url_type,
    loop: Boolean(draft.loop),
    enable_reid: draft.enable_reid !== false,
    tag,
    regions: draft.regions,
    // Frame the regions were drawn against, so the backend can rescale them
    // onto whatever resolution the pipeline actually decodes.
    frame_width: draft.frame_width,
    frame_height: draft.frame_height,
    snapshots_at_tag,
    tag_at_reid: "",
    is_global_reid: false,
    num_of_frame_per_sec: draft.num_of_frame_per_sec,
    redis_job_ttl_seconds: draft.redis_job_ttl_seconds,
    redis_detection_log_ttl_seconds: draft.redis_detection_log_ttl_seconds,
    model_infos: normalizeModelOrder(draft.model_infos, kpi)
  };
}
function buildVerifyRequest(draft, kpi) {
  const { tag, snapshots_at_tag } = computeTags(draft.model_infos);
  return {
    camera_id: draft.camera_id.trim(),
    location_id: draft.location_id.trim(),
    kpi_name: draft.kpi_name,
    url_type: draft.url_type,
    url: draft.url.trim(),
    tag,
    regions: draft.regions,
    snapshots_at_tag,
    model_infos: normalizeModelOrder(draft.model_infos, kpi)
  };
}
function exportJobConfig(detail) {
  return {
    job_id: detail.job_id || null,
    camera_id: detail.camera_id ?? "",
    location_id: detail.location_id ?? "",
    kpi_name: detail.kpi_name ?? "",
    url: detail.url_masked ?? "",
    url_type: detail.url_type,
    tag: detail.tag ?? "",
    regions: detail.regions ?? [],
    snapshots_at_tag: detail.snapshots_at_tag ?? "",
    tag_at_reid: "",
    is_global_reid: detail.is_global_reid ?? false,
    num_of_frame_per_sec: detail.num_of_frame_per_sec ?? 1,
    redis_job_ttl_seconds: detail.redis_job_ttl_seconds ?? 86400,
    redis_detection_log_ttl_seconds: detail.redis_detection_log_ttl_seconds ?? 300,
    model_infos: detail.model_infos ?? []
  };
}
export {
  buildRegisterRequest,
  buildVerifyRequest,
  draftFromConfig,
  emptyDraft,
  exportJobConfig,
  normalizeModelOrder
};
