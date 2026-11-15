import React from 'react';
import { ArrowUpRight, ArrowDownRight, Minus } from 'lucide-react';

/** Signed change with an arrow, never colour alone. `value` is a percentage. */
export default function DeltaBadge({ value, label = 'vs yesterday', size = 'sm' }) {
    if (value == null || Number.isNaN(Number(value))) return null;
    const n = Number(value);
    const dir = n > 0 ? 'up' : n < 0 ? 'down' : 'flat';
    const Icon = dir === 'up' ? ArrowUpRight : dir === 'down' ? ArrowDownRight : Minus;
    const text = dir === 'flat' ? '0%' : `${n > 0 ? '+' : ''}${n}%`;

    return (
        <span className={`ui-delta ui-delta--${size}`}>
            <span className={`delta${dir === 'flat' ? '' : ` delta-${dir}`}`}>
                <Icon size={11} strokeWidth={2} aria-hidden />
                {text}
            </span>
            {label ? <span className="ui-delta-label">{label}</span> : null}
        </span>
    );
}
