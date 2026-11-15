/**
 * OmniTrack AI — sidebar: brand mark, role-filtered navigation, theme switch, sign out.
 */

import React, { useEffect, useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { LogOut, Sun, Moon } from 'lucide-react';
import { useTheme } from '../contexts/ThemeContext';
import { alertsAPI, logout, tokenStore } from '../services/api';
import useUserRole from '../hooks/useUserRole';
import useLivePoll from '../hooks/useLivePoll';
import { ALERTS_CHANGED_EVENT } from './alerts/alertFormat';
import { visibleNav } from '../nav';

/** Brand mark: viewfinder corners around a signal dot. */
function BrandMark() {
    return (
        <svg viewBox="0 0 32 32" aria-hidden focusable="false">
            <path fill="currentColor" d="M4 4h8v3H7v5H4zM28 4v8h-3V7h-5V4zM4 28v-8h3v5h5v3zM28 28h-8v-3h5v-5h3z" />
            <circle cx="16" cy="16" r="4.5" fill="var(--accent-fill)" />
        </svg>
    );
}


const ALERT_BADGE_POLL_MS = 30000;

/** Unacknowledged alert count for the nav badge; stops polling if the server has no alerts API. */
function useUnackedAlerts() {
    const [unsupported, setUnsupported] = useState(false);
    const { data, error, refresh } = useLivePoll(() => alertsAPI.facets(), {
        intervalMs: ALERT_BADGE_POLL_MS,
        enabled: !unsupported,
    });
    useEffect(() => {
        const status = error?.response?.status;
        if (status === 404 || status === 405) setUnsupported(true);
    }, [error]);
    useEffect(() => {
        const onChange = () => refresh();
        window.addEventListener(ALERTS_CHANGED_EVENT, onChange);
        return () => window.removeEventListener(ALERTS_CHANGED_EVENT, onChange);
    }, [refresh]);
    const n = Number(data?.unacked);
    return !unsupported && !error && Number.isFinite(n) ? n : null;
}


export default function Sidebar() {
    const navigate = useNavigate();
    const location = useLocation();
    const { theme, setTheme } = useTheme();
    const { role } = useUserRole();

    const user = tokenStore.getUser();
    const filteredNavItems = visibleNav(role);
    const badges = { unackedAlerts: useUnackedAlerts() };

    return (
        <aside className="sidebar" aria-label="Primary">
            <div className="sidebar-header">
                <div className="sidebar-logo"><BrandMark /></div>
                <div>
                    <div className="sidebar-title">OmniTrack</div>
                    <div className="sidebar-subtitle">
                        {user?.username ? `@${user.username}` : 'Retail intelligence'}
                    </div>
                </div>
            </div>

            <nav className="sidebar-nav">
                {filteredNavItems.map((item) => {
                    if (item.section) {
                        return <div key={`s-${item.section}`} className="nav-section-label">{item.section}</div>;
                    }
                    const Icon = item.icon;
                    const isActive = location.pathname === item.path
                        || (item.path !== '/' && location.pathname.startsWith(`${item.path}/`));
                    const badge = item.badge ? badges[item.badge] : null;
                    return (
                        <button
                            key={item.path}
                            type="button"
                            className={`nav-item${isActive ? ' active' : ''}`}
                            onClick={() => navigate(item.path)}
                            aria-current={isActive ? 'page' : undefined}
                        >
                            <Icon size={16} strokeWidth={1.75} />
                            <span>{item.label}</span>
                            {badge > 0 && (
                                <span className="nav-item-badge" title={`${badge} unacknowledged alert${badge === 1 ? '' : 's'}`}>
                                    {badge > 99 ? '99+' : badge}
                                </span>
                            )}
                        </button>
                    );
                })}
            </nav>

            <div className="sidebar-footer">
                <div className="theme-toggle" role="group" aria-label="Colour theme">
                    <button type="button" aria-pressed={theme === 'light'} onClick={() => setTheme('light')}>
                        <Sun size={13} /> Light
                    </button>
                    <button type="button" aria-pressed={theme === 'dark'} onClick={() => setTheme('dark')}>
                        <Moon size={13} /> Dark
                    </button>
                </div>
                <button
                    type="button"
                    className="nav-item"
                    onClick={async () => { await logout(); navigate('/login'); }}
                >
                    <LogOut size={16} strokeWidth={1.75} />
                    <span>Sign out</span>
                </button>
            </div>
        </aside>
    );
}
