/**
 * Theme context — light/dark mode + canvas gradient preset with persistence
 */
import React, { createContext, useContext, useState, useEffect } from 'react';
import {
    DEFAULT_GRADIENT_ID,
    applyGradientToDocument,
    resolvePresetId,
} from '../lib/gradientPresets';

const ThemeContext = createContext(null);

const STORAGE_KEY = 'omnitrack-theme';
const GRADIENT_STORAGE_KEY = 'omnitrack-gradient';
const UI_THEME_STORAGE_KEY = 'omnitrack-ui-theme'; // legacy (removed)
const LOCKED_UI_THEME = 'pearl';

export function ThemeProvider({ children }) {
    const [theme, setThemeState] = useState(() => {
        if (typeof window === 'undefined') return 'dark';
        return localStorage.getItem(STORAGE_KEY) || 'dark';
    });

    const [gradientPreset, setGradientPresetState] = useState(() => {
        if (typeof window === 'undefined') return DEFAULT_GRADIENT_ID;
        const raw = localStorage.getItem(GRADIENT_STORAGE_KEY) || DEFAULT_GRADIENT_ID;
        const resolved = resolvePresetId(raw);
        // One-time bump: older installs defaulted to low-contrast presets.
        if (resolved === 'obsidian' && raw === 'obsidian') {
            return DEFAULT_GRADIENT_ID;
        }
        return resolved;
    });

    useEffect(() => {
        const root = document.documentElement;
        root.classList.remove('light', 'dark');
        root.classList.add(theme);
        localStorage.setItem(STORAGE_KEY, theme);
    }, [theme]);

    useEffect(() => {
        const root = document.documentElement;
        // lock UI style to Pearl (remove any legacy theme classes)
        root.classList.remove('theme-enterprise', 'theme-premium', 'theme-amoled');
        root.classList.add(`theme-${LOCKED_UI_THEME}`);
        try { localStorage.removeItem(UI_THEME_STORAGE_KEY); } catch { /* ignore */ }
    }, []);

    useEffect(() => {
        applyGradientToDocument(gradientPreset, theme);
        localStorage.setItem(GRADIENT_STORAGE_KEY, gradientPreset);
    }, [gradientPreset, theme]);

    const setTheme = (value) => setThemeState(value === 'light' ? 'light' : 'dark');
    const toggleTheme = () => setThemeState((t) => (t === 'dark' ? 'light' : 'dark'));
    const setGradientPreset = (id) => setGradientPresetState(id);

    return (
        <ThemeContext.Provider value={{
            theme,
            setTheme,
            toggleTheme,
            gradientPreset,
            setGradientPreset,
        }}
        >
            {children}
        </ThemeContext.Provider>
    );
}

export function useTheme() {
    const ctx = useContext(ThemeContext);
    if (!ctx) throw new Error('useTheme must be used within ThemeProvider');
    return ctx;
}
