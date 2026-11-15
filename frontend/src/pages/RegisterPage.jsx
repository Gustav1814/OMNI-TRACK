/**
 * OmniTrack AI — Sign up
 * GET /api/auth/bootstrap decides the mode:
 *   - no users yet          -> "create the first admin" (bootstraps as admin)
 *   - public registration   -> self sign-up (viewer role)
 *   - otherwise             -> closed; accounts are created by an admin.
 * POST /api/auth/register, then signs you in with the same password.
 */

import React, { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { apiErrorMessage, authAPI, tokenStore } from '../services/api';
import { AuthBrand } from './LoginPage';

export default function RegisterPage({ onRegister }) {
    const [username, setUsername] = useState('');
    const [email, setEmail] = useState('');
    const [fullName, setFullName] = useState('');
    const [password, setPassword] = useState('');
    const [confirm, setConfirm] = useState('');
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);
    const [boot, setBoot] = useState(null); // {needs_bootstrap, public_registration, password_min_length}
    const navigate = useNavigate();

    useEffect(() => {
        let alive = true;
        authAPI.bootstrap()
            .then((r) => alive && setBoot(r.data))
            .catch(() => alive && setBoot({ needs_bootstrap: false, public_registration: false, unreachable: true }));
        return () => { alive = false; };
    }, []);

    const minLen = boot?.password_min_length || 10;
    const open = !!boot && (boot.needs_bootstrap || boot.public_registration);

    const handleSubmit = async (e) => {
        e.preventDefault();
        setError('');
        if (username.trim().length < 3) {
            setError('Username must be at least 3 characters.');
            return;
        }
        if (password.length < minLen) {
            setError(`Password must be at least ${minLen} characters.`);
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
            setError(apiErrorMessage(err, 'Sign up failed — is the backend and database running?'));
        } finally {
            setLoading(false);
        }
    };

    const canSubmit = open && username.trim().length >= 3 && email.includes('@') && password.length >= minLen && password === confirm;

    return (
        <div className="auth">
            <AuthBrand />
            <main className="auth-main">
                <div className="auth-card">
                    <h1 className="auth-title">
                        {boot?.needs_bootstrap ? 'Create the first admin' : 'Create account'}
                    </h1>
                    <p className="auth-sub">
                        {!boot && 'Checking registration status…'}
                        {boot?.needs_bootstrap && 'No accounts exist yet — this account becomes the administrator.'}
                        {boot && !boot.needs_bootstrap && boot.public_registration && 'New accounts start with read-only (viewer) access.'}
                        {boot && !open && !boot.unreachable && 'Self-registration is disabled. Ask an administrator to create your account.'}
                        {boot?.unreachable && 'Cannot reach the server.'}
                    </p>

                    {error && <div className="alert-banner danger" role="alert">{error}</div>}

                    <form className="auth-form" onSubmit={handleSubmit}>
                        <div className="form-row">
                            <div className="form-group">
                                <label className="form-label" htmlFor="reg-username">Username</label>
                                <input
                                    id="reg-username"
                                    className="form-input"
                                    type="text"
                                    autoComplete="username"
                                    minLength={3}
                                    maxLength={50}
                                    value={username}
                                    onChange={(e) => setUsername(e.target.value)}
                                    disabled={!open}
                                    required
                                />
                            </div>
                            <div className="form-group">
                                <label className="form-label" htmlFor="reg-name">Full name <span className="form-hint">optional</span></label>
                                <input
                                    id="reg-name"
                                    className="form-input"
                                    type="text"
                                    autoComplete="name"
                                    value={fullName}
                                    onChange={(e) => setFullName(e.target.value)}
                                    disabled={!open}
                                />
                            </div>
                        </div>
                        <div className="form-group">
                            <label className="form-label" htmlFor="reg-email">Email</label>
                            <input
                                id="reg-email"
                                className="form-input"
                                type="email"
                                autoComplete="email"
                                value={email}
                                onChange={(e) => setEmail(e.target.value)}
                                disabled={!open}
                                required
                            />
                        </div>
                        <div className="form-group">
                            <label className="form-label" htmlFor="reg-password">Password</label>
                            <input
                                id="reg-password"
                                className="form-input"
                                type="password"
                                autoComplete="new-password"
                                minLength={minLen}
                                maxLength={128}
                                value={password}
                                onChange={(e) => setPassword(e.target.value)}
                                aria-describedby="reg-password-hint"
                                disabled={!open}
                                required
                            />
                            <span className="form-hint" id="reg-password-hint">At least {minLen} characters.</span>
                        </div>
                        <div className="form-group">
                            <label className="form-label" htmlFor="reg-confirm">Confirm password</label>
                            <input
                                id="reg-confirm"
                                className="form-input"
                                type="password"
                                autoComplete="new-password"
                                value={confirm}
                                onChange={(e) => setConfirm(e.target.value)}
                                disabled={!open}
                                required
                            />
                        </div>
                        <button
                            className="btn btn-primary btn-lg btn-block auth-submit"
                            type="submit"
                            disabled={loading || !canSubmit}
                        >
                            {loading ? 'Creating account…' : 'Create account'}
                        </button>
                    </form>

                    <p className="auth-foot">
                        Already have an account? <Link to="/login">Sign in</Link>
                    </p>
                </div>
            </main>
        </div>
    );
}
