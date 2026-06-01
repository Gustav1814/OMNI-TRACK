import React from 'react';
import MagicCard from './MagicCard';

/** Page title block with magic-card border (no top accent line). */
export default function MagicPageHeader({ children, className = '', actions }) {
    return (
        <MagicCard className={`page-header magic-page-header ${className}`.trim()} borderRadius={16}>
            <div className="page-header-inner">
                <div className="page-header-copy">{children}</div>
                {actions ? <div className="page-header-actions">{actions}</div> : null}
            </div>
        </MagicCard>
    );
}
