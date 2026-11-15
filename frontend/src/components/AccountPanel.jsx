/**
 * Account (own profile, password, sessions) and admin user management.
 * Backed by /api/auth/{me,change-password,logout,users}.
 */

import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { KeyRound, LogOut, UserPlus, Unlock, Trash2 } from 'lucide-react';
import { apiErrorMessage, authAPI, logout, tokenStore } from '../services/api';
import { useToast } from '../contexts/ToastContext';

const MIN_PW = 10;
const ROLES = ['viewer', 'operator', 'admin'];

/** Password and sessions. The identity (name, role) is shown by the Settings page header block. */
export default function AccountPanel({ me }) {
    const navigate = useNavigate();
    const { toast } = useToast();
    const [pw, setPw] = useState({ current: '', next: '', confirm: '' });
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');

    const changePassword = async (e) => {
        e.preventDefault();
        setError('');
        if (pw.next.length < MIN_PW) return setError(`New password must be at least ${MIN_PW} characters.`);
        if (pw.next !== pw.confirm) return setError('New passwords do not match.');
        setBusy(true);
        try {
            const { data } = await authAPI.changePassword(pw.current, pw.next);
            tokenStore.set(data.access_token, data.refresh_token);
            setPw({ current: '', next: '', confirm: '' });
            toast({ title: 'Password changed', message: 'All other sessions were signed out.', type: 'success' });
        } catch (err) {
            setError(apiErrorMessage(err, 'Could not change password'));
        } finally { setBusy(false); }
    };

    const signOut = async (allSessions) => {
        await logout({ allSessions });
        navigate('/login');
    };

    return (
        <div className="panel">
            <div className="panel-body">
                {me?.role && me.role !== 'admin' && (
                    <>
                        <p className="adm-inline-note">
                            {me.role === 'operator' ? 'Operator access: you can run feeds but not change users or security.' : 'Read-only access. Changes need an administrator.'}
                        </p>
                        <div className="adm-divider" />
                    </>
                )}

                <form onSubmit={changePassword}>
                    <div className="adm-group-head">
                        <h3 className="adm-group-title">Password</h3>
                        <p className="adm-group-sub">Changing it signs out every other session.</p>
                    </div>
                    <div className="form-row">
                        <div className="form-group">
                            <label className="form-label" htmlFor="pw-current">Current password</label>
                            <input id="pw-current" className="form-input" type="password" autoComplete="current-password"
                                value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} required />
                        </div>
                        <div className="form-group">
                            <label className="form-label" htmlFor="pw-next">New password</label>
                            <input id="pw-next" className="form-input" type="password" autoComplete="new-password"
                                minLength={MIN_PW} maxLength={128} aria-describedby="pw-next-hint"
                                value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} required />
                        </div>
                        <div className="form-group">
                            <label className="form-label" htmlFor="pw-confirm">Repeat new password</label>
                            <input id="pw-confirm" className="form-input" type="password" autoComplete="new-password"
                                value={pw.confirm} onChange={(e) => setPw({ ...pw, confirm: e.target.value })} required />
                        </div>
                    </div>
                    <p className="form-hint adm-hint-gap" id="pw-next-hint">At least {MIN_PW} characters.</p>
                    {error && <div className="alert-banner danger adm-form-error" role="alert">{error}</div>}
                    <div className="adm-form-actions">
                        <button type="submit" className="btn btn-primary btn-sm" disabled={busy}>
                            <KeyRound size={14} strokeWidth={1.75} /> {busy ? 'Saving…' : 'Change password'}
                        </button>
                    </div>
                </form>

                <div className="adm-divider" />

                <div className="adm-group-head">
                    <h3 className="adm-group-title">Sessions</h3>
                    <p className="adm-group-sub">Sign out here, or revoke every refresh token issued to your account.</p>
                </div>
                <div className="adm-actions">
                    <button type="button" className="btn btn-secondary btn-sm" onClick={() => signOut(false)}>
                        <LogOut size={14} strokeWidth={1.75} /> Sign out
                    </button>
                    <button type="button" className="btn btn-ghost btn-sm" onClick={() => signOut(true)}>
                        Sign out of all sessions
                    </button>
                </div>
            </div>
        </div>
    );
}

export function UserManagementPanel() {
    const { toast } = useToast();
    const [users, setUsers] = useState([]);
    const [error, setError] = useState('');
    const [busyId, setBusyId] = useState(null);
    const [form, setForm] = useState({ username: '', email: '', password: '', role: 'viewer' });
    const myId = tokenStore.getUser()?.id;

    const load = useCallback(async () => {
        try {
            const { data } = await authAPI.users();
            setUsers(Array.isArray(data) ? data : []);
            setError('');
        } catch (e) {
            setError(apiErrorMessage(e, 'Could not load users'));
        }
    }, []);

    useEffect(() => { load(); }, [load]);

    const act = async (id, fn, okMsg) => {
        setBusyId(id);
        try {
            await fn();
            if (okMsg) toast({ title: okMsg, type: 'success' });
            await load();
        } catch (e) {
            toast({ title: 'Action failed', message: apiErrorMessage(e), type: 'error' });
        } finally { setBusyId(null); }
    };

    const create = (e) => {
        e.preventDefault();
        act('new', async () => {
            await authAPI.createUser({
                username: form.username.trim(), email: form.email.trim(), password: form.password, role: form.role,
            });
            setForm({ username: '', email: '', password: '', role: 'viewer' });
        }, 'User created');
    };

    const locked = (u) => u.locked_until && new Date(u.locked_until) > new Date();

    return (
        <div className="panel">
            <div className="panel-body">
                <div className="panel-head">
                    <div>
                        <h3 className="panel-title">Users</h3>
                        <p className="panel-sub">Every change here is written to the audit chain.</p>
                    </div>
                    <span className="count-badge">{users.length} {users.length === 1 ? 'user' : 'users'}</span>
                </div>
                {error && <div className="alert-banner danger" role="alert">{error}</div>}
                <div className="table-scroll">
                    <table className="data-table adm-table adm-table--wide">
                        <thead>
                            <tr>
                                <th>User</th>
                                <th>Role</th>
                                <th>Active</th>
                                <th>Last sign-in</th>
                                <th><span className="sr-only">Actions</span></th>
                            </tr>
                        </thead>
                        <tbody>
                            {users.map((u) => (
                                <tr key={u.id}>
                                    <td>
                                        <span className="adm-strong">@{u.username}</span>
                                        {u.id === myId && <span className="adm-inline-note"> (you)</span>}
                                        <span className="adm-sub">{u.email}</span>
                                    </td>
                                    <td>
                                        <select className="form-select" value={u.role} aria-label={`Role for ${u.username}`}
                                            disabled={busyId === u.id}
                                            onChange={(e) => act(u.id, () => authAPI.updateUser(u.id, { role: e.target.value }), 'Role updated')}>
                                            {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                                        </select>
                                    </td>
                                    <td>
                                        <input type="checkbox" checked={u.is_active} aria-label={`${u.username} active`}
                                            disabled={busyId === u.id || u.id === myId}
                                            onChange={(e) => act(u.id, () => authAPI.updateUser(u.id, { is_active: e.target.checked }),
                                                e.target.checked ? 'User activated' : 'User deactivated')} />
                                    </td>
                                    <td>
                                        <span className="adm-mono">{u.last_login_at ? new Date(u.last_login_at).toLocaleString() : 'Never'}</span>
                                        {locked(u) && <span className="adm-sub">Locked after failed sign-ins</span>}
                                        {u.failed_login_attempts > 0 && !locked(u) && (
                                            <span className="adm-sub">{u.failed_login_attempts} failed attempt{u.failed_login_attempts === 1 ? '' : 's'}</span>
                                        )}
                                    </td>
                                    <td>
                                        <div className="adm-table-actions">
                                            {locked(u) && (
                                                <button type="button" className="btn btn-secondary btn-xs" disabled={busyId === u.id}
                                                    onClick={() => act(u.id, () => authAPI.updateUser(u.id, { unlock: true }), 'Account unlocked')}>
                                                    <Unlock size={14} strokeWidth={1.75} /> Unlock
                                                </button>
                                            )}
                                            {u.id !== myId && (
                                                <button type="button" className="btn btn-ghost btn-xs btn-icon" disabled={busyId === u.id}
                                                    onClick={() => {
                                                        if (window.confirm(`Delete user @${u.username}? This cannot be undone.`)) {
                                                            act(u.id, () => authAPI.deleteUser(u.id), 'User deleted');
                                                        }
                                                    }}
                                                    aria-label={`Delete ${u.username}`} title="Delete user">
                                                    <Trash2 size={14} strokeWidth={1.75} />
                                                </button>
                                            )}
                                        </div>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>

                <div className="adm-divider" />

                <form onSubmit={create}>
                    <div className="adm-group-head">
                        <h3 className="adm-group-title">Add a user</h3>
                    </div>
                    <div className="adm-addrow">
                        <div className="form-group">
                            <label className="form-label" htmlFor="nu-username">Username</label>
                            <input id="nu-username" className="form-input" minLength={3} maxLength={50} required autoComplete="off"
                                value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} />
                        </div>
                        <div className="form-group">
                            <label className="form-label" htmlFor="nu-email">Email</label>
                            <input id="nu-email" className="form-input" type="email" required autoComplete="off"
                                value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
                        </div>
                        <div className="form-group">
                            <label className="form-label" htmlFor="nu-password">Password</label>
                            <input id="nu-password" className="form-input" type="password" autoComplete="new-password"
                                minLength={MIN_PW} maxLength={128} required placeholder={`${MIN_PW}+ characters`}
                                value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
                        </div>
                        <div className="form-group">
                            <label className="form-label" htmlFor="nu-role">Role</label>
                            <select id="nu-role" className="form-select" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
                                {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                            </select>
                        </div>
                        <button type="submit" className="btn btn-primary" disabled={busyId === 'new'}>
                            <UserPlus size={16} strokeWidth={1.75} /> Add user
                        </button>
                    </div>
                </form>
            </div>
        </div>
    );
}
