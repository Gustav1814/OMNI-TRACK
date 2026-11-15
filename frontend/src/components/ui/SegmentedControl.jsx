import React from 'react';

/** Segmented pill toggle (`.seg`): active option is ink-filled and `aria-pressed`. */
export default function SegmentedControl({ options, value, onChange, size = 'sm', ariaLabel = undefined }) {
    return (
        <div className={`seg ui-segmented ui-segmented--${size}`} role="group" aria-label={ariaLabel}>
            {options.map((opt) => {
                const id = typeof opt === 'string' ? opt : opt.value;
                const label = typeof opt === 'string' ? opt : opt.label;
                const active = value === id;
                return (
                    <button
                        key={id}
                        type="button"
                        aria-pressed={active}
                        className={`ui-segmented-item ${active ? 'active' : ''}`.trim()}
                        onClick={() => onChange(id)}
                    >
                        {label}
                    </button>
                );
            })}
        </div>
    );
}
