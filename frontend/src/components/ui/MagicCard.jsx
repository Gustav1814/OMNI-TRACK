import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useTheme } from '../../contexts/ThemeContext';
import { getPresetMagicColor } from '../../lib/gradientPresets';

/**
 * Magic UI–style card — cursor-following radial spotlight, theme-aware.
 */
export default function MagicCard({
    children,
    className = '',
    innerClassName = '',
    gradientSize = 280,
    gradientColor,
    gradientOpacity = 1,
    borderRadius = 16,
    as: Tag = 'div',
}) {
    const cardRef = useRef(null);
    const { theme, gradientPreset } = useTheme();
    const [pos, setPos] = useState({ x: 0, y: 0 });
    const [active, setActive] = useState(false);

    const spotColor = gradientColor ?? getPresetMagicColor(gradientPreset, theme);

    const onMove = useCallback((e) => {
        const el = cardRef.current;
        if (!el) return;
        const r = el.getBoundingClientRect();
        setPos({ x: e.clientX - r.left, y: e.clientY - r.top });
    }, []);

    useEffect(() => {
        const el = cardRef.current;
        if (!el) return undefined;
        el.addEventListener('mousemove', onMove);
        return () => el.removeEventListener('mousemove', onMove);
    }, [onMove]);

    const innerRadius = Math.max(borderRadius - 1, 0);

    return (
        <Tag
            ref={cardRef}
            className={`magic-card ${className}`.trim()}
            style={{ '--magic-radius': `${borderRadius}px`, borderRadius: `${borderRadius}px` }}
            onMouseEnter={() => setActive(true)}
            onMouseLeave={() => setActive(false)}
        >
            <div
                className="magic-card-spotlight"
                aria-hidden
                style={{
                    opacity: active ? gradientOpacity : 0,
                    background: `radial-gradient(${gradientSize}px circle at ${pos.x}px ${pos.y}px, ${spotColor}, transparent 100%)`,
                }}
            />
            <div
                className={`magic-card-inner ${innerClassName}`.trim()}
                style={{ borderRadius: `${innerRadius}px` }}
            >
                {children}
            </div>
        </Tag>
    );
}
