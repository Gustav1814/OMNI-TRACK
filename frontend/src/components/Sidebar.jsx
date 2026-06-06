/**
 * OmniTrack AI — Premium Sidebar
 * Animated nav items, hover glow, smooth transitions.
 */

import React from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { motion } from 'framer-motion';
import {
    LayoutDashboard, Users, Scan, Video, ShoppingBag,
    Flame, UsersRound, ShoppingCart,
    Activity, TrendingUp, BarChart3, Settings, Smile, ShieldCheck, ClipboardList,
    SlidersHorizontal, Store
} from 'lucide-react';
import { useTheme } from '../contexts/ThemeContext';
import { tokenStore } from '../services/api';
import useUserRole from '../hooks/useUserRole';

const navItems = [
    { section: 'Command Center' },
    { path: '/', icon: LayoutDashboard, label: 'Live Overview', roles: ['admin', 'operator', 'viewer'] },
    { path: '/vibe', icon: Activity, label: 'Store Pulse', roles: ['admin', 'operator', 'viewer'] },
    { path: '/peak-hours', icon: TrendingUp, label: 'Rush Hours', roles: ['admin', 'operator', 'viewer'] },
    { section: 'Operations' },
    { path: '/detection', icon: Scan, label: 'Video Feeds', roles: ['admin', 'operator', 'viewer'] },
    { path: '/reid', icon: Users, label: 'Cross-Feed Match', roles: ['admin', 'operator', 'viewer'] },
    { path: '/synopsis', icon: Video, label: 'Highlights Reel', roles: ['admin', 'operator', 'viewer'] },
    { section: 'Analytics' },
    { path: '/shelf', icon: ShoppingBag, label: 'Shelf Activity', roles: ['admin', 'operator', 'viewer'] },
    { path: '/crowd', icon: UsersRound, label: 'Footfall', roles: ['admin', 'operator', 'viewer'] },
    { path: '/checkout', icon: ShoppingCart, label: 'Queue Insights', roles: ['admin', 'operator', 'viewer'] },
    { path: '/emotion', icon: Smile, label: 'Mood Trends', roles: ['admin', 'operator', 'viewer'] },
    { path: '/demographics', icon: BarChart3, label: 'Audience Mix', roles: ['admin', 'operator', 'viewer'] },
    { path: '/fire', icon: Flame, label: 'Safety Watch', roles: ['admin', 'operator', 'viewer'] },
    { section: 'Smart Commerce' },
    { path: '/humanless', icon: Store, label: 'Smart Store', roles: ['admin', 'operator', 'viewer'] },
    { section: 'Administration' },
    { path: '/setup', icon: SlidersHorizontal, label: 'Store Setup', roles: ['admin', 'operator'] },
    { path: '/audit', icon: ClipboardList, label: 'Activity Log', roles: ['admin', 'operator'] },
    { path: '/security', icon: ShieldCheck, label: 'Model Health', roles: ['admin', 'operator'] },
    { path: '/settings', icon: Settings, label: 'Appearance', roles: ['admin', 'operator', 'viewer'] },
];

function visibleNavItems(role) {
    return navItems.reduce((items, item, index) => {
        if (item.section) {
            const hasVisibleChild = navItems
                .slice(index + 1)
                .some((candidate) => candidate.section || candidate.roles?.includes(role));
            if (hasVisibleChild) items.push(item);
            return items;
        }
        if (item.roles?.includes(role)) items.push(item);
        return items;
    }, []);
}

export default function Sidebar() {
    const navigate = useNavigate();
    const location = useLocation();
    const { theme, toggleTheme } = useTheme();
    const { role } = useUserRole();

    const user = tokenStore.getUser();
    const filteredNavItems = visibleNavItems(role);

    return (
        <motion.aside
            className="sidebar"
            initial={{ x: -20, opacity: 0 }}
            animate={{ x: 0, opacity: 1 }}
            transition={{ duration: 0.35, ease: [0.25, 0.46, 0.45, 0.94] }}
        >
            <div className="sidebar-header">
                <motion.div
                    className="sidebar-logo"
                    whileHover={{ scale: 1.03 }}
                    whileTap={{ scale: 0.98 }}
                    transition={{ type: 'spring', stiffness: 400, damping: 25 }}
                >
                    OT
                </motion.div>
                <div>
                    <div className="sidebar-title">OmniTrack</div>
                    <div className="sidebar-subtitle">
                        {user?.username ? `@${user.username}` : 'Retail Intelligence'}
                    </div>
                </div>
            </div>

            <nav className="sidebar-nav">
                {filteredNavItems.map((item, i) => {
                    if (item.section) {
                        return (
                            <motion.div
                                key={i}
                                className="nav-section-label"
                                initial={{ opacity: 0 }}
                                animate={{ opacity: 1 }}
                                transition={{ delay: i * 0.02 }}
                            >
                                {item.section}
                            </motion.div>
                        );
                    }
                    const Icon = item.icon;
                    const isActive = location.pathname === item.path;
                    return (
                        <motion.div
                            key={item.path}
                            className={`nav-item ${isActive ? 'active' : ''}`}
                            onClick={() => navigate(item.path)}
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            transition={{ delay: i * 0.02 }}
                            whileHover={{ x: 2 }}
                            whileTap={{ scale: 0.99 }}
                        >
                            <Icon size={15} />
                            <span>{item.label}</span>
                        </motion.div>
                    );
                })}
            </nav>

            <div className="sidebar-footer">
                <motion.button type="button" className="theme-toggle" onClick={toggleTheme} whileTap={{ scale: 0.99 }} aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}>
                    <span className={`theme-toggle-track ${theme === 'dark' ? 'dark' : 'light'}`}>
                        <motion.span className="theme-toggle-knob" layout transition={{ type: 'spring', stiffness: 420, damping: 30 }} />
                    </span>
                    <span className="theme-toggle-text">{theme === 'dark' ? 'Dark' : 'Light'}</span>
                </motion.button>
            </div>
        </motion.aside>
    );
}
