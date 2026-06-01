import React from 'react';
import MagicCard from './MagicCard';

/** Dashboard section shell — magic-card border, no top accent line. */
export default function ExecPanel({ children, className = '' }) {
    return (
        <MagicCard className={`exec-panel ${className}`.trim()} borderRadius={16}>
            {children}
        </MagicCard>
    );
}
