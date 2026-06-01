/**
 * Executive canvas presets — muted, boardroom-grade accent pairs.
 * Dashboard KPI cards use a separate fixed palette (kpiAccentColors.js).
 */

export const GRADIENT_PRESETS = [
    {
        id: 'boardroom',
        name: 'Aurora Teal',
        description: 'Soft teal with airy sky',
        a: '#2dd4bf',
        b: '#60a5fa',
        magicLight: 'rgba(45, 212, 191, 0.16)',
        magicDark: 'rgba(96, 165, 250, 0.22)',
    },
    {
        id: 'charcoal',
        name: 'Champagne Ink',
        description: 'Champagne gold with cool ink',
        a: '#fbbf24',
        b: '#334155',
        magicLight: 'rgba(251, 191, 36, 0.16)',
        magicDark: 'rgba(51, 65, 85, 0.22)',
    },
    {
        id: 'merlot',
        name: 'Peach Rose',
        description: 'Peach glow with soft rose',
        a: '#fb923c',
        b: '#fda4af',
        magicLight: 'rgba(251, 146, 60, 0.16)',
        magicDark: 'rgba(253, 164, 175, 0.22)',
    },
    {
        id: 'capital',
        name: 'Emerald Mist',
        description: 'Emerald lift with mint haze',
        a: '#34d399',
        b: '#22c55e',
        magicLight: 'rgba(52, 211, 153, 0.16)',
        magicDark: 'rgba(34, 197, 94, 0.22)',
    },
    {
        id: 'private',
        name: 'Royal Iris',
        description: 'Indigo silk with cool sky',
        a: '#6366f1',
        b: '#38bdf8',
        magicLight: 'rgba(99, 102, 241, 0.14)',
        magicDark: 'rgba(56, 189, 248, 0.22)',
    },
    {
        id: 'corporate',
        name: 'Crimson Quartz',
        description: 'Crimson highlight with blush quartz',
        a: '#ef4444',
        b: '#f472b6',
        magicLight: 'rgba(239, 68, 68, 0.14)',
        magicDark: 'rgba(244, 114, 182, 0.22)',
    },
];

const LEGACY_PRESET_IDS = {
    gilded: 'charcoal',
    'rose-gold': 'merlot',
    sapphire: 'corporate',
    burgundy: 'merlot',
    obsidian: 'boardroom',
    prestige: 'capital',
    sunset: 'charcoal',
    arctic: 'corporate',
    forest: 'capital',
    berry: 'merlot',
    copper: 'private',
    dusk: 'boardroom',
    ocean: 'corporate',
    twilight: 'boardroom',
    ember: 'merlot',
};

export const DEFAULT_GRADIENT_ID = 'boardroom';

export function resolvePresetId(id) {
    return LEGACY_PRESET_IDS[id] || id;
}

export function getGradientPreset(id) {
    const resolved = resolvePresetId(id);
    return GRADIENT_PRESETS.find((p) => p.id === resolved) || GRADIENT_PRESETS[0];
}

export function hexToRgb(hex) {
    const n = hex.replace('#', '');
    const v = n.length === 3
        ? n.split('').map((c) => parseInt(c + c, 16))
        : [parseInt(n.slice(0, 2), 16), parseInt(n.slice(2, 4), 16), parseInt(n.slice(4, 6), 16)];
    return `${v[0]}, ${v[1]}, ${v[2]}`;
}

export function getPresetMagicColor(presetId, theme) {
    const preset = getGradientPreset(presetId);
    return theme === 'dark' ? preset.magicDark : preset.magicLight;
}

/** Map UI accent slot names → gradient A or B (rose stays fixed for alerts). */
export function resolveAccentRgb(accent, preset) {
    const a = hexToRgb(preset.a);
    const b = hexToRgb(preset.b);
    switch (accent) {
        case 'sky':
        case 'cyan':
        case 'secondary':
        case 'gold':
            return b;
        case 'rose':
            return '225, 29, 72';
        case 'teal':
        case 'emerald':
        case 'coral':
        case 'amber':
        case 'primary':
        default:
            return a;
    }
}

export function applyGradientToDocument(presetId, theme) {
    const preset = getGradientPreset(presetId);
    const aRgb = hexToRgb(preset.a);
    const bRgb = hexToRgb(preset.b);
    const root = document.documentElement;

    root.dataset.gradient = preset.id;
    root.style.setProperty('--gradient-a', preset.a);
    root.style.setProperty('--gradient-b', preset.b);
    root.style.setProperty('--gradient-a-rgb', aRgb);
    root.style.setProperty('--gradient-b-rgb', bRgb);
    root.style.setProperty('--magic-spotlight', getPresetMagicColor(preset.id, theme));

    root.style.setProperty('--accent-primary', preset.a);
    root.style.setProperty('--accent-secondary', preset.b);
    root.style.setProperty('--exec-teal', preset.a);
    root.style.setProperty('--exec-sky', preset.b);
    root.style.setProperty('--exec-coral', preset.a);
    root.style.setProperty('--exec-emerald', preset.b);
    root.style.setProperty('--accent-cta', `linear-gradient(135deg, ${preset.a} 0%, ${preset.b} 100%)`);
    root.style.setProperty('--accent-soft', `linear-gradient(135deg, rgba(${aRgb}, 0.08), rgba(${bRgb}, 0.04))`);
    root.style.setProperty('--accent-a-soft', `rgba(${aRgb}, 0.08)`);
    root.style.setProperty('--accent-b-soft', `rgba(${bRgb}, 0.06)`);
    root.style.setProperty('--accent-a-muted', `rgba(${aRgb}, 0.16)`);
    root.style.setProperty('--accent-b-muted', `rgba(${bRgb}, 0.12)`);
    root.style.setProperty('--exec-bar-track', `rgba(${aRgb}, 0.1)`);
    root.style.setProperty('--exec-shadow-tint', `rgba(${aRgb}, 0.08)`);
}

export function readGradientFromDocument() {
    if (typeof document === 'undefined') {
        const fallback = GRADIENT_PRESETS[0];
        return {
            a: fallback.a,
            b: fallback.b,
            aRgb: hexToRgb(fallback.a),
            bRgb: hexToRgb(fallback.b),
        };
    }
    const root = document.documentElement;
    const style = getComputedStyle(root);
    const fallback = GRADIENT_PRESETS[0];
    return {
        a: style.getPropertyValue('--gradient-a').trim() || fallback.a,
        b: style.getPropertyValue('--gradient-b').trim() || fallback.b,
        aRgb: style.getPropertyValue('--gradient-a-rgb').trim() || hexToRgb(fallback.a),
        bRgb: style.getPropertyValue('--gradient-b-rgb').trim() || hexToRgb(fallback.b),
    };
}
