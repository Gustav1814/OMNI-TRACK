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

// --- Detection ---
export const detectionAPI = {
    start: (cameraId) => api.post(`/detection/start/${cameraId}`),
    stop: (cameraId) => api.post(`/detection/stop/${cameraId}`),
    status: () => api.get('/detection/status'),
    results: (cameraId) => api.get(`/detection/results/${cameraId}`),
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

export default api;
