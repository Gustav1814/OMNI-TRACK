import React from 'react';

/**
 * Titled panel. `accent` is accepted for backward compatibility and ignored:
 * the Viewfinder system keeps panels neutral.
 */
export default function ContentCard({
    title = undefined,
    subtitle = undefined,
    children,
    actions = undefined,
    // eslint-disable-next-line no-unused-vars
    accent = undefined,
    className = '',
    bodyClassName = '',
}) {
    return (
        <section className={`panel ui-content-card ${className}`.trim()}>
            {(title || subtitle || actions) ? (
                <header className="ui-content-card-header">
                    <div className="ui-content-card-heading">
                        {title ? <h3 className="panel-title ui-content-card-title">{title}</h3> : null}
                        {subtitle ? <p className="panel-sub ui-content-card-sub">{subtitle}</p> : null}
                    </div>
                    {actions ? <div className="ui-content-card-actions">{actions}</div> : null}
                </header>
            ) : null}
            <div className={`ui-content-card-body ${bodyClassName}`.trim()}>{children}</div>
        </section>
    );
}
