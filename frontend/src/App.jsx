/**
 * OmniTrack AI — App Component
 * Premium 3D dashboard: custom cursor, animated layout, React Router
 * Light/dark theme via ThemeProvider (main.jsx) and toggle in Sidebar.
 */

import React, { useState, useEffect, lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { motion } from 'framer-motion';
import { RefreshCw, PlayCircle, Check, Radio } from 'lucide-react';
import Sidebar from './components/Sidebar';
import CustomCursor from './components/CustomCursor';

// Lazy-load heavy and route-level modules to reduce initial bundle cost.
const Scene3D = lazy(() => import('./components/Scene3D'));
const LoginPage = lazy(() => import('./pages/LoginPage'));
const RegisterPage = lazy(() => import('./pages/RegisterPage'));
const DashboardPage = lazy(() => import('./pages/DashboardPage'));
const DetectionPage = lazy(() => import('./pages/DetectionPage'));
const ReIDPage = lazy(() => import('./pages/ReIDPage'));
const SynopsisPage = lazy(() => import('./pages/SynopsisPage'));
const ShelfPage = lazy(() => import('./pages/ShelfPage'));
const FirePage = lazy(() => import('./pages/FirePage'));
const CrowdPage = lazy(() => import('./pages/CrowdPage'));
const CheckoutPage = lazy(() => import('./pages/CheckoutPage'));
const EmotionPage = lazy(() => import('./pages/EmotionPage'));
const AuditPage = lazy(() => import('./pages/AuditPage'));
const VibePage = lazy(() => import('./pages/VibePage'));
const PeakHoursPage = lazy(() => import('./pages/PeakHoursPage'));
const DemographicsPage = lazy(() => import('./pages/DemographicsPage'));
const SecurityPage = lazy(() => import('./pages/SecurityPage'));
const TrimPage = lazy(() => import('./pages/TrimPage'));
const SettingsPage = lazy(() => import('./pages/SettingsPage'));

const pageTitles = {
    '/': 'Live Overview',
    '/detection': 'Video Feeds',
    '/reid': 'Cross-Feed Matching',
    '/synopsis': 'Highlights Reel',
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
    '/settings': 'Settings',
};

function TopBar() {
    const location = useLocation();
    const title = pageTitles[location.pathname] || 'OmniTrack AI';

    return (
        <motion.header
            className="topbar"
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.3 }}
        >
            <div className="topbar-left">
                <h1 className="topbar-title">{title}</h1>
                <span className="topbar-live-chip">
                    <i className="live-dot" />
                    LIVE
                </span>
            </div>
            <div className="topbar-actions">
                <motion.span className="topbar-pill" whileHover={{ y: -1 }} whileTap={{ scale: 0.97 }}>
                    <Radio size={12} />
                    Channel open
                </motion.span>
                <motion.span className="topbar-pill topbar-pill-good" whileHover={{ y: -1 }} whileTap={{ scale: 0.97 }}>
                    <Check size={12} />
                    Healthy
                </motion.span>
                <motion.button type="button" className="topbar-pill topbar-pill-button" whileHover={{ y: -1 }} whileTap={{ scale: 0.97 }}>
                    <RefreshCw size={12} />
                    Refresh
                </motion.button>
                <motion.button type="button" className="topbar-pill topbar-pill-primary" whileHover={{ y: -2 }} whileTap={{ scale: 0.97 }}>
                    <PlayCircle size={12} />
                    Start Session
                </motion.button>
            </div>
        </motion.header>
    );
}

function ProtectedLayout() {
    const location = useLocation();
    const isDashboard = location.pathname === '/';

    return (
        <div className="app-layout">
            <CustomCursor />
            {isDashboard ? (
                <Suspense fallback={null}>
                    <Scene3D />
                </Suspense>
            ) : null}
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
                        <Suspense fallback={<div className="dashboard-empty-note">Loading page...</div>}>
                            <Routes>
                                <Route path="/" element={<DashboardPage />} />
                                <Route path="/detection" element={<DetectionPage />} />
                                <Route path="/reid" element={<ReIDPage />} />
                                <Route path="/synopsis" element={<SynopsisPage />} />
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
                                <Route path="/settings" element={<SettingsPage />} />
                                <Route path="*" element={<Navigate to="/" />} />
                            </Routes>
                        </Suspense>
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
            <Suspense fallback={<div className="dashboard-empty-note">Loading...</div>}>
                <Routes>
                    <Route path="/login" element={<LoginPage onLogin={() => setIsAuth(true)} />} />
                    <Route path="/register" element={<RegisterPage onRegister={() => setIsAuth(true)} />} />
                    <Route path="/*" element={isAuth ? <ProtectedLayout /> : <Navigate to="/login" />} />
                </Routes>
            </Suspense>
        </BrowserRouter>
    );
}
