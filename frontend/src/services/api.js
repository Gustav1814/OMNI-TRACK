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
export const AUTH_CHANGE_EVENT = 'omnitrack-auth-change';

const notifyAuthChange = () => {
    if (typeof window !== 'undefined') {
        window.dispatchEvent(new Event(AUTH_CHANGE_EVENT));
    }
};

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
        notifyAuthChange();
    },
    clear: () => {
        localStorage.removeItem('omnitrack_token');
        localStorage.removeItem('omnitrack_refresh_token');
        localStorage.removeItem('omnitrack_user');
        notifyAuthChange();
    },
    setUser: (user) => {
        localStorage.setItem('omnitrack_user', JSON.stringify(user || {}));
        notifyAuthChange();
    },
    getUser: () => {
        try { return JSON.parse(localStorage.getItem('omnitrack_user') || 'null'); }
        catch { return null; }
    },
};

// ────────────────────────────────────────────────────────────────
// Interceptors (JWT attach + transparent refresh + logout on failure)
//
// Access tokens live 15 min; refresh tokens 7 days and ROTATE on every use
// (re-using an old refresh token revokes the whole session family). A 401 on
// any API call triggers ONE shared refresh (single-flight, so parallel 401s
// never race two refreshes and trip reuse detection); the failed requests are
// replayed with the new token. If refresh fails, the session is cleared and
// the user is sent to /login.
// ────────────────────────────────────────────────────────────────

const AUTH_PATHS_NO_RETRY = ['/auth/login', '/auth/register', '/auth/refresh', '/auth/logout'];
const isAuthPath = (url = '') => AUTH_PATHS_NO_RETRY.some((p) => url.includes(p));

api.interceptors.request.use((config) => {
    const token = tokenStore.get();
    if (token && !config.headers.Authorization) config.headers.Authorization = `Bearer ${token}`;
    return config;
});

let refreshInFlight = null;

/** Exchange the stored refresh token for a new pair. Shared by all callers. */
export function refreshSession() {
    if (refreshInFlight) return refreshInFlight;
    const refreshToken = tokenStore.getRefresh();
    if (!refreshToken) return Promise.reject(new Error('No refresh token'));
    refreshInFlight = axios
        .post(`${API_BASE}/auth/refresh`, { refresh_token: refreshToken }, { timeout: 15000 })
        .then(({ data }) => {
            tokenStore.set(data.access_token, data.refresh_token);
            return data.access_token;
        })
        .finally(() => { refreshInFlight = null; });
    return refreshInFlight;
}

/** Seconds until the stored access token expires (null if none / unreadable). */
export function accessTokenTtl() {
    const token = tokenStore.get();
    if (!token) return null;
    try {
        const part = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
        const { exp } = JSON.parse(atob(part.padEnd(part.length + ((4 - (part.length % 4)) % 4), '=')));
        return Number.isFinite(exp) ? exp - Date.now() / 1000 : null;
    } catch {
        return null;
    }
}

/**
 * Refresh the access token shortly BEFORE it expires so that URLs carrying
 * ?token= (MJPEG streams, video players) and WebSocket reconnects keep working.
 * Returns a stop function.
 */
export function startSessionKeeper({ marginSeconds = 120, everyMs = 30000 } = {}) {
    const tick = () => {
        const ttl = accessTokenTtl();
        if (ttl == null || ttl > marginSeconds || !tokenStore.getRefresh()) return;
        refreshSession().catch((e) => {
            if (e?.response?.status === 401) bounceToLogin();
        });
    };
    tick();
    const id = window.setInterval(tick, everyMs);
    const onVisible = () => { if (document.visibilityState === 'visible') tick(); };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
        window.clearInterval(id);
        document.removeEventListener('visibilitychange', onVisible);
    };
}

const bounceToLogin = () => {
    tokenStore.clear();
    const path = window.location.pathname;
    if (path !== '/login' && path !== '/register') window.location.href = '/login';
};

api.interceptors.response.use(
    (res) => res,
    async (err) => {
        const cfg = err.config || {};
        if (err.response?.status !== 401 || isAuthPath(cfg.url)) return Promise.reject(err);
        if (cfg._omniRetried || !tokenStore.getRefresh()) {
            bounceToLogin();
            return Promise.reject(err);
        }
        try {
            const access = await refreshSession();
            cfg._omniRetried = true;
            cfg.headers = { ...(cfg.headers || {}), Authorization: `Bearer ${access}` };
            return api(cfg);
        } catch {
            bounceToLogin();
            return Promise.reject(err);
        }
    }
);

/** Turn an axios error into a user-facing message (lockout / rate limit aware). */
export function apiErrorMessage(err, fallback = 'Request failed') {
    const res = err?.response;
    if (!res) return err?.code === 'ECONNABORTED' ? 'The server took too long to respond' : 'Cannot reach the server';
    const detail = res.data?.detail;
    const text = typeof detail === 'string'
        ? detail
        : Array.isArray(detail) ? detail.map((d) => d.msg).filter(Boolean).join('; ') : '';
    const retry = Number(res.headers?.['retry-after']);
    const wait = Number.isFinite(retry) && retry > 0
        ? ` Try again in ${retry >= 90 ? `${Math.ceil(retry / 60)} min` : `${retry} s`}.` : '';
    if (res.status === 423) return `${text || 'Account temporarily locked after repeated failed sign-ins.'}${wait}`;
    if (res.status === 429) return `${text || 'Too many requests.'}${wait}`;
    if (res.status === 403 && !text) return 'You do not have permission to do that (admin role required).';
    return text || fallback;
}

// ────────────────────────────────────────────────────────────────
// Auth
// ────────────────────────────────────────────────────────────────

export const authAPI = {
    bootstrap: () => api.get('/auth/bootstrap'),
    login: (username, password) => api.post('/auth/login', { username, password }),
    register: (data) => api.post('/auth/register', data),
    refresh: (refreshToken) => api.post('/auth/refresh', { refresh_token: refreshToken }),
    logout: (allSessions = false) => api.post('/auth/logout', {
        refresh_token: tokenStore.getRefresh() || null,
        all_sessions: allSessions,
    }),
    me: () => api.get('/auth/me'),
    changePassword: (currentPassword, newPassword) =>
        api.post('/auth/change-password', { current_password: currentPassword, new_password: newPassword }),
    // admin user management
    users: () => api.get('/auth/users'),
    createUser: (data) => api.post('/auth/users', data),
    updateUser: (id, data) => api.patch(`/auth/users/${id}`, data),
    deleteUser: (id) => api.delete(`/auth/users/${id}`),
};

/** Revoke the refresh token server-side (best effort), then clear local state. */
export async function logout({ allSessions = false } = {}) {
    try { await authAPI.logout(allSessions); } catch { /* token may already be invalid */ }
    tokenStore.clear();
}

// ────────────────────────────────────────────────────────────────
// System
// ────────────────────────────────────────────────────────────────

export const systemAPI = {
    health: () => api.get('/health'),
    securityPosture: () => api.get('/security/posture'),
    robustness: () => api.get('/security/robustness'),
    runRobustness: (params = {}) => api.post('/security/robustness/run', null, { params }),
};

// ────────────────────────────────────────────────────────────────
// Dashboard
// ────────────────────────────────────────────────────────────────

export const dashboardAPI = {
    overview: () => api.get('/dashboard/overview'),
    recentEvents: (limit = 30) => api.get('/dashboard/recent-events', { params: { limit } }),
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
        source = '0',
        stream_type = 'webcam',
        zone = 'default',
        model = null,
        models = null,
        model_mode = 'manual',
        fps = 30,
        skip_frames = 1,
        enable_reid = true,
        tracker = 'bytetrack',
        loop = undefined,
    } = {}) =>
        api.post(`/detection/start/${cameraId}`, null, {
            params: {
                source,
                // Opt-in video looping for file sources (backend default: off).
                ...(loop != null ? { loop: Boolean(loop) } : {}),
                stream_type,
                zone,
                fps,
                skip_frames,
                enable_reid,
                tracker,
                model_mode,
                ...(model ? { model } : {}),
                ...(models?.length ? { models: Array.isArray(models) ? models.join(',') : models } : {}),
            },
        }),
    stop: (cameraId) => api.post(`/detection/stop/${cameraId}`),
    status: () => api.get('/detection/status'),
    results: (cameraId) => api.get(`/detection/results/${cameraId}`),
    recordingStart: (cameraId) => api.post(`/detection/recording/start/${cameraId}`),
    recordingStop: (cameraId) => api.post(`/detection/recording/stop/${cameraId}`),
    recordingStatus: () => api.get('/detection/recording/status'),
};

// ────────────────────────────────────────────────────────────────
// Pipeline
// ────────────────────────────────────────────────────────────────

export const pipelineAPI = {
    status: () => api.get('/pipeline/status'),
    start: () => api.post('/pipeline/start'),
    stop: () => api.post('/pipeline/stop'),
    addCamera: (cameraId, source, streamType = 'webcam', zone = 'default', fps = 30, skipFrames = 1, models = null, enableReid = true, tracker = 'bytetrack', modelMode = 'manual', model = null) =>
        api.post('/pipeline/cameras/add', null, {
            params: {
                camera_id: cameraId,
                source,
                stream_type: streamType,
                zone,
                fps,
                skip_frames: skipFrames,
                enable_reid: enableReid,
                tracker,
                model_mode: modelMode,
                ...(model ? { model } : {}),
                ...(models?.length ? { models: Array.isArray(models) ? models.join(',') : models } : {}),
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
    /** Best crop of one identity (JPEG blob); 404 when none is held in memory. */
    thumb: (globalId) => api.get(`/reid/thumb/${encodeURIComponent(globalId)}`, { responseType: 'blob', timeout: 10000 }),
    calibrations: () => api.get('/reid/calibration'),
    /** body: { points: [{ image: [u, v] (0..1), floor: [x, y] (m) }], frame_wh: [w, h] } */
    saveCalibration: (cameraId, body) => api.put(`/reid/calibration/${cameraId}`, body),
    deleteCalibration: (cameraId) => api.delete(`/reid/calibration/${cameraId}`),
};

// ────────────────────────────────────────────────────────────────
// Video Synopsis
// ────────────────────────────────────────────────────────────────

export const synopsisAPI = {
    list: () => api.get('/synopsis/'),
    sources: () => api.get('/synopsis/sources'),
    generate: (cameraId, { source = undefined, compression = 10.0 } = {}) =>
        api.post('/synopsis/generate', null, {
            params: { camera_id: cameraId, compression, ...(source ? { source } : {}) },
        }),
    job: (jobId) => api.get(`/synopsis/jobs/${jobId}`),
    serveUrl: (filename) => {
        const token = tokenStore.get();
        const qs = token ? `?token=${encodeURIComponent(token)}` : '';
        return `${API_BASE}/synopsis/serve/${encodeURIComponent(filename)}${qs}`;
    },
};

// ────────────────────────────────────────────────────────────────
// Analytics
// ────────────────────────────────────────────────────────────────

// Zones (drawn on a camera snapshot; normalized 0..1 coordinates)
export const zonesAPI = {
    types: () => api.get('/zones/types'),
    list: (cameraId) => api.get('/zones', { params: cameraId != null ? { camera_id: cameraId } : {} }),
    create: (data) => api.post('/zones', data),
    update: (id, data) => api.put(`/zones/${id}`, data),
    delete: (id) => api.delete(`/zones/${id}`),
    live: (cameraId) => api.get('/zones/live', { params: cameraId != null ? { camera_id: cameraId } : {} }),
    alerts: (hours = 24) => api.get('/zones/alerts', { params: { hours } }),
    snapshot: (cameraId, { clean = false } = {}) => api.get(`/zones/cameras/${cameraId}/snapshot`, {
        responseType: 'blob', timeout: 15000, params: clean ? { clean: true } : undefined,
    }),
    heatmap: (cameraId, minutes = 60) => api.get(`/zones/cameras/${cameraId}/heatmap`, { params: { minutes } }),
};

// `hours` omitted = today (store time zone)
const win = (hours) => ({ params: hours ? { hours } : {} });

export const shelfAPI = {
    engagement: (hours) => api.get('/shelf/engagement', win(hours)),
    topZones: (hours) => api.get('/shelf/top-zones', win(hours)),
};

export const fireAPI = {
    alerts: (hours = 24) => api.get('/fire/alerts', { params: { hours } }),
    status: () => api.get('/fire/status'),
};

export const crowdAPI = {
    status: () => api.get('/crowd/status'),
    history: (zoneId, hours = 6) => api.get('/crowd/history', {
        params: { hours, ...(zoneId != null ? { zone_id: zoneId } : {}) },
    }),
};

export const checkoutAPI = {
    metrics: (hours) => api.get('/checkout/metrics', win(hours)),
    summary: (hours) => api.get('/checkout/summary', win(hours)),
};

export const emotionAPI = {
    current: (minutes = 30) => api.get('/emotion/current', { params: { minutes } }),
    sentiment: (hours = 6) => api.get('/emotion/store-sentiment', { params: { hours } }),
};

export const auditAPI = {
    logs: (limit = 50, { offset = 0, eventType = null } = {}) => api.get('/audit/logs', {
        params: { limit, offset, ...(eventType ? { event_type: eventType } : {}) },
    }),
    verify: () => api.get('/audit/verify', { timeout: 120000 }),
    status: () => api.get('/audit/status'),
};

export const evaluationAPI = {
    summary: () => api.get('/evaluation/summary'),
    result: (id) => api.get(`/evaluation/results/${encodeURIComponent(id)}`),
};

export const vibeAPI = {
    current: () => api.get('/vibe/current'),
    trend: (hours = 24) => api.get('/vibe/trend', { params: { hours } }),
};

export const demographicsAPI = {
    current: (zone, hours) => api.get('/demographics/current', { params: cleanParams({ zone, hours }) }),
};

export const peakHoursAPI = {
    today: (date) => api.get('/peak-hours/today', { params: date ? { date } : {} }),
    weekly: (days = 7) => api.get('/peak-hours/weekly', { params: { days } }),
};

// ────────────────────────────────────────────────────────────────
// Models (YOLO model management)
// ────────────────────────────────────────────────────────────────

export const modelAPI = {
    list: (includeClasses = false) => api.get('/models/', { params: { include_classes: includeClasses } }),
    classes: (modelFilename) => api.get(`/models/${modelFilename}/classes`),
    current: () => api.get('/models/current'),
    loaded: () => api.get('/models/loaded'),
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
    logAnalytics: (logFilename, bins = 4, minTrackFrames = 30) =>
        api.get(`/footage/logs/${encodeURIComponent(logFilename)}/analytics`, {
            params: { bins, min_track_frames: minTrackFrames },
        }),
    // Trim video by track ID
    trimByTrack: (logFilename, trackId, paddingFrames = 5) =>
        api.post('/footage/trim/by-track', null, {
            params: { log_filename: logFilename, track_id: trackId, padding_frames: paddingFrames },
        }),
};

// ────────────────────────────────────────────────────────────────
// Alerts centre, evidence (snapshots / clips) and data export
// ────────────────────────────────────────────────────────────────

/** Drop empty filter values so they are not sent as `?x=`. */
const cleanParams = (params = {}) => Object.fromEntries(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')
);

/** Same-origin URL carrying the current access token, for <img>/<video>/<a download>. */
const tokenUrl = (path, extra = {}) => {
    const qs = new URLSearchParams(cleanParams({ ...extra, token: tokenStore.get() || undefined })).toString();
    return `${API_BASE}${path}${qs ? `?${qs}` : ''}`;
};

export const alertsAPI = {
    /** filters: source, camera_id, zone_id, kind, acked (bool), since, until (ISO), limit, offset */
    list: (filters = {}) => api.get('/alerts', { params: cleanParams(filters) }),
    facets: (filters = {}) => api.get('/alerts/facets', { params: cleanParams(filters) }),
    ack: (source, id, note = null) =>
        api.post(`/alerts/${encodeURIComponent(source)}/${encodeURIComponent(id)}/ack`, note ? { note } : {}),
};

export const evidenceAPI = {
    /** filters: camera_id, source, kind, limit, offset */
    list: (filters = {}) => api.get('/evidence', { params: cleanParams(filters) }),
    fileUrl: (id) => tokenUrl(`/evidence/${encodeURIComponent(id)}/file`),
    thumbUrl: (id) => tokenUrl(`/evidence/${encodeURIComponent(id)}/thumb`),
};

/** Filename from a Content-Disposition header (RFC 5987 aware). */
function dispositionFilename(header) {
    if (!header) return null;
    const star = /filename\*\s*=\s*(?:UTF-8'')?([^;]+)/i.exec(header);
    if (star) {
        try { return decodeURIComponent(star[1].trim().replace(/^"|"$/g, '')); } catch { /* fall through */ }
    }
    const plain = /filename\s*=\s*"?([^";]+)"?/i.exec(header);
    return plain ? plain[1].trim() : null;
}

/** Blob error bodies hide `detail`; parse them so apiErrorMessage() works. */
async function unwrapBlobError(err) {
    const data = err?.response?.data;
    if (typeof Blob !== 'undefined' && data instanceof Blob) {
        try { err.response.data = JSON.parse(await data.text()); } catch { /* not JSON */ }
    }
    return err;
}

export const exportAPI = {
    /** Datasets with row counts and whether the caller may export them. */
    datasets: () => api.get('/export'),
    /**
     * Fetch an export with the Authorization header (so 401 refresh works) and
     * hand it to the browser as a file download. Resolves to the filename.
     */
    download: async (dataset, { format = 'csv', since, until } = {}) => {
        let res;
        try {
            res = await api.get(`/export/${encodeURIComponent(dataset)}`, {
                params: cleanParams({ format, since, until, download: true }),
                responseType: 'blob',
                timeout: 300000,
            });
        } catch (err) {
            throw await unwrapBlobError(err);
        }
        const name = dispositionFilename(res.headers?.['content-disposition'])
            || `omnitrack_${dataset}_${new Date().toISOString().slice(0, 10)}.${format}`;
        const url = URL.createObjectURL(res.data);
        const a = document.createElement('a');
        a.href = url;
        a.download = name;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 30000);
        return name;
    },
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
