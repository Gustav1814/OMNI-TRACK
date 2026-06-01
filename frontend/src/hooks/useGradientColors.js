import { useTheme } from '../contexts/ThemeContext';
import { getGradientPreset, hexToRgb, resolveAccentRgb } from '../lib/gradientPresets';

/** Active canvas gradient pair from Settings. */
export function useGradientColors() {
    const { gradientPreset, theme } = useTheme();
    const preset = getGradientPreset(gradientPreset);
    return {
        preset,
        theme,
        a: preset.a,
        b: preset.b,
        aRgb: hexToRgb(preset.a),
        bRgb: hexToRgb(preset.b),
        accentRgb: (accent) => resolveAccentRgb(accent, preset),
    };
}

export default useGradientColors;
