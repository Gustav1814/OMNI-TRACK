/**
 * OmniTrack — Account, user management (admin) and appearance settings.
 * `embedded` renders the content without the page header and page wrapper,
 * for the System hub.
 */

import React, { useEffect, useState } from 'react';
import { Moon, Sun } from 'lucide-react';
import { useTheme } from '../contexts/ThemeContext';
import AccountPanel, { UserManagementPanel } from '../components/AccountPanel';
import { apiErrorMessage, authAPI, tokenStore } from '../services/api';
import useUserRole from '../hooks/useUserRole';
import PageHeader from '../components/PageHeader';

const THEMES = [
    { id: 'light', label: 'Light', Icon: Sun },
    { id: 'dark', label: 'Dark', Icon: Moon },
];

const ROLE_PILL = { admin: 'pill-accent', operator: 'pill-info' };
const capitalise = (s) => (s ? `${s.charAt(0).toUpperCase()}${s.slice(1)}` : s);

function Section({ title, desc, children }) {
    return (
        <section className="adm-section">
            <div>
                <h2 className="adm-section-title">{title}</h2>
                {desc && <p className="adm-section-desc">{desc}</p>}
            </div>
            <div>{children}</div>
        </section>
    );
}

export default function SettingsPage({ embedded = false }) {
    const { theme, setTheme } = useTheme();
    const { canAdmin } = useUserRole();
    const [me, setMe] = useState(() => tokenStore.getUser());
    const [meError, setMeError] = useState(null);

    const loadMe = () => {
        setMeError(null);
        authAPI.me()
            .then((r) => { setMe(r.data); tokenStore.setUser(r.data); })
            .catch((e) => setMeError(apiErrorMessage(e, 'Could not load your account')));
    };

    useEffect(loadMe, []);

    const handle = [me?.username ? `@${me.username}` : null, me?.email].filter(Boolean).join(' · ');

    return (
        <div className={embedded ? 'adm-embedded' : 'adm-page'}>
            {!embedded && (
                <PageHeader
                    title="Account and settings"
                    subtitle={`Your account and sessions${canAdmin ? ', user access' : ''} and appearance.`}
                />
            )}

            <section className="panel panel--flush adm-flush" aria-label="Signed in as">
                <div className="panel-body">
                    <div>
                        <span className="metric-label">Signed in as</span>
                        <h2 className="adm-big">{me?.full_name || me?.username || (meError ? 'Account not loaded' : 'Loading account…')}</h2>
                        {handle && <p className="adm-big-sub osd">{handle}</p>}
                        {meError && !me?.username && (
                            <div className="adm-embed-bar adm-load-error">
                                <span className="form-hint">{meError}</span>
                                <button type="button" className="btn btn-secondary btn-sm" onClick={loadMe}>Try again</button>
                            </div>
                        )}
                    </div>
                    <div className="kpi-strip">
                        <div className="metric">
                            <span className="metric-label">Role</span>
                            <span className={`pill ${ROLE_PILL[me?.role] || 'pill-neutral'}`}>{capitalise(me?.role) || '—'}</span>
                        </div>
                        <div className="metric">
                            <span className="metric-label">Member since</span>
                            <span className="adm-text-value is-mono">
                                {me?.created_at ? new Date(me.created_at).toLocaleDateString() : '—'}
                            </span>
                        </div>
                        <div className="metric">
                            <span className="metric-label">Account</span>
                            <span className={`pill ${me?.is_active === false ? 'pill-danger' : 'pill-success'}`}>
                                {me?.is_active === false ? 'Disabled' : 'Active'}
                            </span>
                        </div>
                    </div>
                </div>
            </section>

            <div>
                <Section title="Account" desc="Your password and signed-in sessions.">
                    <AccountPanel me={me} />
                </Section>

                {canAdmin && (
                    <Section title="Users and roles" desc="Viewers read dashboards. Operators run feeds and zones. Admins manage users and security.">
                        <UserManagementPanel />
                    </Section>
                )}

                <Section title="Appearance" desc="Saved in this browser.">
                    <div className="panel">
                        <div className="panel-body">
                            <div className="adm-row">
                                <div>
                                    <div className="adm-row-label" id="theme-label">Theme</div>
                                    <div className="adm-row-sub">Video tiles stay black in both themes.</div>
                                </div>
                                <div className="seg adm-seg" role="group" aria-labelledby="theme-label">
                                    {THEMES.map(({ id, label, Icon }) => (
                                        <button key={id} type="button" aria-pressed={theme === id} onClick={() => setTheme(id)}>
                                            <Icon size={14} strokeWidth={1.75} /> {label}
                                        </button>
                                    ))}
                                </div>
                            </div>
                        </div>
                    </div>
                </Section>
            </div>
        </div>
    );
}
