/**
 * OmniTrack AI — hub page: one nav entry, several focused views as tabs.
 * The tab is part of the URL (/floor/queues), so links and reloads land on the same view.
 * Section components render with `embedded` (no page header of their own).
 */
import React, { Suspense } from 'react';
import { Link, Navigate, useParams } from 'react-router-dom';
import useUserRole from '../hooks/useUserRole';
import { NAV, canSee } from '../nav';
import '../styles/hub.css';

export default function HubPage({ path, subtitle, views }) {
    const { section } = useParams();
    const { role } = useUserRole();
    const hub = NAV.find((n) => n.path === path);
    const sections = (hub?.sections || []).filter((s) => canSee(s, role) && views[s.id]);
    if (!sections.length) return <Navigate to="/" replace />;
    const active = sections.find((s) => s.id === section);
    if (section && !active) return <Navigate to={path} replace />;
    const current = active || sections[0];
    const View = views[current.id];

    return (
        <div className="hub">
            <header className="page-header hub-header">
                <div>
                    <h1 className="page-title">{hub.label}</h1>
                    {subtitle ? <p className="page-subtitle">{subtitle}</p> : null}
                </div>
            </header>
            {sections.length > 1 ? (
                <nav className="hub-tabs" aria-label={`${hub.label} views`}>
                    {sections.map((s, i) => (
                        <Link
                            key={s.id}
                            to={`${path}/${s.id}`}
                            className={`hub-tab${s.id === current.id ? ' is-active' : ''}`}
                            aria-current={s.id === current.id ? 'page' : undefined}
                        >
                            <span className="hub-tab-index" aria-hidden>{String(i + 1).padStart(2, '0')}</span>
                            {s.label}
                        </Link>
                    ))}
                </nav>
            ) : null}
            <section className="hub-body" aria-label={current.label}>
                <Suspense fallback={<div className="app-loading osd">Loading</div>}>
                    <View embedded />
                </Suspense>
            </section>
        </div>
    );
}
