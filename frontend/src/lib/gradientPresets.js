/**
 * Executive canvas presets — premium-curated accent pairs.
 * Dashboard KPI cards use a separate fixed palette (kpiAccentColors.js).
 */

export const GRADIENT_PRESETS = [
    {
        id: 'ocean-depth',
        name: 'Ocean Depth',
        description: 'Deep emerald and navy silk mesh',
        a: '#0d6b63',
        b: '#1a365d',
        magicLight: 'rgba(13, 107, 99, 0.14)',
        magicDark: 'rgba(26, 54, 93, 0.26)',
    },
    {
        id: 'obsidian',
        name: 'Obsidian Steel',
        description: 'Cool steel with calm blue accent',
        a: '#94a3b8',
        b: '#60a5fa',
        magicLight: 'rgba(148, 163, 184, 0.14)',
        magicDark: 'rgba(96, 165, 250, 0.22)',
    },
    {
        id: 'boardroom',
        name: 'Gilded Noir',
        description: 'Rich warm gold — luxury watch finish',
        a: '#d4a55a',
        b: '#b8862e',
        magicLight: 'rgba(212, 165, 90, 0.14)',
        magicDark: 'rgba(184, 134, 46, 0.20)',
    },
    {
        id: 'merlot',
        name: 'Rose Quartz',
        description: 'Rich dusty rose with warm mauve',
        a: '#d4748a',
        b: '#b85c72',
        magicLight: 'rgba(212, 116, 138, 0.14)',
        magicDark: 'rgba(184, 92, 114, 0.20)',
    },
    {
        id: 'capital',
        name: 'Emerald Depth',
        description: 'Vivid emerald with forest undertone',
        a: '#34d399',
        b: '#10b981',
        magicLight: 'rgba(52, 211, 153, 0.14)',
        magicDark: 'rgba(16, 185, 129, 0.20)',
    },
    {
        id: 'charcoal',
        name: 'Amber Blaze',
        description: 'Warm amber glow — burnished sunset',
        a: '#f0a050',
        b: '#d48a38',
        magicLight: 'rgba(240, 160, 80, 0.14)',
        magicDark: 'rgba(212, 138, 56, 0.20)',
    },
    {
        id: 'private',
        name: 'Royal Indigo',
        description: 'Deep violet-blue — evening twilight',
        a: '#818cf8',
        b: '#6366f1',
        magicLight: 'rgba(129, 140, 248, 0.14)',
        magicDark: 'rgba(99, 102, 241, 0.20)',
    },
    {
        id: 'corporate',
        name: 'Arctic Cyan',
        description: 'Icy fresh cyan — glacial clarity',
        a: '#67e8f9',
        b: '#22d3ee',
        magicLight: 'rgba(103, 232, 249, 0.14)',
        magicDark: 'rgba(34, 211, 238, 0.20)',
    },
    {
        id: 'aurora',
        name: 'Copper Patina',
        description: 'Burnished copper with bronze warmth',
        a: '#c4896a',
        b: '#a87352',
        magicLight: 'rgba(196, 137, 106, 0.14)',
        magicDark: 'rgba(168, 115, 82, 0.20)',
    },
];

const LEGACY_PRESET_IDS = {
    gilded: 'boardroom',
    'rose-gold': 'merlot',
    sapphire: 'corporate',
    burgundy: 'merlot',
    prestige: 'capital',
    sunset: 'charcoal',
    arctic: 'ocean-depth',
    ocean: 'ocean-depth',
    forest: 'capital',
    berry: 'merlot',
    copper: 'private',
    dusk: 'boardroom',
    ocean: 'private',
    twilight: 'boardroom',
    ember: 'merlot',
};

export const DEFAULT_GRADIENT_ID = 'ocean-depth';

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

    /* Glass-card system — soft accent-tinted dark-mode tokens */
    root.style.setProperty('--glass-card-bg', `rgba(${aRgb}, 0.01)`);
    root.style.setProperty('--glass-card-border', `rgba(${aRgb}, 0.06)`);
    root.style.setProperty('--glass-card-shadow-tint', `rgba(${aRgb}, 0.04)`);
    root.style.setProperty('--glass-card-highlight', `rgba(${aRgb}, 0.04)`);
    root.style.setProperty('--glass-card-surface',
        `linear-gradient(145deg, rgba(${aRgb}, 0.02), rgba(${bRgb}, 0.008) 60%, transparent)`
    );
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
