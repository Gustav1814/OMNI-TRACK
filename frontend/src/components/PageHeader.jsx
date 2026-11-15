/**
 * Page header: optional OSD kicker, display-size title, subtitle and actions.
 * `highlight` is appended to the title in a quieter ink (no gradient, no colour).
 */
import React from 'react';

export default function PageHeader({
    kicker,
    title,
    highlight,
    subtitle,
    children = undefined,
    className = '',
}) {
    return (
        <header className={`page-header ${className}`.trim()}>
            <div className="page-header-copy">
                {kicker ? <p className="osd page-kicker">{kicker}</p> : null}
                <h1 className="page-title">
                    {title}
                    {highlight ? <span className="page-title-quiet"> {highlight}</span> : null}
                </h1>
                {subtitle ? <p className="page-subtitle">{subtitle}</p> : null}
            </div>
            {children ? <div className="page-header-actions">{children}</div> : null}
        </header>
    );
}
