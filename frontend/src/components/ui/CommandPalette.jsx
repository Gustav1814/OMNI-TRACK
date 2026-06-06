import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
    Search, LayoutDashboard, Scan, Flame, Users, Command, Activity, Video,
    ShoppingBag, UsersRound, ShoppingCart, TrendingUp, BarChart3, Smile,
    Store, SlidersHorizontal, ClipboardList, ShieldCheck, Settings,
} from 'lucide-react';

const COMMANDS = [
    { type: 'page', path: '/', label: 'Live Overview', icon: LayoutDashboard, group: 'Command Center' },
    { type: 'page', path: '/vibe', label: 'Store Pulse', icon: Activity, group: 'Command Center' },
    { type: 'page', path: '/peak-hours', label: 'Rush Hours', icon: TrendingUp, group: 'Command Center' },
    { type: 'page', path: '/detection', label: 'Video Feeds', icon: Scan, group: 'Operations' },
    { type: 'page', path: '/reid', label: 'Cross-Feed Match', icon: Users, group: 'Operations' },
    { type: 'page', path: '/synopsis', label: 'Highlights Reel', icon: Video, group: 'Operations' },
    { type: 'page', path: '/shelf', label: 'Shelf Activity', icon: ShoppingBag, group: 'Analytics' },
    { type: 'page', path: '/crowd', label: 'Footfall', icon: UsersRound, group: 'Analytics' },
    { type: 'page', path: '/checkout', label: 'Queue Insights', icon: ShoppingCart, group: 'Analytics' },
    { type: 'page', path: '/emotion', label: 'Mood Trends', icon: Smile, group: 'Analytics' },
    { type: 'page', path: '/demographics', label: 'Audience Mix', icon: BarChart3, group: 'Analytics' },
    { type: 'page', path: '/fire', label: 'Safety Watch', icon: Flame, group: 'Analytics' },
    { type: 'page', path: '/humanless', label: 'Smart Store', icon: Store, group: 'Smart Commerce' },
    { type: 'page', path: '/setup', label: 'Store Setup', icon: SlidersHorizontal, group: 'Administration' },
    { type: 'page', path: '/audit', label: 'Activity Log', icon: ClipboardList, group: 'Administration' },
    { type: 'page', path: '/security', label: 'Model Health', icon: ShieldCheck, group: 'Administration' },
    { type: 'page', path: '/settings', label: 'Appearance', icon: Settings, group: 'Administration' },
    { type: 'action', id: 'refresh', label: 'Refresh data', group: 'Actions' },
    { type: 'action', id: 'start-session', label: 'Start monitoring session', group: 'Actions' },
    { type: 'action', id: 'stop-session', label: 'Stop monitoring session', group: 'Actions' },
    { type: 'action', id: 'theme', label: 'Toggle light / dark theme', group: 'Actions' },
];

export default function CommandPalette({ onAction }) {
    const [open, setOpen] = useState(false);
    const [query, setQuery] = useState('');
    const navigate = useNavigate();

    useEffect(() => {
        const onKey = (e) => {
            if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
                e.preventDefault();
                setOpen((v) => !v);
            }
            if (e.key === 'Escape') setOpen(false);
        };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, []);

    const filtered = useMemo(() => {
        const q = query.trim().toLowerCase();
        if (!q) return COMMANDS;
        return COMMANDS.filter((c) => c.label.toLowerCase().includes(q) || c.group.toLowerCase().includes(q));
    }, [query]);

    const run = (cmd) => {
        if (cmd.type === 'page') {
            navigate(cmd.path);
        } else if (onAction) {
            onAction(cmd.id);
        }
        setOpen(false);
        setQuery('');
    };

    if (!open) return null;

    const groups = [...new Set(filtered.map((c) => c.group))];

    return (
        <div className="ui-cmd-overlay" onClick={() => setOpen(false)} role="presentation">
            <div className="ui-cmd" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Command palette">
                <div className="ui-cmd-search">
                    <Search size={16} />
                    <input
                        autoFocus
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        placeholder="Search pages and actions…"
                    />
                    <kbd className="ui-cmd-kbd">esc</kbd>
                </div>
                <div className="ui-cmd-list">
                    {groups.map((group) => (
                        <div key={group} className="ui-cmd-group">
                            <div className="ui-cmd-group-label">{group}</div>
                            {filtered.filter((c) => c.group === group).map((cmd) => {
                                const Icon = cmd.icon || Command;
                                return (
                                    <button key={cmd.path || cmd.id} type="button" className="ui-cmd-item" onClick={() => run(cmd)}>
                                        <Icon size={15} />
                                        <span>{cmd.label}</span>
                                    </button>
                                );
                            })}
                        </div>
                    ))}
                    {!filtered.length ? <div className="ui-cmd-empty">No results</div> : null}
                </div>
                <div className="ui-cmd-foot">
                    <kbd>↑↓</kbd> navigate
                    <kbd>↵</kbd> select
                    <kbd>⌘K</kbd> toggle
                </div>
            </div>
        </div>
    );
}
