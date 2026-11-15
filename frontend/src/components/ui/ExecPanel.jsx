import React from 'react';

/** Dashboard section shell: a calm `.panel` with standard padding. */
export default function ExecPanel({ children, className = '' }) {
    return <section className={`panel exec-panel ${className}`.trim()}>{children}</section>;
}
