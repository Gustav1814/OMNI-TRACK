/**
 * OmniTrack AI — API Service Layer
 * Centralized Axios client covering EVERY backend endpoint + WebSocket helpers.
 *
 * Token storage keys:
 *   omnitrack_token          → access JWT (added to every /api/* request)
 *   omnitrack_refresh_token  → refresh JWT
 *   omnitrack_user           → last /auth/me payload (cached for UI)
 */

import axios from 'axios';

export const API_BASE = '/api';

const api = axios.create({
    baseURL: API_BASE,
    timeout: 20000,
    headers: { 'Content-Type': 'application/json' },
});

// ────────────────────────────────────────────────────────────────
// Token helpers
// ────────────────────────────────────────────────────────────────

export const tokenStore = {
    get: () => localStorage.getItem('omnitrack_token') || '',
    getRefresh: () => localStorage.getItem('omnitrack_refresh_token') || '',
    set: (access, refresh) => {
        if (access) localStorage.setItem('omnitrack_token', access);
        if (refresh) localStorage.setItem('omnitrack_refresh_token', refresh);
    },
    clear: () => {
        localStorage.removeItem('omnitrack_token');
        localStorage.removeItem('omnitrack_refresh_token');
        localStorage.removeItem('omnitrack_user');
    },
    setUser: (user) => localStorage.setItem('omnitrack_user', JSON.stringify(user || {})),
    getUser: () => {
        try { return JSON.parse(localStorage.getItem('omnitrack_user') || 'null'); }
        catch { return null; }
    },
};

// ────────────────────────────────────────────────────────────────
// Interceptors (JWT attach + 401 bounce)
// ────────────────────────────────────────────────────────────────

api.interceptors.request.use((config) => {
    const token = tokenStore.get();
    if (token) config.headers.Authorization = `Bearer ${token}`;
    return config;
});

api.interceptors.response.use(
    (res) => res,
    (err) => {
        if (err.response?.status === 401) {
            const onLogin = window.location.pathname === '/login';
            tokenStore.clear();
            if (!onLogin) window.location.href = '/login';
        }
        return Promise.reject(err);
    }
);

// ────────────────────────────────────────────────────────────────
// Auth
// ────────────────────────────────────────────────────────────────

export const authAPI = {
    login: (username, password) => api.post('/auth/login', { username, password }),
    register: (data) => api.post('/auth/register', data),
    refresh: (refreshToken) => api.post('/auth/refresh', { refresh_token: refreshToken }),
    me: () => api.get('/auth/me'),
};

// ────────────────────────────────────────────────────────────────
// System
// ────────────────────────────────────────────────────────────────

export const systemAPI = {
    health: () => api.get('/health'),
    profile: () => api.get('/system/profile'),
    robustness: () => api.get('/security/robustness'),
    runRobustness: (params = {}) => api.post('/security/robustness/run', null, { params }),
};

// ────────────────────────────────────────────────────────────────
// Dashboard
// ────────────────────────────────────────────────────────────────

export const dashboardAPI = {
    overview: () => api.get('/dashboard/overview'),
};

// ────────────────────────────────────────────────────────────────
// Cameras
// ────────────────────────────────────────────────────────────────

export const camerasAPI = {
    list: () => api.get('/cameras/'),
    get: (id) => api.get(`/cameras/${id}`),
    create: (data) => api.post('/cameras/', data),
    update: (id, data) => api.put(`/cameras/${id}`, data),
    delete: (id) => api.delete(`/cameras/${id}`),
};

// ────────────────────────────────────────────────────────────────
// Detection (per-camera stream + recording)
// ────────────────────────────────────────────────────────────────

export const detectionAPI = {
    start: (cameraId, {
        source = '0', stream_type = 'webcam', zone = 'default', model = null, tracker = 'botsort.yaml', fps = 30, skip_frames = 1, enable_reid = true, enable_fire = false,
    } = {}) =>
        api.post(`/detection/start/${cameraId}`, null, {
            params: { source, stream_type, zone, model, tracker, fps, skip_frames, enable_reid, enable_fire },
        }),
    stop: (cameraId) => api.post(`/detection/stop/${cameraId}`),
    status: () => api.get('/detection/status'),
    results: (cameraId) => api.get(`/detection/results/${cameraId}`),
    recordingStart: (cameraId) => api.post(`/detection/recording/start/${cameraId}`),
    recordingStop: (cameraId) => api.post(`/detection/recording/stop/${cameraId}`),
    recordingStatus: () => api.get('/detection/recording/status'),
    segmentRun: (cameraId, bbox = null) => api.post('/detection/segment/run', null, { params: { camera_id: cameraId, ...(bbox ? { bbox } : {}) } }),
};

// ────────────────────────────────────────────────────────────────
// Pipeline
// ────────────────────────────────────────────────────────────────

export const pipelineAPI = {
    status: () => api.get('/pipeline/status'),
    start: () => api.post('/pipeline/start'),
    stop: () => api.post('/pipeline/stop'),
    addCamera: (cameraId, source, streamType = 'webcam', zone = 'default', fps = 30, skipFrames = 1, enableReid = true, enableFire = false) =>
        api.post('/pipeline/cameras/add', null, {
            params: {
                camera_id: cameraId, source, stream_type: streamType, zone, fps, skip_frames: skipFrames, enable_reid: enableReid, enable_fire: enableFire,
            },
        }),
    results: (cameraId) => api.get('/pipeline/results', {
        params: cameraId != null ? { camera_id: cameraId } : {},
    }),
};

// ────────────────────────────────────────────────────────────────
// Re-Identification
// ────────────────────────────────────────────────────────────────

export const reidAPI = {
    search: (data) => api.post('/reid/search', data),
    journey: (globalId) => api.get(`/reid/journey/${globalId}`),
    active: () => api.get('/reid/active'),
};

// ────────────────────────────────────────────────────────────────
// Video Synopsis
// ────────────────────────────────────────────────────────────────

export const synopsisAPI = {
    list: () => api.get('/synopsis/'),
    generate: (cameraId, { source, compression = 10.0, hours = 1 } = {}) =>
        api.post('/synopsis/generate', null, {
            params: { camera_id: cameraId, hours, compression, ...(source ? { source } : {}) },
        }),
    job: (jobId) => api.get(`/synopsis/jobs/${jobId}`),
};

// ────────────────────────────────────────────────────────────────
// Analytics
// ────────────────────────────────────────────────────────────────

export const shelfAPI = {
    engagement: () => api.get('/shelf/engagement'),
    topZones: () => api.get('/shelf/top-zones'),
    listZones: () => api.get('/shelf/zones'),
    /** @param {{ zone_id: string, zone_name: string, bbox: number[], camera_id: number }} zone */
    addZone: (zone) => api.post('/shelf/zones', zone),
};

export const fireAPI = {
    alerts: () => api.get('/fire/alerts'),
    status: () => api.get('/fire/status'),
};

export const crowdAPI = {
    status: () => api.get('/crowd/status'),
    history: (zone) => api.get(`/crowd/history/${encodeURIComponent(zone)}`),
};

export const checkoutAPI = {
    metrics: () => api.get('/checkout/metrics'),
    summary: () => api.get('/checkout/summary'),
};

export const emotionAPI = {
    current: () => api.get('/emotion/current'),
    sentiment: () => api.get('/emotion/store-sentiment'),
};

export const auditAPI = {
    logs: (limit = 50) => api.get('/audit/logs', { params: { limit } }),
    verify: () => api.get('/audit/verify'),
};

export const vibeAPI = {
    current: () => api.get('/vibe/current'),
    trend: (hours = 24) => api.get('/vibe/trend', { params: { hours } }),
};

export const demographicsAPI = {
    current: (zone) => api.get('/demographics/current', { params: zone ? { zone } : {} }),
};

export const peakHoursAPI = {
    today: (zone) => api.get('/peak-hours/today', { params: zone ? { zone } : {} }),
};

// ────────────────────────────────────────────────────────────────
// Models (YOLO model management)
// ────────────────────────────────────────────────────────────────

export const modelAPI = {
    list: () => api.get('/models/'),
    classes: (modelFilename) => api.get(`/models/${modelFilename}/classes`),
    current: () => api.get('/models/current'),
    loaded: () => api.get('/models/loaded'),
    upload: (file) => {
        const form = new FormData();
        form.append('model_file', file);
        return api.post('/models/upload', form, {
            headers: { 'Content-Type': 'multipart/form-data' },
        });
    },
    fetch: (source, filename = '') => api.post('/models/fetch', { source, ...(filename ? { filename } : {}) }),
    remove: (filename) => api.delete(`/models/${encodeURIComponent(filename)}`),
};

// ────────────────────────────────────────────────────────────────
// Footage (stored CCTV clips)
// ────────────────────────────────────────────────────────────────

export const footageAPI = {
    list: (cameraId) => api.get('/footage/list', {
        params: cameraId != null ? { camera_id: cameraId } : {},
    }),
    upload: (file, cameraId = 1) => {
        const form = new FormData();
        form.append('file', file);
        return api.post(`/footage/upload?camera_id=${cameraId}`, form, {
            headers: { 'Content-Type': 'multipart/form-data' },
        });
    },
    serveUrl: (filename) => {
        const token = tokenStore.get();
        const qs = token ? `?token=${encodeURIComponent(token)}` : '';
        return `${API_BASE}/footage/serve/${encodeURIComponent(filename)}${qs}`;
    },
    // Detection logs
    logsList: () => api.get('/footage/logs/list'),
    logGet: (logFilename) => api.get(`/footage/logs/${encodeURIComponent(logFilename)}`),
    logTracks: (logFilename) => api.get(`/footage/logs/${encodeURIComponent(logFilename)}/tracks`),
    logGlobalIds: (logFilename) => api.get(`/footage/logs/${encodeURIComponent(logFilename)}/global-ids`),
    // Trim video by track ID (single recorded video)
    trimByTrack: (logFilename, trackId, paddingFrames = 5) =>
        api.post('/footage/trim/by-track', null, {
            params: { log_filename: logFilename, track_id: trackId, padding_frames: paddingFrames },
        }),
    // Trim across all recorded videos containing a Re-ID global_id (multi-camera)
    trimByGlobalId: (globalId, paddingFrames = 5) =>
        api.post('/footage/trim/by-global-id', null, {
            params: { global_id: globalId, padding_frames: paddingFrames },
        }),
};

// ────────────────────────────────────────────────────────────────
// Live MJPEG stream (authenticated via ?token=JWT for <img src>)
// ────────────────────────────────────────────────────────────────

export const liveStreamUrl = (cameraId) => {
    const token = tokenStore.get();
    const qs = token ? `?token=${encodeURIComponent(token)}` : '';
    return `${API_BASE}/stream/camera/${cameraId}/live${qs}`;
};

// ────────────────────────────────────────────────────────────────
// WebSocket URL builders
// ────────────────────────────────────────────────────────────────

export function buildWsUrl(path = '/ws/live') {
    const token = tokenStore.get();
    const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const qs = token ? `?token=${encodeURIComponent(token)}` : '';
    return `${scheme}://${window.location.host}${path}${qs}`;
}

export default api;
