/**
 * OmniTrack AI — Premium Sidebar
 * Animated nav items, hover glow, smooth transitions.
 */

import React from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { motion } from 'framer-motion';
import {
    LayoutDashboard, Users, Scan,
    Flame, UsersRound,
    LogOut, Sun, Moon, Scissors
} from 'lucide-react';
import { useTheme } from '../contexts/ThemeContext';
import { tokenStore } from '../services/api';

const navItems = [
    { section: 'Home' },
    { path: '/', icon: LayoutDashboard, label: 'Live Overview' },
    { section: 'Video Workspace' },
    { path: '/detection', icon: Scan, label: 'Video Feeds' },
    { path: '/reid', icon: Users, label: 'Cross-Feed Match' },
    { path: '/trim', icon: Scissors, label: 'Video Trimmer' },
    { section: 'Safety & Analytics' },
    { path: '/fire', icon: Flame, label: 'Safety Watch' },
    { path: '/crowd', icon: UsersRound, label: 'Footfall' },
];

export default function Sidebar() {
    const navigate = useNavigate();
    const location = useLocation();
    const { theme, toggleTheme } = useTheme();

    const user = tokenStore.getUser();

    const handleLogout = () => {
        tokenStore.clear();
        navigate('/login');
    };

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
                {navItems.map((item, i) => {
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
                <motion.button
                    type="button"
                    className="sidebar-logout"
                    onClick={handleLogout}
                    whileHover={{ x: 2 }}
                    whileTap={{ scale: 0.98 }}
                    title={user?.username ? `Sign out @${user.username}` : 'Sign out'}
                >
                    <LogOut size={15} />
                    <span>Sign out</span>
                </motion.button>
            </div>
        </motion.aside>
    );
}
