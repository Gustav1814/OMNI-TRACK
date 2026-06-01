import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Search, LayoutDashboard, Scan, Flame, Users, Command } from 'lucide-react';

const COMMANDS = [
    { type: 'page', path: '/', label: 'Executive Overview', icon: LayoutDashboard, group: 'Navigate' },
    { type: 'page', path: '/vibe', label: 'Store Atmosphere', icon: LayoutDashboard, group: 'Navigate' },
    { type: 'page', path: '/detection', label: 'In-Store Cameras', icon: Scan, group: 'Navigate' },
    { type: 'page', path: '/reid', label: 'Shopper Journey', icon: Users, group: 'Navigate' },
    { type: 'page', path: '/fire', label: 'Safety Monitoring', icon: Flame, group: 'Navigate' },
    { type: 'page', path: '/crowd', label: 'Store Traffic', icon: Users, group: 'Navigate' },
    { type: 'page', path: '/checkout', label: 'Checkout Experience', icon: Users, group: 'Navigate' },
    { type: 'page', path: '/peak-hours', label: 'Peak Performance', icon: LayoutDashboard, group: 'Navigate' },
    { type: 'action', id: 'refresh', label: 'Refresh data', group: 'Actions' },
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
