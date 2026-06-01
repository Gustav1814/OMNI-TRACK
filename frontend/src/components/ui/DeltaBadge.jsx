import React from 'react';
import { TrendingDown, TrendingUp, Minus } from 'lucide-react';

export default function DeltaBadge({ value, label = 'vs yesterday', size = 'sm' }) {
    if (value == null || Number.isNaN(Number(value))) return null;
    const n = Number(value);
    const up = n > 0;
    const flat = n === 0;
    const cls = flat ? 'flat' : up ? 'up' : 'down';
    const Icon = flat ? Minus : up ? TrendingUp : TrendingDown;
    const text = flat ? '0%' : `${up ? '+' : ''}${n}%`;

    return (
        <span className={`ui-delta ui-delta--${cls} ui-delta--${size}`}>
            <Icon size={size === 'xs' ? 10 : 12} />
            {text}
            {label ? <span className="ui-delta-label">{label}</span> : null}
        </span>
    );
}
