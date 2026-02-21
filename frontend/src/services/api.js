/**
 * OmniTrack AI — API Service Layer
 * Centralized API client with JWT auth interceptors
 */

import axios from 'axios';

const API_BASE = '/api';

const api = axios.create({
    baseURL: API_BASE,
    timeout: 15000,
    headers: { 'Content-Type': 'application/json' },
});

// Request interceptor: attach JWT
api.interceptors.request.use((config) => {
    const token = localStorage.getItem('omnitrack_token');
    if (token) {
        config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
});

// Response interceptor: handle 401
api.interceptors.response.use(
    (res) => res,
    (err) => {
        if (err.response?.status === 401) {
            localStorage.removeItem('omnitrack_token');
            window.location.href = '/login';
        }
        return Promise.reject(err);
    }
);

// --- Auth ---
export const authAPI = {
    login: (data) => api.post('/auth/login', data),
    register: (data) => api.post('/auth/register', data),
    me: () => api.get('/auth/me'),
};

// --- Dashboard ---
export const dashboardAPI = {
    overview: () => api.get('/dashboard/overview'),
};

// --- Cameras ---
export const camerasAPI = {
    list: () => api.get('/cameras/'),
    get: (id) => api.get(`/cameras/${id}`),
    create: (data) => api.post('/cameras/', data),
    update: (id, data) => api.put(`/cameras/${id}`, data),
    delete: (id) => api.delete(`/cameras/${id}`),
};

// --- Detection (wired to pipeline: real YOLO + ByteTrack on camera feeds) ---
export const detectionAPI = {
    start: (cameraId, params = {}) =>
        api.post(`/detection/start/${cameraId}`, null, { params: { source: params.source ?? '0', stream_type: params.stream_type ?? 'webcam', zone: params.zone ?? 'default' } }),
    stop: (cameraId) => api.post(`/detection/stop/${cameraId}`),
    status: () => api.get('/detection/status'),
    results: (cameraId) => api.get(`/detection/results/${cameraId}`),
    recordingStart: (cameraId) => api.post(`/detection/recording/start/${cameraId}`),
    recordingStop: (cameraId) => api.post(`/detection/recording/stop/${cameraId}`),
    recordingStatus: () => api.get('/detection/recording/status'),
};

// --- Pipeline (multi-camera processing: add cameras, start/stop, real results) ---
export const pipelineAPI = {
    status: () => api.get('/pipeline/status'),
    start: () => api.post('/pipeline/start'),
    stop: () => api.post('/pipeline/stop'),
    addCamera: (cameraId, source, streamType = 'webcam', zone = 'default') =>
        api.post('/pipeline/cameras/add', null, { params: { camera_id: cameraId, source, stream_type: streamType, zone } }),
    results: (cameraId) => api.get('/pipeline/results', { params: cameraId != null ? { camera_id: cameraId } : {} }),
};

// --- Re-ID ---
export const reidAPI = {
    search: (data) => api.post('/reid/search', data),
    journey: (globalId) => api.get(`/reid/journey/${globalId}`),
    active: () => api.get('/reid/active'),
};

// --- Analytics ---
export const synopsisAPI = {
    list: () => api.get('/synopsis/'),
    generate: (cameraId) => api.post(`/synopsis/generate?camera_id=${cameraId}`),
};

export const shelfAPI = {
    engagement: () => api.get('/shelf/engagement'),
    topZones: () => api.get('/shelf/top-zones'),
};

export const fireAPI = {
    alerts: () => api.get('/fire/alerts'),
    status: () => api.get('/fire/status'),
};

export const crowdAPI = {
    status: () => api.get('/crowd/status'),
    history: (zone) => api.get(`/crowd/history/${zone}`),
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
    logs: () => api.get('/audit/logs'),
    verify: () => api.get('/audit/verify'),
};

export const vibeAPI = {
    current: () => api.get('/vibe/current'),
    trend: () => api.get('/vibe/trend'),
};

export const demographicsAPI = {
    current: () => api.get('/demographics/current'),
};

export const peakHoursAPI = {
    today: () => api.get('/peak-hours/today'),
};

// --- Footage (CCTV storage: list, upload, serve for playback) ---
export const footageAPI = {
    list: (cameraId) => api.get('/footage/list', { params: cameraId != null ? { camera_id: cameraId } : {} }),
    upload: (file, cameraId = 1) => {
        const form = new FormData();
        form.append('file', file);
        return api.post(`/footage/upload?camera_id=${cameraId}`, form, {
            headers: { 'Content-Type': 'multipart/form-data' },
        });
    },
    /** URL to stream a stored clip (append ?token= for auth). Use same origin so proxy works. */
    serveUrl: (filename) => `${api.defaults.baseURL || '/api'}/footage/serve/${encodeURIComponent(filename)}`,
};

/** Base URL for API (for stream URLs that need token in query). */
export const API_BASE = api.defaults.baseURL || '/api';

export default api;
