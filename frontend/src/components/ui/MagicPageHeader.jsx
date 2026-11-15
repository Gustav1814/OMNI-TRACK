import React from 'react';

/**
 * @deprecated Compatibility wrapper. Renders the same markup as PageHeader
 * (`.page-header`). Use `components/PageHeader` in new code.
 */
export default function MagicPageHeader({ children, className = '', actions = undefined }) {
    return (
        <header className={`page-header ${className}`.trim()}>
            <div className="page-header-copy">{children}</div>
            {actions ? <div className="page-header-actions">{actions}</div> : null}
        </header>
    );
}
