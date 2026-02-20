/**
 * OmniTrack AI — App Component
 * Root layout with React Router
 */

import React, { useState, useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import Sidebar from './components/Sidebar';

// Pages
import LoginPage from './pages/LoginPage';
import DashboardPage from './pages/DashboardPage';
import DetectionPage from './pages/DetectionPage';
import ReIDPage from './pages/ReIDPage';
import SynopsisPage from './pages/SynopsisPage';
import ShelfPage from './pages/ShelfPage';
import FirePage from './pages/FirePage';
import CrowdPage from './pages/CrowdPage';
import CheckoutPage from './pages/CheckoutPage';
import EmotionPage from './pages/EmotionPage';
import AuditPage from './pages/AuditPage';
import VibePage from './pages/VibePage';
import PeakHoursPage from './pages/PeakHoursPage';
import DemographicsPage from './pages/DemographicsPage';

const pageTitles = {
    '/': 'Dashboard',
    '/detection': 'Person Detection',
    '/reid': 'Re-Identification',
    '/synopsis': 'Video Synopsis',
    '/shelf': 'Shelf Engagement',
    '/fire': 'Fire & Smoke',
    '/crowd': 'Crowd Density',
    '/checkout': 'Checkout Analytics',
    '/emotion': 'Emotion Recognition',
    '/audit': 'Audit Log',
    '/vibe': 'Store Vibe',
    '/peak-hours': 'Peak Hours',
    '/demographics': 'Demographics',
};

function TopBar() {
    const location = useLocation();
    const title = pageTitles[location.pathname] || 'OmniTrack AI';

    return (
        <header className="topbar">
            <h1 className="topbar-title">{title}</h1>
            <div className="topbar-actions">
                <span className="topbar-badge">
                    <span className="dot"></span>
                    System Online
                </span>
            </div>
        </header>
    );
}

function ProtectedLayout() {
    return (
        <div className="app-layout">
            <Sidebar />
            <div className="main-content">
                <TopBar />
                <div className="page-content">
                    <Routes>
                        <Route path="/" element={<DashboardPage />} />
                        <Route path="/detection" element={<DetectionPage />} />
                        <Route path="/reid" element={<ReIDPage />} />
                        <Route path="/synopsis" element={<SynopsisPage />} />
                        <Route path="/shelf" element={<ShelfPage />} />
                        <Route path="/fire" element={<FirePage />} />
                        <Route path="/crowd" element={<CrowdPage />} />
                        <Route path="/checkout" element={<CheckoutPage />} />
                        <Route path="/emotion" element={<EmotionPage />} />
                        <Route path="/audit" element={<AuditPage />} />
                        <Route path="/vibe" element={<VibePage />} />
                        <Route path="/peak-hours" element={<PeakHoursPage />} />
                        <Route path="/demographics" element={<DemographicsPage />} />
                        <Route path="*" element={<Navigate to="/" />} />
                    </Routes>
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
                <Route path="/*" element={isAuth ? <ProtectedLayout /> : <Navigate to="/login" />} />
            </Routes>
        </BrowserRouter>
    );
}
