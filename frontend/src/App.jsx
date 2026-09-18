/**
 * OmniTrack AI — App Component
 * Premium 3D dashboard: custom cursor, animated layout, React Router
 * Light/dark theme via ThemeProvider (main.jsx) and toggle in Sidebar.
 */

import React, { useState, useEffect, useCallback } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { motion } from 'framer-motion';
import { RefreshCw, Check, Radio, AlertTriangle } from 'lucide-react';
import { systemAPI } from './services/api';
import './styles/jobs-modal.css';
import './styles/jobs-page.css';
import './styles/jobs-live.css';
import './styles/captures.css';
import Sidebar from './components/Sidebar';
import Scene3D from './components/Scene3D';
// Pages
import LoginPage from './pages/LoginPage';
import RegisterPage from './pages/RegisterPage';
import DashboardPage from './pages/DashboardPage';
import DetectionPage from './pages/DetectionPage';
import ReIDPage from './pages/ReIDPage';
import ShelfPage from './pages/ShelfPage';
import FirePage from './pages/FirePage';
import CrowdPage from './pages/CrowdPage';
import CheckoutPage from './pages/CheckoutPage';
import EmotionPage from './pages/EmotionPage';
import AuditPage from './pages/AuditPage';
import VibePage from './pages/VibePage';
import PeakHoursPage from './pages/PeakHoursPage';
import DemographicsPage from './pages/DemographicsPage';
import SecurityPage from './pages/SecurityPage';
import TrimPage from './pages/TrimPage';
import JobsPage from './pages/JobsPage';

const pageTitles = {
    '/': 'Live Overview',
    '/jobs': 'Jobs',
    '/detection': 'Recordings',
    '/reid': 'Cross-Feed Matching',
    '/trim': 'Video Trimmer',
    '/shelf': 'Shelf Activity',
    '/fire': 'Safety Watch',
    '/crowd': 'Footfall',
    '/checkout': 'Queue Insights',
    '/emotion': 'Mood Trends',
    '/audit': 'Activity Log',
    '/vibe': 'Store Pulse',
    '/peak-hours': 'Rush Hours',
    '/demographics': 'Audience Mix',
    '/security': 'Model Health',
};

function TopBar() {
    const location = useLocation();
    const title = pageTitles[location.pathname] || 'OmniTrack AI';
    const [health, setHealth] = useState(null);

    const loadHealth = useCallback(async () => {
        try {
            const { data } = await systemAPI.health();
            setHealth(data);
        } catch {
            setHealth({ status: 'unreachable' });
        }
    }, []);

    useEffect(() => {
        loadHealth();
        const id = setInterval(loadHealth, 15000);
        return () => clearInterval(id);
    }, [loadHealth]);

    const cameras = health?.components?.pipeline?.cameras_active ?? 0;
    const pipelineState = health?.components?.pipeline?.state;
    const healthy = health?.status === 'healthy';

    return (
        <header className="topbar">
            <div className="topbar-left">
                <h1 className="topbar-title">{title}</h1>
                {pipelineState === 'running' && (
                    <span className="topbar-live-chip">
                        <i className="live-dot" />
                        LIVE
                    </span>
                )}
            </div>
            <div className="topbar-actions">
                <span className="topbar-pill" title="Cameras currently processing">
                    <Radio size={12} />
                    {cameras} {cameras === 1 ? 'feed' : 'feeds'} active
                </span>
                <span className={`topbar-pill ${healthy ? 'topbar-pill-good' : 'topbar-pill-bad'}`}>
                    {healthy ? <Check size={12} /> : <AlertTriangle size={12} />}
                    {healthy ? 'Healthy' : health ? 'Degraded' : 'Checking…'}
                </span>
                <button type="button" className="topbar-pill topbar-pill-button" onClick={loadHealth} title="Refresh system status">
                    <RefreshCw size={12} />
                    Refresh
                </button>
            </div>
        </header>
    );
}

function ProtectedLayout() {
    const location = useLocation();
    const isDashboard = location.pathname === '/';

    return (
        <div className="app-layout">
            {isDashboard && <Scene3D />}
            <div className="ambient-bg">
                <div className="ambient-blob ambient-blob-a" />
                <div className="ambient-blob ambient-blob-b" />
                <div className="ambient-blob ambient-blob-c" />
                <div className="ambient-grid" />
            </div>
            <Sidebar />
            <div className="main-content">
                <TopBar />
                <div className="page-content">
                    <motion.div
                        key={location.pathname}
                        initial={{ opacity: 0, y: 6 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ duration: 0.25, ease: [0.25, 0.46, 0.45, 0.94] }}
                        style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}
                    >
                        <Routes>
                            <Route path="/" element={<DashboardPage />} />
                            <Route path="/jobs" element={<JobsPage />} />
                            <Route path="/detection" element={<DetectionPage />} />
                            <Route path="/reid" element={<ReIDPage />} />
                            <Route path="/trim" element={<TrimPage />} />
                            <Route path="/shelf" element={<ShelfPage />} />
                            <Route path="/fire" element={<FirePage />} />
                            <Route path="/crowd" element={<CrowdPage />} />
                            <Route path="/checkout" element={<CheckoutPage />} />
                            <Route path="/emotion" element={<EmotionPage />} />
                            <Route path="/audit" element={<AuditPage />} />
                            <Route path="/vibe" element={<VibePage />} />
                            <Route path="/peak-hours" element={<PeakHoursPage />} />
                            <Route path="/demographics" element={<DemographicsPage />} />
                            <Route path="/security" element={<SecurityPage />} />
                            <Route path="*" element={<Navigate to="/" />} />
                        </Routes>
                    </motion.div>
                </div>
            </div>
        </div>
    );
}

export default function App() {
    const [isAuth, setIsAuth] = useState(!!localStorage.getItem('omnitrack_token'));

    // Listen for auth changes
    useEffect(() => {
        const check = () => setIsAuth(!!localStorage.getItem('omnitrack_token'));
        window.addEventListener('storage', check);
        return () => window.removeEventListener('storage', check);
    }, []);

    return (
        <BrowserRouter>
            <Routes>
                <Route path="/login" element={<LoginPage onLogin={() => setIsAuth(true)} />} />
                <Route path="/register" element={<RegisterPage onRegister={() => setIsAuth(true)} />} />
                <Route path="/*" element={isAuth ? <ProtectedLayout /> : <Navigate to="/login" />} />
            </Routes>
        </BrowserRouter>
    );
}
