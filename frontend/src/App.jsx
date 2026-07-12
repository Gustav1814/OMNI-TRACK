/**
 * OmniTrack AI — App Component
 * Premium dashboard: custom cursor, animated layout, React Router
 * Light/dark theme via ThemeProvider (main.jsx) and toggle in Sidebar.
 */

import React, { useState, useEffect, lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { motion } from 'framer-motion';
import { RefreshCw, PlayCircle, Check, Radio, StopCircle } from 'lucide-react';
import Sidebar from './components/Sidebar';
import CommandPalette from './components/ui/CommandPalette';
import useLivePoll from './hooks/useLivePoll';
import useUserRole from './hooks/useUserRole';
import { pipelineAPI, systemAPI, AUTH_CHANGE_EVENT } from './services/api';
import { useTheme } from './contexts/ThemeContext';

// Lazy-load heavy and route-level modules to reduce initial bundle cost.
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
const LicensePlatePage = lazy(() => import('./pages/LicensePlatePage'));
const SecurityPage = lazy(() => import('./pages/SecurityPage'));
const SettingsPage = lazy(() => import('./pages/SettingsPage'));
const SetupPage = lazy(() => import('./pages/SetupPage'));
const HumanlessStorePage = lazy(() => import('./pages/HumanlessStorePage'));

const pageTitles = {
    '/': 'Live Overview',
    '/detection': 'Video Feeds',
    '/reid': 'Cross-Feed Matching',
    '/synopsis': 'Highlights Reel',
    '/trim': 'Highlights Reel',
    '/shelf': 'Shelf Activity',
    '/fire': 'Safety Watch',
    '/crowd': 'Footfall',
    '/checkout': 'Queue Insights',
    '/license-plate': 'License Plate',
    '/emotion': 'Mood Trends',
    '/audit': 'Activity Log',
    '/vibe': 'Store Pulse',
    '/peak-hours': 'Rush Hours',
    '/demographics': 'Audience Mix',
    '/security': 'Model Health',
    '/settings': 'Settings',
    '/setup': 'Store Setup',
    '/setup/products': 'Store Setup',
    '/setup/zones': 'Store Setup',
    '/setup/cameras': 'Store Setup',
    '/setup/models': 'Store Setup',
    '/setup/integrations': 'Store Setup',
    '/setup/users': 'Store Setup',
    '/humanless': 'Smart Store',
};

function TopBar() {
    const location = useLocation();
    const { canOperate } = useUserRole();
    const title = pageTitles[location.pathname] || 'OmniTrack AI';
    const { data: health, refresh: refreshHealth } = useLivePoll(() => systemAPI.health(), { intervalMs: 10000 });
    const { data: pipeline, refresh: refreshPipeline } = useLivePoll(() => pipelineAPI.status(), { intervalMs: 5000 });
    const [busy, setBusy] = useState(false);

    const refreshAll = async () => {
        await Promise.all([refreshHealth(), refreshPipeline()]);
    };

    const togglePipeline = async () => {
        if (!canOperate || busy) return;
        setBusy(true);
        try {
            if (pipeline?.state === 'running') await pipelineAPI.stop();
            else await pipelineAPI.start();
            await refreshAll();
        } catch {
            await refreshAll();
        } finally {
            setBusy(false);
        }
    };

    const healthOk = health?.status === 'healthy';
    const wsCount = health?.components?.websocket?.active_connections ?? 0;
    const running = pipeline?.state === 'running';
    const hasFeeds = Number(pipeline?.cameras?.total || 0) > 0;
    const sessionLabel = running ? 'Stop Session' : 'Start Session';

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
                    {wsCount} channel{wsCount === 1 ? '' : 's'}
                </motion.span>
                <motion.span className={`topbar-pill ${healthOk ? 'topbar-pill-good' : ''}`} whileHover={{ y: -1 }} whileTap={{ scale: 0.97 }}>
                    <Check size={12} />
                    {health?.status || 'Checking'}
                </motion.span>
                <motion.button type="button" className="topbar-pill topbar-pill-button" onClick={refreshAll} disabled={busy} whileHover={{ y: -1 }} whileTap={{ scale: 0.97 }}>
                    <RefreshCw size={12} />
                    Refresh
                </motion.button>
                <motion.button
                    type="button"
                    className="topbar-pill topbar-pill-primary"
                    onClick={togglePipeline}
                    disabled={!canOperate || busy || (!running && !hasFeeds)}
                    title={!running && !hasFeeds ? 'Add a video feed before starting the session' : sessionLabel}
                    whileHover={{ y: -2 }}
                    whileTap={{ scale: 0.97 }}
                >
                    {running ? <StopCircle size={12} /> : <PlayCircle size={12} />}
                    {sessionLabel}
                </motion.button>
            </div>
        </motion.header>
    );
}

function ProtectedLayout() {
    const location = useLocation();
    const { toggleTheme } = useTheme();

    const handleCommand = async (action) => {
        if (action === 'refresh') await Promise.allSettled([systemAPI.health(), pipelineAPI.status()]);
        if (action === 'start-session') await pipelineAPI.start().catch(() => {});
        if (action === 'stop-session') await pipelineAPI.stop().catch(() => {});
        if (action === 'theme') toggleTheme();
    };

    return (
        <div className="app-layout">
            <div className="ambient-bg" aria-hidden>
                <div className="ambient-blob ambient-blob-a" />
                <div className="ambient-blob ambient-blob-b" />
                <div className="ambient-blob ambient-blob-c" />
                <div className="ambient-sheen" />
                <div className="ambient-mesh ambient-mesh-fine" />
                <div className="ambient-mesh ambient-mesh-wide" />
                <div className="ambient-vignette" />
            </div>
            <Sidebar />
            <CommandPalette onAction={handleCommand} />
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
                                <Route path="/trim" element={<Navigate to="/synopsis" replace />} />
                                <Route path="/shelf" element={<ShelfPage />} />
                                <Route path="/fire" element={<FirePage />} />
                                <Route path="/crowd" element={<CrowdPage />} />
                                <Route path="/checkout" element={<CheckoutPage />} />
                                <Route path="/license-plate" element={<LicensePlatePage />} />
                                <Route path="/emotion" element={<EmotionPage />} />
                                <Route path="/audit" element={<AuditPage />} />
                                <Route path="/vibe" element={<VibePage />} />
                                <Route path="/peak-hours" element={<PeakHoursPage />} />
                                <Route path="/demographics" element={<DemographicsPage />} />
                                <Route path="/humanless" element={<HumanlessStorePage />} />
                                <Route path="/security" element={<SecurityPage />} />
                                <Route path="/setup/*" element={<SetupPage />} />
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
        window.addEventListener(AUTH_CHANGE_EVENT, check);
        return () => {
            window.removeEventListener('storage', check);
            window.removeEventListener(AUTH_CHANGE_EVENT, check);
        };
    }, []);

    return (
        <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
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
