/**
 * OmniTrack AI — App Component
 * Premium 3D dashboard: custom cursor, animated layout, React Router
 * Light/dark theme via ThemeProvider (main.jsx) and toggle in Sidebar.
 */

import React, { useState, useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { motion } from 'framer-motion';
import { RefreshCw, PlayCircle, Check, Radio } from 'lucide-react';
import Sidebar from './components/Sidebar';
import CustomCursor from './components/CustomCursor';
import Scene3D from './components/Scene3D';
// Pages
import LoginPage from './pages/LoginPage';
import RegisterPage from './pages/RegisterPage';
import DashboardPage from './pages/DashboardPage';
import DetectionPage from './pages/DetectionPage';
import ReIDPage from './pages/ReIDPage';
import FirePage from './pages/FirePage';
import CrowdPage from './pages/CrowdPage';
import EmotionPage from './pages/EmotionPage';
import AuditPage from './pages/AuditPage';
import SecurityPage from './pages/SecurityPage';
import TrimPage from './pages/TrimPage';

const pageTitles = {
    '/': 'Live Overview',
    '/detection': 'Video Feeds',
    '/reid': 'Cross-Feed Matching',
    '/trim': 'Video Trimmer',
    '/fire': 'Safety Watch',
    '/crowd': 'Footfall',
    '/emotion': 'Mood Trends',
    '/audit': 'Activity Log',
    '/security': 'Model Health',
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
                        className="page-route-outlet"
                        initial={{ opacity: 0, y: 4 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ duration: 0.22, ease: [0.25, 0.46, 0.45, 0.94] }}
                    >
                        <Routes>
                            <Route path="/" element={<DashboardPage />} />
                            <Route path="/detection" element={<DetectionPage />} />
                            <Route path="/reid" element={<ReIDPage />} />
                            <Route path="/trim" element={<TrimPage />} />
                            <Route path="/fire" element={<FirePage />} />
                            <Route path="/crowd" element={<CrowdPage />} />
                            <Route path="/emotion" element={<EmotionPage />} />
                            <Route path="/audit" element={<AuditPage />} />
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
