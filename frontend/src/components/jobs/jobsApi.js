/**
 * Data layer for the Add Camera Job modal.
 *
 * Everything here talks to OmniTrack's OWN endpoints — the same ones the
 * Video Feeds (/detection) page uses:
 *   GET  /api/models/                     → model picker
 *   GET  /api/models/{filename}/classes   → Object Classes editor
 *   GET  /api/footage/serve/{name}        → Camera Marking preview (stored clips)
 *   POST /api/detection/start/{camera_id} → Register job
 *
 * Only two things are local constants, because OmniTrack has no endpoint for
 * them: the Activity Type list (the analytics behind these is not built yet)
 * and the tracker list (the backend takes a tracker filename, it does not
 * enumerate them — DetectionPage hardcodes the same two).
 *
 * Hook return shapes mirror react-query (`{ data: { data }, isLoading }`) so the
 * ported components did not need to change.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { detectionAPI, footageAPI, modelAPI } from "../../services/api";

/* ── Activity types ─────────────────────────────────────────────────── */

/** Region types each activity needs; drives the Camera Marking tool rail. */
export const ACTIVITY_TYPES = [
    {
        kpi_name: "line_passing",
        label: "Line passing",
        allowed_region_types: ["Line"],
        region_descriptions: {
            Line: "Counts tracked objects crossing the line in the configured direction.",
        },
    },
    {
        kpi_name: "roi_region",
        label: "ROI region",
        allowed_region_types: ["bounding_box", "polygon"],
        region_descriptions: {
            bounding_box: "Rectangular region of interest.",
            polygon: "Free-form region of interest.",
        },
    },
    {
        kpi_name: "checkout_queue",
        label: "Checkout queue",
        allowed_region_types: ["polygon", "bounding_box"],
        region_descriptions: {
            polygon:
                "One shape per till, around where customers stand. Usually the " +
                "right choice — tills rarely sit square to the camera, and a " +
                "rectangle around one lane tends to cover its neighbours.",
            bounding_box:
                "A rectangle per till. Fine when the camera looks straight down " +
                "a lane. Keep the cashier outside it, or the queue never empties.",
        },
    },
    {
        // No regions at all — the right shape for detector-only models such as
        // fire/smoke, where "is it present, and when" is the whole question.
        // VisRax likewise leaves this KPI out of its region mappings.
        kpi_name: "general_object_detection",
        label: "General object detection",
        allowed_region_types: [],
        region_descriptions: {},
    },
];

/** Same two the Video Feeds form offers; the backend takes a filename. */
const TRACKERS = [{ name: "botsort.yaml" }, { name: "bytetrack.yaml" }];

/* ── Source helpers ─────────────────────────────────────────────────── */

/**
 * Map a source string onto the `stream_type` values the backend's
 * `_resolve_source` understands (rtsp | http | file | webcam).
 */
export function inferStreamType(source) {
    const s = (source || "").trim().toLowerCase();
    if (!s) return "file";
    if (s.startsWith("rtsp://")) return "rtsp";
    if (s.startsWith("footage:")) return "file";
    if (s.startsWith("http://") || s.startsWith("https://")) return "http";
    if (/^\d{1,2}$/.test(s)) return "webcam";
    return "file";
}

/**
 * Browser-playable URL for the Camera Marking preview, or null when the source
 * cannot be shown in a <video> element (RTSP and webcam indices can't be).
 */
export function previewUrlFor(source) {
    const s = (source || "").trim();
    if (!s) return null;
    if (s.toLowerCase().startsWith("footage:")) {
        const name = s.slice("footage:".length).replace(/^\/+/, "");
        return name ? footageAPI.serveUrl(name) : null;
    }
    if (/^https?:\/\//i.test(s)) return s;
    return null;
}

/* ── Generic fetch hook ─────────────────────────────────────────────── */

function useApi(fetcher, deps, enabled = true) {
    const [state, setState] = useState({ data: null, isLoading: enabled, error: null });

    useEffect(() => {
        let alive = true;
        if (!enabled) {
            setState({ data: null, isLoading: false, error: null });
            return () => { alive = false; };
        }
        setState((s) => ({ ...s, isLoading: true }));
        fetcher()
            .then((payload) => alive && setState({ data: { data: payload }, isLoading: false, error: null }))
            .catch((error) => alive && setState({ data: null, isLoading: false, error }));
        return () => { alive = false; };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, deps);

    return { ...state, isError: Boolean(state.error) };
}

/* ── Hooks ──────────────────────────────────────────────────────────── */

/**
 * Activity types, each offering every model installed on the backend.
 * OmniTrack has no notion of pipeline stages, so everything is stage 0 and the
 * modal shows a single Stage panel.
 */
export function useKpiCatalog() {
    const fetcher = useCallback(async () => {
        const res = await modelAPI.list();
        const models = (res?.data?.models ?? []).map((m) => ({
            model_id: m.filename,
            stage: 0,
        }));
        return ACTIVITY_TYPES.map((a) => ({ ...a, models }));
    }, []);
    return useApi(fetcher, []);
}

export function useTrackers() {
    const fetcher = useCallback(async () => TRACKERS, []);
    return useApi(fetcher, []);
}

/**
 * Clips already on the backend, plus an uploader.
 *
 * The upload endpoint renames the file to camera_{id}_{epoch}_{name}.ext, so the
 * stored name is not something the user can type from memory — listing is what
 * makes `footage:<name>` usable as a job source.
 */
export function useFootage() {
    const [items, setItems] = useState([]);
    const [isLoading, setLoading] = useState(false);
    const [uploading, setUploading] = useState(false);
    const [error, setError] = useState(null);

    const refresh = useCallback(async () => {
        setLoading(true);
        try {
            const res = await footageAPI.list();
            setItems(Array.isArray(res?.data) ? res.data : []);
            setError(null);
        } catch (e) {
            setError(e?.response?.data?.detail || e.message);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { refresh(); }, [refresh]);

    const upload = useCallback(async (file, cameraId = 1) => {
        setUploading(true);
        setError(null);
        try {
            const res = await footageAPI.upload(file, Number(cameraId) || 1);
            await refresh();
            return res?.data?.filename ?? null;
        } catch (e) {
            setError(e?.response?.data?.detail || e.message);
            return null;
        } finally {
            setUploading(false);
        }
    }, [refresh]);

    return { items, isLoading, uploading, error, refresh, upload };
}

/** Classes the chosen model can detect, shaped as { object_names: {id: name} }. */
export function useModelClasses(modelId) {
    const fetcher = useCallback(async () => {
        const res = await modelAPI.classes(modelId);
        const rows = Array.isArray(res?.data) ? res.data : [];
        const object_names = {};
        for (const row of rows) object_names[row.id] = row.name;
        return { model_id: modelId, object_names };
    }, [modelId]);
    return useApi(fetcher, [modelId], Boolean(modelId));
}

/**
 * Registers the job in two steps:
 *   1. POST /api/detection/start/{id}  — starts the feed (same call Video Feeds makes)
 *   2. POST /api/detection/jobs/{id}   — attaches regions + selected classes
 *
 * Step 2 is what makes the drawn line show up on the live stream and drives the
 * in/out crossing counters; the start endpoint takes query params only and
 * cannot carry region geometry.
 */
export function useRegisterJob() {
    const [isPending, setIsPending] = useState(false);
    const [error, setError] = useState(null);

    const mutateAsync = async (request) => {
        setIsPending(true);
        setError(null);
        try {
            const cameraId = Number(request.camera_id) || 1;
            const source = (request.url || "").trim();
            const model = request.model_infos?.[0]?.model_id || undefined;
            const tracker = request.model_infos?.[0]?.track_name || "botsort.yaml";

            const zone = request.location_id?.trim() || "default";

            // `tag` is the comma-joined list of classes with Detection enabled.
            const classes = (request.tag || "")
                .split(",")
                .map((c) => c.trim())
                .filter(Boolean);

            // Register only. The job is started from the Jobs page, so it can
            // be run again when a clip ends instead of being rebuilt.
            await detectionAPI.setJobConfig(cameraId, {
                regions: request.regions || [],
                frame_width: request.frame_width || 0,
                frame_height: request.frame_height || 0,
                classes,
                activity_type: request.kpi_name,
                model,
                tracker,
                source,
                zone,
                job_id: request.job_id || `JOB-CAM${String(cameraId).padStart(2, "0")}`,
                stream_type: inferStreamType(source),
                fps: request.num_of_frame_per_sec > 1 ? request.num_of_frame_per_sec : 30,
                // Process EVERY frame. Skipping breaks tracking continuity:
                // ByteTrack loses identities across the gaps, tracks fragment, and
                // many are first seen already past the counting line — which the
                // KPI then (correctly) refuses to count. Measured on a 12.5fps
                // clip with 15 crossings: every frame -> 15, every 2nd -> 11,
                // every 6th -> 1.
                skip_frames: 0,
                enable_reid: request.enable_reid !== false,
                loop: Boolean(request.loop),
                extra_models: (request.extra_models || []).join(","),
            });

            return { data: `Camera ${cameraId}` };
        } catch (e) {
            const detail = e?.response?.data?.detail || e.message;
            const wrapped = new Error(detail);
            setError(wrapped);
            throw wrapped;
        } finally {
            setIsPending(false);
        }
    };

    return { mutateAsync, isPending, isError: Boolean(error), error };
}
