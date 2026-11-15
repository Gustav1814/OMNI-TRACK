import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Search, Command } from 'lucide-react';
import useUserRole from '../../hooks/useUserRole';
import { paletteEntries } from '../../nav';

const ACTIONS = [
    { type: 'action', id: 'refresh', label: 'Refresh data', group: 'Actions' },
    { type: 'action', id: 'start-session', label: 'Start monitoring session', group: 'Actions' },
    { type: 'action', id: 'stop-session', label: 'Stop monitoring session', group: 'Actions' },
    { type: 'action', id: 'theme', label: 'Toggle light / dark theme', group: 'Actions' },
];

const IS_MAC = typeof navigator !== 'undefined' && /mac|iphone|ipad/i.test(navigator.platform || navigator.userAgent || '');

export default function CommandPalette({ onAction }) {
    const [open, setOpen] = useState(false);
    const [query, setQuery] = useState('');
    const [active, setActive] = useState(0);
    const navigate = useNavigate();
    const listRef = useRef(null);

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

    const { role } = useUserRole();
    const commands = useMemo(() => [...paletteEntries(role), ...ACTIONS], [role]);
    const filtered = useMemo(() => {
        const q = query.trim().toLowerCase();
        if (!q) return commands;
        return commands.filter((c) => c.label.toLowerCase().includes(q) || c.group.toLowerCase().includes(q));
    }, [query, commands]);

    useEffect(() => { setActive(0); }, [query, open]);

    useEffect(() => {
        listRef.current?.querySelector('[data-active="true"]')?.scrollIntoView({ block: 'nearest' });
    }, [active]);

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
    // flat order matches the rendered (grouped) order
    const ordered = groups.flatMap((g) => filtered.filter((c) => c.group === g));

    const onInputKey = (e) => {
        if (e.key === 'ArrowDown') { e.preventDefault(); setActive((i) => Math.min(ordered.length - 1, i + 1)); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((i) => Math.max(0, i - 1)); }
        else if (e.key === 'Enter' && ordered[active]) { e.preventDefault(); run(ordered[active]); }
    };

    return (
        <div className="ui-cmd-overlay" onClick={() => setOpen(false)} role="presentation">
            <div className="ui-cmd" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="Command palette">
                <div className="ui-cmd-search">
                    <Search size={16} strokeWidth={1.75} aria-hidden />
                    <input
                        autoFocus
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        onKeyDown={onInputKey}
                        placeholder="Jump to a page or run an action"
                        aria-label="Search pages and actions"
                        aria-controls="ui-cmd-list"
                        aria-activedescendant={ordered[active] ? `ui-cmd-${ordered[active].path || ordered[active].id}` : undefined}
                    />
                    <kbd className="ui-cmd-kbd">Esc</kbd>
                </div>
                <div className="ui-cmd-list" id="ui-cmd-list" role="listbox" ref={listRef}>
                    {groups.map((group) => (
                        <div key={group} className="ui-cmd-group" role="group" aria-label={group}>
                            <div className="ui-cmd-group-label" aria-hidden="true">{group}</div>
                            {filtered.filter((c) => c.group === group).map((cmd) => {
                                const Icon = cmd.icon || Command;
                                const idx = ordered.indexOf(cmd);
                                const isActive = idx === active;
                                return (
                                    <button
                                        key={cmd.path || cmd.id}
                                        id={`ui-cmd-${cmd.path || cmd.id}`}
                                        type="button"
                                        role="option"
                                        aria-selected={isActive}
                                        data-active={isActive}
                                        className="ui-cmd-item"
                                        onMouseMove={() => { if (!isActive) setActive(idx); }}
                                        onClick={() => run(cmd)}
                                    >
                                        <Icon size={16} strokeWidth={1.75} aria-hidden />
                                        <span className="ui-cmd-item-label">{cmd.label}</span>
                                        {cmd.path ? <span className="ui-cmd-item-path">{cmd.path}</span> : null}
                                    </button>
                                );
                            })}
                        </div>
                    ))}
                    {!filtered.length ? <div className="ui-cmd-empty">Nothing matches “{query.trim()}”.</div> : null}
                </div>
                <div className="ui-cmd-foot">
                    <span><kbd>↑</kbd><kbd>↓</kbd> move</span>
                    <span><kbd>↵</kbd> open</span>
                    <span><kbd>{IS_MAC ? '⌘' : 'Ctrl'}</kbd><kbd>K</kbd> toggle</span>
                </div>
            </div>
        </div>
    );
}
