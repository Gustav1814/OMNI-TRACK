import React from 'react';
import useGradientColors from '../../hooks/useGradientColors';

export default function EntityCell({ name, meta, avatar, color }) {
    const { a, b } = useGradientColors();
    const PALETTE = [a, b, a, '#f97316', '#f43f5e', b];

    function colorFromString(str = '') {
        let h = 0;
        for (let i = 0; i < str.length; i += 1) h = str.charCodeAt(i) + ((h << 5) - h);
        return PALETTE[Math.abs(h) % PALETTE.length];
    }

    const initial = (name || '?').charAt(0).toUpperCase();
    const bg = color || colorFromString(name);

    return (
        <div className="ui-entity">
            <div className="ui-entity-avatar" style={{ background: `${bg}18`, color: bg, borderColor: `${bg}40` }}>
                {avatar || initial}
            </div>
            <div className="ui-entity-copy">
                <span className="ui-entity-name">{name}</span>
                {meta ? <span className="ui-entity-meta">{meta}</span> : null}
            </div>
        </div>
    );
}
