import React from 'react';

export default function SegmentedControl({ options, value, onChange, size = 'sm' }) {
    return (
        <div className={`ui-segmented ui-segmented--${size}`} role="tablist">
            {options.map((opt) => {
                const id = typeof opt === 'string' ? opt : opt.value;
                const label = typeof opt === 'string' ? opt : opt.label;
                const active = value === id;
                return (
                    <button
                        key={id}
                        type="button"
                        role="tab"
                        aria-selected={active}
                        className={`ui-segmented-item ${active ? 'active' : ''}`}
                        onClick={() => onChange(id)}
                    >
                        {label}
                    </button>
                );
            })}
        </div>
    );
}
