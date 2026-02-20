/**
 * OmniTrack AI — Sidebar Component
 */

import React from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import {
    LayoutDashboard, Camera, Users, Scan, Video, ShoppingBag,
    Flame, UsersRound, ShoppingCart, SmilePlus, ShieldCheck,
    Activity, TrendingUp, BarChart3, LogOut
} from 'lucide-react';

const navItems = [
    { section: 'Overview' },
    { path: '/', icon: LayoutDashboard, label: 'Dashboard' },
    { path: '/vibe', icon: Activity, label: 'Store Vibe' },
    { section: 'AI Modules' },
    { path: '/detection', icon: Scan, label: 'Detection' },
    { path: '/reid', icon: Users, label: 'Re-Identification' },
    { path: '/synopsis', icon: Video, label: 'Video Synopsis' },
    { section: 'Analytics' },
    { path: '/shelf', icon: ShoppingBag, label: 'Shelf Engagement' },
    { path: '/fire', icon: Flame, label: 'Fire & Smoke' },
    { path: '/crowd', icon: UsersRound, label: 'Crowd Density' },
    { path: '/checkout', icon: ShoppingCart, label: 'Checkout' },
    { path: '/emotion', icon: SmilePlus, label: 'Emotions' },
    { section: 'Insights' },
    { path: '/peak-hours', icon: TrendingUp, label: 'Peak Hours' },
    { path: '/demographics', icon: BarChart3, label: 'Demographics' },
    { section: 'Security' },
    { path: '/audit', icon: ShieldCheck, label: 'Audit Log' },
];

export default function Sidebar() {
    const navigate = useNavigate();
    const location = useLocation();

    const handleLogout = () => {
        localStorage.removeItem('omnitrack_token');
        navigate('/login');
    };

    return (
        <aside className="sidebar">
            <div className="sidebar-header">
                <div className="sidebar-logo">O</div>
                <div>
                    <div className="sidebar-title">OmniTrack</div>
                    <div className="sidebar-subtitle">Retail Analytics</div>
                </div>
            </div>

            <nav className="sidebar-nav">
                {navItems.map((item, i) => {
                    if (item.section) {
                        return <div key={i} className="nav-section-label">{item.section}</div>;
                    }
                    const Icon = item.icon;
                    const isActive = location.pathname === item.path;
                    return (
                        <div
                            key={item.path}
                            className={`nav-item ${isActive ? 'active' : ''}`}
                            onClick={() => navigate(item.path)}
                        >
                            <Icon size={18} />
                            <span>{item.label}</span>
                        </div>
                    );
                })}
            </nav>

            <div style={{ padding: '12px 10px', borderTop: '1px solid var(--border)' }}>
                <div className="nav-item" onClick={handleLogout}>
                    <LogOut size={18} />
                    <span>Sign Out</span>
                </div>
            </div>
        </aside>
    );
}
