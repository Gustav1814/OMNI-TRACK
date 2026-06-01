/** Fixed dashboard KPI palette — never tied to canvas gradient preset */

export const KPI_ACCENT_RGB = {
    teal: '13, 148, 136',
    cyan: '6, 182, 212',
    sky: '14, 165, 233',
    coral: '249, 115, 22',
    emerald: '16, 185, 129',
    rose: '225, 29, 72',
    gold: '202, 138, 4',
};

export function resolveKpiAccentRgb(accent) {
    return KPI_ACCENT_RGB[accent] || KPI_ACCENT_RGB.teal;
}

export const KPI_HERO_COLORS = {
    teal: '#0d9488',
    sky: '#0ea5e9',
    coral: '#f97316',
    emerald: '#10b981',
};
