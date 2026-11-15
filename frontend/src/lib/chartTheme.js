/**
 * Shared chart styling (Chart.js + Recharts), driven entirely by design tokens.
 * Pass the object from useTokens() — never literal colours.
 */
import { withAlpha } from './tokens';

/** Chart.js line options. `t` = useTokens() snapshot. */
export function buildLineChartOptions(t, overrides = {}) {
    const tick = { color: t.axis, font: { family: t.fontMono, size: 10 } };
    return {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 250 },
        interaction: { intersect: false, mode: 'index' },
        plugins: {
            legend: { display: false },
            tooltip: {
                backgroundColor: t.tooltipBg,
                titleColor: t.tooltipText,
                bodyColor: t.tooltipText,
                borderWidth: 0,
                padding: 10,
                cornerRadius: 6,
                displayColors: true,
                boxWidth: 8,
                boxHeight: 8,
                usePointStyle: true,
                titleFont: { family: t.fontSans, size: 12, weight: 600 },
                bodyFont: { family: t.fontMono, size: 11 },
            },
        },
        scales: {
            x: {
                grid: { display: false },
                border: { color: t.lineStrong },
                ticks: { ...tick, maxTicksLimit: 8, maxRotation: 0 },
            },
            y: {
                min: 0,
                grid: { color: t.grid },
                border: { display: false },
                ticks: { ...tick, maxTicksLimit: 5, padding: 8 },
            },
        },
        ...overrides,
    };
}

/** Chart.js line datasets in fixed categorical order (series i → --chart-(i+1)). */
export function buildLineDatasets(t, series) {
    return series.map((s, i) => {
        const color = s.color || t.series[i % t.series.length];
        return {
            label: s.label,
            data: s.data,
            borderColor: color,
            backgroundColor: withAlpha(color, series.length === 1 ? 0.12 : 0.06),
            fill: s.fill ?? series.length === 1,
            tension: 0.3,
            pointRadius: 0,
            pointHoverRadius: 4,
            pointHoverBorderWidth: 2,
            pointHoverBorderColor: t.surface,
            pointHoverBackgroundColor: color,
            borderWidth: 2,
        };
    });
}

/** Props shared by every Recharts chart: axes, grid, tooltip. */
export function rechartsTheme(t) {
    const tick = { fill: t.axis, fontSize: 10, fontFamily: t.fontMono };
    return {
        grid: { stroke: t.grid, strokeDasharray: '0', vertical: false },
        xAxis: { tick, tickLine: false, axisLine: { stroke: t.lineStrong }, minTickGap: 16 },
        yAxis: { tick, tickLine: false, axisLine: false, width: 40 },
        tooltip: {
            cursor: { stroke: t.lineStrong, strokeWidth: 1, fill: withAlpha(t.text, 0.04) },
            contentStyle: {
                background: t.tooltipBg,
                border: 'none',
                borderRadius: 6,
                padding: '8px 10px',
                fontFamily: t.fontMono,
                fontSize: 11,
                boxShadow: 'none',
            },
            labelStyle: { color: t.tooltipText, fontFamily: t.fontSans, fontWeight: 600, marginBottom: 4 },
            itemStyle: { color: t.tooltipText, padding: 0 },
        },
        legend: { wrapperStyle: { fontSize: 12, color: t.text2 }, iconType: 'circle', iconSize: 8 },
        series: t.series,
    };
}
