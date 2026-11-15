/**
 * OmniTrack AI — app shell: sidebar, top bar, routed pages.
 * Theme (light / AMOLED dark) lives in ThemeProvider; all styling comes from styles/tokens.css.
 */

import React, { useState, useEffect, lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { RefreshCw, Play, Square, Menu } from 'lucide-react';
import Sidebar from './components/Sidebar';
import CommandPalette from './components/ui/CommandPalette';
import useLivePoll from './hooks/useLivePoll';
import useUserRole from './hooks/useUserRole';
import { pipelineAPI, systemAPI, startSessionKeeper, AUTH_CHANGE_EVENT } from './services/api';
import { useTheme } from './contexts/ThemeContext';
import HubPage from './pages/HubPage';
import { LEGACY_REDIRECTS, titleFor } from './nav';

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
const PeakHoursPage = lazy(() => import('./pages/PeakHoursPage'));
const DemographicsPage = lazy(() => import('./pages/DemographicsPage'));
const SecurityPage = lazy(() => import('./pages/SecurityPage'));
const SettingsPage = lazy(() => import('./pages/SettingsPage'));
const SetupPage = lazy(() => import('./pages/SetupPage'));
const EvaluationPage = lazy(() => import('./pages/EvaluationPage'));
const AlertsPage = lazy(() => import('./pages/AlertsPage'));
const CapturesPage = lazy(() => import('./pages/CapturesPage'));
const FloorHeatmap = lazy(() => import('./components/FloorHeatmap'));

/** Old URL → new home, keeping ?query and #hash (alert deep links use them). */
function LegacyRedirect({ to }) {
    const { search, hash } = useLocation();
    return <Navigate to={`${to}${search}${hash}`} replace />;
}

const HUBS = [
    { path: '/incidents', subtitle: 'Alerts to act on, the evidence behind them, and fire and smoke watch.',
        views: { alerts: AlertsPage, captures: CapturesPage, fire: FirePage } },
    { path: '/floor', subtitle: 'How people use the floor: where they spend time, when they come, queues and shelves.',
        views: { heatmap: FloorHeatmap, footfall: CrowdPage, 'rush-hours': PeakHoursPage, queues: CheckoutPage, shelves: ShelfPage } },
    { path: '/shoppers', subtitle: 'Mood and audience mix from faces the models can read; small samples are held back.',
        views: { mood: EmotionPage, audience: DemographicsPage } },
    { path: '/system', subtitle: 'Security posture, activity log, measured model accuracy and your account.',
        views: { security: SecurityPage, activity: AuditPage, evaluation: EvaluationPage, account: SettingsPage } },
];


function TopBar({ onMenu }) {
    const location = useLocation();
    const { canOperate } = useUserRole();
    const title = titleFor(location.pathname);
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
        } catch {
            /* status poll below reflects the real state */
        } finally {
            await refreshAll();
            setBusy(false);
        }
    };

    const healthStatus = health?.status;
    const healthDot = !healthStatus ? '' : healthStatus === 'healthy' ? 'is-ok' : healthStatus === 'degraded' ? 'is-warn' : 'is-bad';
    const wsCount = health?.components?.websocket?.active_connections;
    const running = pipeline?.state === 'running';
    const hasFeeds = Number(pipeline?.cameras?.total || 0) > 0;
    const sessionLabel = running ? 'Stop session' : 'Start session';

    return (
        <header className="topbar">
            <div className="topbar-left">
                <button type="button" className="btn btn-ghost btn-icon topbar-menu" onClick={onMenu} aria-label="Open navigation">
                    <Menu size={18} />
                </button>
                <h1 className="topbar-title">{title}</h1>
                <span className="topbar-status" title={running ? 'Analysis session is running' : 'No analysis session running'}>
                    <i className={`live-dot ${running ? 'is-live' : ''}`} aria-hidden />
                    {running ? 'Rec' : 'Idle'}
                </span>
            </div>
            <div className="topbar-actions">
                <span className="topbar-status topbar-hide-sm" title="System health">
                    <i className={`live-dot ${healthDot}`} aria-hidden />
                    {healthStatus || 'checking'}
                </span>
                {Number.isFinite(wsCount) && (
                    <span className="topbar-status topbar-hide-sm" title="Live update channels">
                        {wsCount} ch
                    </span>
                )}
                <span className="topbar-sep topbar-hide-sm" aria-hidden />
                <button type="button" className="btn btn-ghost btn-sm" onClick={refreshAll} disabled={busy} aria-label="Refresh status">
                    <RefreshCw size={14} className={busy ? 'spin' : undefined} />
                    <span className="topbar-hide-sm">Refresh</span>
                </button>
                <button
                    type="button"
                    className={`btn btn-sm ${running ? 'btn-outline' : 'btn-primary'}`}
                    onClick={togglePipeline}
                    disabled={!canOperate || busy || (!running && !hasFeeds)}
                    title={!running && !hasFeeds ? 'Add a video feed before starting a session' : sessionLabel}
                >
                    {running ? <Square size={12} /> : <Play size={12} />}
                    {sessionLabel}
                </button>
            </div>
        </header>
    );
}

function ProtectedLayout() {
    const location = useLocation();
    const { toggleTheme } = useTheme();
    const [navOpen, setNavOpen] = useState(false);

    // Keep the 15-minute access token fresh (rotating refresh token) while signed in.
    useEffect(() => startSessionKeeper(), []);
    // Close the mobile drawer on navigation.
    useEffect(() => { setNavOpen(false); }, [location.pathname]);
    useEffect(() => {
        if (!navOpen) return undefined;
        const onKey = (e) => { if (e.key === 'Escape') setNavOpen(false); };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [navOpen]);

    const handleCommand = async (action) => {
        if (action === 'refresh') await Promise.allSettled([systemAPI.health(), pipelineAPI.status()]);
        if (action === 'start-session') await pipelineAPI.start().catch(() => {});
        if (action === 'stop-session') await pipelineAPI.stop().catch(() => {});
        if (action === 'theme') toggleTheme();
    };

    return (
        <div className={`app-layout${navOpen ? ' nav-open' : ''}`}>
            <Sidebar />
            <button type="button" className="sidebar-scrim" aria-label="Close navigation" onClick={() => setNavOpen(false)} tabIndex={navOpen ? 0 : -1} />
            <CommandPalette onAction={handleCommand} />
            <div className="main-content">
                <TopBar onMenu={() => setNavOpen(true)} />
                <main className="page-content">
                    <div className="page-scroll">
                        <div key={location.pathname} className="page-frame">
                            <Suspense fallback={<div className="app-loading osd">Loading</div>}>
                                <Routes>
                                    <Route path="/" element={<DashboardPage />} />
                                    <Route path="/detection" element={<DetectionPage />} />
                                    <Route path="/reid" element={<ReIDPage />} />
                                    <Route path="/synopsis" element={<SynopsisPage />} />
                                    <Route path="/setup/*" element={<SetupPage />} />
                                    {HUBS.map((h) => (
                                        <React.Fragment key={h.path}>
                                            <Route path={h.path} element={<HubPage {...h} />} />
                                            <Route path={`${h.path}/:section`} element={<HubPage {...h} />} />
                                        </React.Fragment>
                                    ))}
                                    {Object.entries(LEGACY_REDIRECTS).map(([from, to]) => (
                                        <Route key={from} path={from} element={<LegacyRedirect to={to} />} />
                                    ))}
                                    <Route path="*" element={<Navigate to="/" />} />
                                </Routes>
                            </Suspense>
                        </div>
                    </div>
                </main>
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
            <Suspense fallback={<div className="app-loading osd">Loading</div>}>
                <Routes>
                    <Route path="/login" element={<LoginPage onLogin={() => setIsAuth(true)} />} />
                    <Route path="/register" element={<RegisterPage onRegister={() => setIsAuth(true)} />} />
                    <Route path="/*" element={isAuth ? <ProtectedLayout /> : <Navigate to="/login" />} />
                </Routes>
            </Suspense>
        </BrowserRouter>
    );
}
