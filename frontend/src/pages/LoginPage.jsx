/**
 * OmniTrack AI — Login Page
 * Calls POST /api/auth/login, persists access+refresh JWT, caches /auth/me.
 */

import React, { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { apiErrorMessage, authAPI, tokenStore } from '../services/api';

const CAPABILITIES = [
    'Person detection and tracking',
    'Cross-camera re-identification',
    'Zones, queues and dwell',
    'Fire and smoke detection',
    'Alerts with evidence clips',
    'Video synopsis',
];

/** Static "viewfinder" brand panel shared by sign-in and sign-up. No live data. */
export function AuthBrand() {
    return (
        <aside className="auth-brand vf vf--on-media" aria-label="OmniTrack">
            <div className="auth-thirds" aria-hidden="true"><i /><i /><i /><i /></div>
            <div className="osd-strip" aria-hidden="true">
                <span><i className="auth-osd-mark" /><strong>OmniTrack</strong></span>
                <span>Retail video analytics</span>
            </div>

            <div className="auth-hero">
                <svg className="auth-mark" viewBox="0 0 32 32" aria-hidden="true">
                    <path fill="currentColor" d="M4 4h8v3H7v5H4zM28 4v8h-3V7h-5V4zM4 28v-8h3v5h5v3zM28 28h-8v-3h5v-5h3z" />
                    <circle cx="16" cy="16" r="4.5" fill="var(--accent-fill)" />
                </svg>
                <div>
                    <p className="auth-wordmark">OmniTrack</p>
                </div>
                <p className="auth-tagline">
                    Store analytics from the cameras you already run: who comes in, where they go, how long they wait.
                </p>
            </div>

            <div>
                <ul className="auth-caps">
                    {CAPABILITIES.map((c, i) => (
                        <li key={c}>
                            <span className="osd">{String(i + 1).padStart(2, '0')}</span>
                            {c}
                        </li>
                    ))}
                </ul>
                <div className="osd-strip auth-foot-strip">
                    <span>JWT sessions</span>
                    <span>AES-256-GCM at rest</span>
                    <span>HMAC audit chain</span>
                </div>
            </div>
        </aside>
    );
}

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
            if (status === 401) setError('Invalid username or password.');
            else if (status === 403) setError('Account is inactive.');
            else setError(apiErrorMessage(err, 'Login failed — is the backend reachable?'));
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="auth">
            <AuthBrand />
            <main className="auth-main">
                <div className="auth-card">
                    <h1 className="auth-title">Sign in</h1>
                    <p className="auth-sub">Use the account your administrator gave you.</p>

                    {error && <div className="alert-banner danger" role="alert">{error}</div>}

                    <form className="auth-form" onSubmit={handleSubmit}>
                        <div className="form-group">
                            <label className="form-label" htmlFor="login-username">Username</label>
                            <input
                                id="login-username"
                                className="form-input"
                                type="text"
                                autoComplete="username"
                                value={username}
                                onChange={(e) => setUsername(e.target.value)}
                                required
                            />
                        </div>
                        <div className="form-group">
                            <label className="form-label" htmlFor="login-password">Password</label>
                            <input
                                id="login-password"
                                className="form-input"
                                type="password"
                                autoComplete="current-password"
                                value={password}
                                onChange={(e) => setPassword(e.target.value)}
                                required
                            />
                        </div>
                        <button
                            className="btn btn-primary btn-lg btn-block auth-submit"
                            type="submit"
                            disabled={loading || !username || !password}
                        >
                            {loading ? 'Signing in…' : 'Sign in'}
                        </button>
                    </form>

                    <p className="auth-foot">
                        No account yet? <Link to="/register">Create one</Link>
                    </p>
                </div>
            </main>
        </div>
    );
}
