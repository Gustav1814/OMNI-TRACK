/** Shared Chart.js theme — uses active gradient preset from CSS vars */

import { readGradientFromDocument } from './gradientPresets';

export function getChartColors(theme) {
    const light = theme === 'light';
    const { a, b, aRgb, bRgb } = readGradientFromDocument();
    return {
        grid: light ? 'rgba(113, 113, 122, 0.12)' : 'rgba(161, 161, 170, 0.1)',
        tick: light ? '#71717a' : '#a1a1aa',
        tooltipBg: light ? 'rgba(24, 24, 27, 0.94)' : 'rgba(9, 9, 11, 0.96)',
        primary: a,
        secondary: b,
        primaryFill: `rgba(${aRgb}, ${light ? 0.1 : 0.12})`,
        secondaryFill: `rgba(${bRgb}, ${light ? 0.08 : 0.1})`,
    };
}

export function buildLineChartOptions(theme, overrides = {}) {
    const c = getChartColors(theme);
    return {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { intersect: false, mode: 'index' },
        plugins: {
            legend: { display: false },
            tooltip: {
                backgroundColor: c.tooltipBg,
                padding: 12,
                cornerRadius: 10,
                titleColor: '#fafafa',
                bodyColor: '#e4e4e7',
                titleFont: { size: 12, weight: '600' },
                bodyFont: { size: 11 },
            },
        },
        scales: {
            x: {
                grid: { color: c.grid, drawBorder: false },
                ticks: { color: c.tick, maxTicksLimit: 8, font: { size: 10 } },
            },
            y: {
                min: 0,
                grid: { color: c.grid, drawBorder: false },
                ticks: { color: c.tick, font: { size: 10 } },
            },
        },
        ...overrides,
    };
}

export function buildLineDatasets(theme, series) {
    const c = getChartColors(theme);
    const palette = [
        { border: c.secondary, fill: c.secondaryFill },
        { border: c.primary, fill: c.primaryFill },
    ];
    return series.map((s, i) => ({
        label: s.label,
        data: s.data,
        borderColor: s.color || palette[i % palette.length].border,
        backgroundColor: s.fill || palette[i % palette.length].fill,
        fill: true,
        tension: 0.4,
        pointRadius: 0,
        borderWidth: 2,
    }));
}
