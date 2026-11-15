/**
 * OmniTrack AI — the one navigation map. Sidebar, command palette, page titles and
 * legacy redirects are all derived from it, so a page is defined in exactly one place.
 *
 * A hub groups related views under one nav entry; each section is a tab at
 * `${path}/${section.id}` (the first section is also served at `${path}`).
 */
import {
    LayoutDashboard, Scan, Map, Smile, Users, Siren, Video, Cctv, Settings2,
} from 'lucide-react';

const ALL = ['admin', 'operator', 'viewer'];

export const NAV = [
    { section: 'Watch' },
    { path: '/', icon: LayoutDashboard, label: 'Overview', roles: ALL },
    { path: '/detection', icon: Scan, label: 'Live feeds', roles: ALL },
    { path: '/incidents', icon: Siren, label: 'Incidents', roles: ALL, badge: 'unackedAlerts',
        sections: [
            { id: 'alerts', label: 'Alerts' },
            { id: 'captures', label: 'Evidence' },
            { id: 'fire', label: 'Fire & smoke' },
        ] },
    { section: 'Understand' },
    { path: '/floor', icon: Map, label: 'Floor', roles: ALL,
        sections: [
            { id: 'heatmap', label: 'Heatmap' },
            { id: 'footfall', label: 'Footfall' },
            { id: 'rush-hours', label: 'Rush hours' },
            { id: 'queues', label: 'Queues' },
            { id: 'shelves', label: 'Shelves' },
        ] },
    { path: '/shoppers', icon: Smile, label: 'Shoppers', roles: ALL,
        sections: [
            { id: 'mood', label: 'Mood' },
            { id: 'audience', label: 'Audience' },
        ] },
    { path: '/reid', icon: Users, label: 'Journeys', roles: ALL },
    { path: '/synopsis', icon: Video, label: 'Highlights', roles: ALL },
    { section: 'Configure' },
    { path: '/setup', icon: Cctv, label: 'Cameras & zones', roles: ['admin', 'operator'] },
    { path: '/system', icon: Settings2, label: 'System', roles: ALL,
        sections: [
            { id: 'security', label: 'Security' },
            { id: 'activity', label: 'Activity log', roles: ['admin'] },
            { id: 'evaluation', label: 'Evaluation' },
            { id: 'account', label: 'Account' },
        ] },
];

/** Old URLs → their new home (bookmarks, alert links, tests). */
export const LEGACY_REDIRECTS = {
    '/vibe': '/',
    '/alerts': '/incidents/alerts',
    '/captures': '/incidents/captures',
    '/fire': '/incidents/fire',
    '/crowd': '/floor/footfall',
    '/peak-hours': '/floor/rush-hours',
    '/checkout': '/floor/queues',
    '/shelf': '/floor/shelves',
    '/emotion': '/shoppers/mood',
    '/demographics': '/shoppers/audience',
    '/security': '/system/security',
    '/audit': '/system/activity',
    '/evaluation': '/system/evaluation',
    '/settings': '/system/account',
    '/humanless': '/',
    '/trim': '/synopsis',
    '/setup/products': '/setup',
};

export const canSee = (item, role) => !item.roles || item.roles.includes(role);

/** Nav entries (and the section labels above them) the role may open. */
export function visibleNav(role) {
    return NAV.reduce((out, item, i) => {
        if (item.section) {
            const next = NAV.slice(i + 1);
            const end = next.findIndex((n) => n.section);
            const group = end < 0 ? next : next.slice(0, end);
            if (group.some((n) => canSee(n, role))) out.push(item);
        } else if (canSee(item, role)) {
            out.push(item);
        }
        return out;
    }, []);
}

/** Title for the top bar from a pathname. */
export function titleFor(pathname) {
    const target = LEGACY_REDIRECTS[pathname] || pathname;
    const page = NAV.find((n) => n.path && (target === n.path || (n.path !== '/' && target.startsWith(`${n.path}/`))));
    if (!page) return 'OmniTrack';
    const sectionId = target.slice(page.path.length + 1).split('/')[0];
    const section = page.sections?.find((s) => s.id === sectionId);
    return section ? `${page.label} · ${section.label}` : page.label;
}

/** Every page and hub section as a command-palette entry. */
export function paletteEntries(role) {
    let group = '';
    const out = [];
    NAV.forEach((item) => {
        if (item.section) { group = item.section; return; }
        if (!canSee(item, role)) return;
        out.push({ type: 'page', path: item.path, label: item.label, icon: item.icon, group });
        (item.sections || []).forEach((s) => {
            if (canSee(s, role)) {
                out.push({ type: 'page', path: `${item.path}/${s.id}`, label: `${item.label} · ${s.label}`, icon: item.icon, group });
            }
        });
    });
    return out;
}
