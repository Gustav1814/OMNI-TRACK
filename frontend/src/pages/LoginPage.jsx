/**
 * OmniTrack AI — Login Page
 * Calls POST /api/auth/login, persists access+refresh JWT, caches /auth/me.
 */

import React, { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { motion } from 'framer-motion';
import { ShieldCheck, Video, Activity, Sparkles } from 'lucide-react';
import { authAPI, tokenStore } from '../services/api';

export default function LoginPage({ onLogin }) {
    const [username, setUsername] = useState('');
    const [password, setPassword] = useState('');
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);
    const navigate = useNavigate();

    const handleSubmit = async (e) => {
        e.preventDefault();
        setError('');
        setLoading(true);
        try {
            const res = await authAPI.login(username.trim(), password);
            const { access_token, refresh_token } = res.data || {};
            if (!access_token) throw new Error('No access token returned');
            tokenStore.set(access_token, refresh_token);
            // Best-effort: cache the profile so the UI can show username/role.
            try {
                const me = await authAPI.me();
                tokenStore.setUser(me.data);
            } catch { /* profile cache is optional */ }
            onLogin && onLogin();
            navigate('/');
        } catch (err) {
            const status = err?.response?.status;
            const detail = err?.response?.data?.detail || err?.message;
            if (status === 401) setError('Invalid username or password.');
            else if (status === 403) setError('Account is inactive.');
            else setError(detail || 'Login failed — is the backend reachable?');
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="login-page">
            <div className="login-bg-orb login-bg-orb-1" />
            <div className="login-bg-orb login-bg-orb-2" />
            <motion.div
                className="login-card"
                initial={{ opacity: 0, y: 24, scale: 0.98 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                transition={{ duration: 0.4, ease: [0.25, 0.46, 0.45, 0.94] }}
                whileHover={{ boxShadow: '0 28px 64px rgba(0,0,0,0.55), 0 0 0 1px rgba(255,255,255,0.08), 0 0 80px -16px rgba(99,102,241,0.25)' }}
            >
                <div className="login-layout">
                    <div className="login-showcase">
                        <motion.div
                            className="login-logo"
                            initial={{ scale: 0.8, opacity: 0 }}
                            animate={{ scale: 1, opacity: 1 }}
                            transition={{ delay: 0.15, type: 'spring', stiffness: 300, damping: 20 }}
                        >
                            O
                        </motion.div>
                        <h1 className="login-title">OmniTrack</h1>
                        <p className="login-subtitle">
                            Elegant retail intelligence from live and uploaded video feeds.
                        </p>

                        <div className="login-points">
                            <div><Video size={14} /> Video-first monitoring workspace</div>
                            <div><Activity size={14} /> Real-time store pulse and traffic insights</div>
                            <div><ShieldCheck size={14} /> Secure access and audit trail</div>
                        </div>

                        <div className="login-showcase-pill">
                            <Sparkles size={14} />
                            Production-ready visual command center
                        </div>
                    </div>

                    <div className="login-form-wrap">
                        <h2 className="login-form-title">Welcome back</h2>
                        <p className="login-form-subtitle">Sign in to continue to your live dashboard.</p>

                        {error && (
                            <motion.div className="alert-banner danger" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
                                {error}
                            </motion.div>
                        )}

                        <form onSubmit={handleSubmit}>
                            <div className="form-group">
                                <label className="form-label">Username</label>
                                <input
                                    className="form-input"
                                    type="text"
                                    autoComplete="username"
                                    value={username}
                                    onChange={(e) => setUsername(e.target.value)}
                                    placeholder="Enter username"
                                    required
                                />
                            </div>
                            <div className="form-group">
                                <label className="form-label">Password</label>
                                <input
                                    className="form-input"
                                    type="password"
                                    autoComplete="current-password"
                                    value={password}
                                    onChange={(e) => setPassword(e.target.value)}
                                    placeholder="Enter password"
                                    required
                                />
                            </div>
                            <motion.button
                                className="btn btn-primary"
                                type="submit"
                                disabled={loading || !username || !password}
                                style={{ width: '100%', justifyContent: 'center', padding: '12px', marginTop: 8 }}
                                whileHover={{ scale: loading ? 1 : 1.02 }}
                                whileTap={{ scale: loading ? 1 : 0.98 }}
                            >
                                {loading ? 'Signing in…' : 'Sign In'}
                            </motion.button>
                        </form>

                        <p className="login-register-note">
                            New here?{' '}
                            <Link to="/register" style={{ color: 'var(--accent-secondary)', fontWeight: 600 }}>
                                Create an account
                            </Link>
                        </p>
                    </div>
                </div>
            </motion.div>
        </div>
    );
}
