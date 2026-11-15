/**
 * Read design tokens (CSS custom properties from styles/tokens.css) in JS.
 * Charts and canvas drawing must use these — never literal colours.
 */
import { useEffect, useState } from 'react';
import { useTheme } from '../contexts/ThemeContext';

export const CHART_SERIES = ['--chart-1', '--chart-2', '--chart-3', '--chart-4', '--chart-5', '--chart-6', '--chart-7', '--chart-8'];

/** Current value of one CSS custom property, e.g. token('--text-2'). */
export function token(name, el = typeof document !== 'undefined' ? document.documentElement : null) {
    if (!el) return '';
    return getComputedStyle(el).getPropertyValue(name).trim();
}

/** Same colour with an alpha channel, for chart fills. Works for #rrggbb tokens. */
export function withAlpha(color, alpha) {
    const hex = String(color || '').trim();
    if (/^#[0-9a-f]{6}$/i.test(hex)) {
        const a = Math.round(Math.max(0, Math.min(1, alpha)) * 255).toString(16).padStart(2, '0');
        return `${hex}${a}`;
    }
    return `color-mix(in srgb, ${hex} ${Math.round(alpha * 100)}%, transparent)`;
}

/** Snapshot of every token the charts need. */
export function readTokens() {
    return {
        text: token('--text'),
        text2: token('--text-2'),
        text3: token('--text-3'),
        surface: token('--surface'),
        surface2: token('--surface-2'),
        line: token('--line'),
        lineStrong: token('--line-strong'),
        accent: token('--accent'),
        accentFill: token('--accent-fill'),
        accent2: token('--accent-2'),
        neutral: token('--chart-neutral'),
        highlight: token('--chart-highlight'),
        danger: token('--danger'),
        warn: token('--warn'),
        success: token('--success'),
        info: token('--info'),
        grid: token('--chart-grid'),
        axis: token('--chart-axis'),
        tooltipBg: token('--chart-tooltip-bg'),
        tooltipText: token('--chart-tooltip-text'),
        seqLo: token('--chart-seq-lo'),
        seqHi: token('--chart-seq-hi'),
        series: CHART_SERIES.map((n) => token(n)),
        fontSans: token('--font-sans'),
        fontMono: token('--font-mono'),
    };
}

/** React hook: token snapshot that refreshes when the theme changes. */
export function useTokens() {
    const { theme } = useTheme();
    const [tokens, setTokens] = useState(readTokens);
    useEffect(() => { setTokens(readTokens()); }, [theme]);
    return tokens;
}
