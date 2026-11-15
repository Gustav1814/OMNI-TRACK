/**
 * Theme context: light (paper white) / dark (AMOLED black), persisted in localStorage.
 * index.html applies the stored class before first paint; this keeps it in sync.
 */
import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';

const ThemeContext = createContext(null);
const STORAGE_KEY = 'omnitrack-theme';
const LEGACY_KEYS = ['omnitrack-gradient', 'omnitrack-ui-theme'];

function initialTheme() {
    if (typeof window === 'undefined') return 'dark';
    try {
        const saved = localStorage.getItem(STORAGE_KEY);
        if (saved === 'light' || saved === 'dark') return saved;
    } catch { /* storage unavailable */ }
    return window.matchMedia?.('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}

export function ThemeProvider({ children }) {
    const [theme, setThemeState] = useState(initialTheme);

    useEffect(() => {
        const root = document.documentElement;
        root.classList.remove('light', 'dark');
        root.classList.add(theme);
        root.style.removeProperty('background');
        try {
            localStorage.setItem(STORAGE_KEY, theme);
            LEGACY_KEYS.forEach((k) => localStorage.removeItem(k));
        } catch { /* storage unavailable */ }
    }, [theme]);

    const setTheme = useCallback((value) => setThemeState(value === 'light' ? 'light' : 'dark'), []);
    const toggleTheme = useCallback(() => setThemeState((t) => (t === 'dark' ? 'light' : 'dark')), []);
    const value = useMemo(() => ({ theme, setTheme, toggleTheme }), [theme, setTheme, toggleTheme]);

    return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
    const ctx = useContext(ThemeContext);
    if (!ctx) throw new Error('useTheme must be used within ThemeProvider');
    return ctx;
}
