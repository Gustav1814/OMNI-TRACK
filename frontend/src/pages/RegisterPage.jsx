/**
 * OmniTrack AI — Sign up
 * POST /api/auth/register (UserCreate), then signs you in with the same password.
 */

import React, { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { motion } from 'framer-motion';
import { ShieldCheck, Video, Activity, Sparkles } from 'lucide-react';
import { authAPI, tokenStore } from '../services/api';

export default function RegisterPage({ onRegister }) {
    const [username, setUsername] = useState('');
    const [email, setEmail] = useState('');
    const [fullName, setFullName] = useState('');
    const [password, setPassword] = useState('');
    const [confirm, setConfirm] = useState('');
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);
    const navigate = useNavigate();

    const handleSubmit = async (e) => {
        e.preventDefault();
        setError('');
        if (username.trim().length < 3) {
            setError('Username must be at least 3 characters.');
            return;
        }
        if (password.length < 6) {
            setError('Password must be at least 6 characters.');
            return;
        }
        if (password !== confirm) {
            setError('Passwords do not match.');
            return;
        }
        setLoading(true);
        try {
            await authAPI.register({
                username: username.trim(),
                email: email.trim(),
                password,
                full_name: fullName.trim() || undefined,
            });
            const res = await authAPI.login(username.trim(), password);
            const { access_token, refresh_token } = res.data || {};
            if (!access_token) throw new Error('Account created but login returned no token.');
            tokenStore.set(access_token, refresh_token);
            try {
                const me = await authAPI.me();
                tokenStore.setUser(me.data);
            } catch { /* optional */ }
            onRegister && onRegister();
            navigate('/');
        } catch (err) {
            const status = err?.response?.status;
            const detail = err?.response?.data?.detail;
            if (status === 400 && typeof detail === 'string') setError(detail);
            else if (status === 422 && Array.isArray(detail)) {
                setError(detail.map((d) => d.msg || d.message || JSON.stringify(d)).join(' · '));
            } else if (status === 422 && detail) setError(String(detail));
            else setError(detail || err?.message || 'Sign up failed — is the backend and database running?');
        } finally {
            setLoading(false);
        }
    };

    const canSubmit = username.trim().length >= 3 && email.includes('@') && password.length >= 6 && password === confirm;

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
                            transition={{ delay: 0.1, type: 'spring', stiffness: 300, damping: 20 }}
                        >
                            O
                        </motion.div>
                        <h1 className="login-title">Create your workspace</h1>
                        <p className="login-subtitle">
                            Start with secure access, then track store activity from one elegant dashboard.
                        </p>

                        <div className="login-points">
                            <div><Video size={14} /> Turn uploaded videos into live feeds</div>
                            <div><Activity size={14} /> Get instant traffic and engagement insights</div>
                            <div><ShieldCheck size={14} /> Privacy-aware access and event history</div>
                        </div>

                        <div className="login-showcase-pill">
                            <Sparkles size={14} />
                            Modern monitoring experience
                        </div>
                    </div>
                    <div className="login-form-wrap">
                        <h2 className="login-form-title">Create account</h2>
                        <p className="login-form-subtitle">Set up your profile to access the dashboard.</p>

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
                                    minLength={3}
                                    maxLength={50}
                                    value={username}
                                    onChange={(e) => setUsername(e.target.value)}
                                    placeholder="At least 3 characters"
                                    required
                                />
                            </div>
                            <div className="form-group">
                                <label className="form-label">Email</label>
                                <input
                                    className="form-input"
                                    type="email"
                                    autoComplete="email"
                                    value={email}
                                    onChange={(e) => setEmail(e.target.value)}
                                    placeholder="you@store.com"
                                    required
                                />
                            </div>
                            <div className="form-group">
                                <label className="form-label">Full name (optional)</label>
                                <input
                                    className="form-input"
                                    type="text"
                                    autoComplete="name"
                                    value={fullName}
                                    onChange={(e) => setFullName(e.target.value)}
                                    placeholder="Display name"
                                />
                            </div>
                            <div className="form-group">
                                <label className="form-label">Password</label>
                                <input
                                    className="form-input"
                                    type="password"
                                    autoComplete="new-password"
                                    minLength={6}
                                    value={password}
                                    onChange={(e) => setPassword(e.target.value)}
                                    placeholder="At least 6 characters"
                                    required
                                />
                            </div>
                            <div className="form-group">
                                <label className="form-label">Confirm password</label>
                                <input
                                    className="form-input"
                                    type="password"
                                    autoComplete="new-password"
                                    value={confirm}
                                    onChange={(e) => setConfirm(e.target.value)}
                                    placeholder="Repeat password"
                                    required
                                />
                            </div>
                            <motion.button
                                className="btn btn-primary"
                                type="submit"
                                disabled={loading || !canSubmit}
                                style={{ width: '100%', justifyContent: 'center', padding: '12px', marginTop: 8 }}
                                whileHover={{ scale: loading ? 1 : 1.02 }}
                                whileTap={{ scale: loading ? 1 : 0.98 }}
                            >
                                {loading ? 'Creating account…' : 'Create account'}
                            </motion.button>
                        </form>

                        <p className="login-register-note">
                            Already have an account?{' '}
                            <Link to="/login" style={{ color: 'var(--accent-secondary)', fontWeight: 600 }}>
                                Sign in
                            </Link>
                        </p>
                    </div>
                </div>
            </motion.div>
        </div>
    );
}
