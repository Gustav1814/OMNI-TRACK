/**
 * OmniTrack AI — Login Page
 */

import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';

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

        // For demo: accept any credentials
        setTimeout(() => {
            localStorage.setItem('omnitrack_token', 'demo-jwt-token');
            onLogin();
            navigate('/');
            setLoading(false);
        }, 800);
    };

    return (
        <div className="login-page">
            <div className="login-card animate-in">
                <div className="login-logo">O</div>
                <h1 className="login-title">OmniTrack AI</h1>
                <p className="login-subtitle">Retail Analytics Platform</p>

                {error && <div className="alert-banner danger">{error}</div>}

                <form onSubmit={handleSubmit}>
                    <div className="form-group">
                        <label className="form-label">Username</label>
                        <input
                            className="form-input"
                            type="text"
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
                            value={password}
                            onChange={(e) => setPassword(e.target.value)}
                            placeholder="Enter password"
                            required
                        />
                    </div>
                    <button
                        className="btn btn-primary"
                        type="submit"
                        disabled={loading}
                        style={{ width: '100%', justifyContent: 'center', padding: '12px', marginTop: 8 }}
                    >
                        {loading ? 'Signing in...' : 'Sign In'}
                    </button>
                </form>

                <p style={{ textAlign: 'center', fontSize: 12, color: 'var(--text-muted)', marginTop: 20 }}>
                    Demo: Enter any credentials to continue
                </p>
            </div>
        </div>
    );
}
