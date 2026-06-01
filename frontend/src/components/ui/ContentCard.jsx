import React from 'react';
import MagicCard from './MagicCard';

export default function ContentCard({
    title,
    subtitle,
    children,
    actions,
    className = '',
    bodyClassName = '',
}) {
    return (
        <section className={`ui-content-card-shell ${className}`.trim()}>
            <MagicCard className="ui-content-card" borderRadius={16}>
                {(title || subtitle || actions) ? (
                    <header className="ui-content-card-header">
                        <div className="ui-content-card-heading">
                            {title ? <h3 className="ui-content-card-title">{title}</h3> : null}
                            {subtitle ? <p className="ui-content-card-sub">{subtitle}</p> : null}
                        </div>
                        {actions ? <div className="ui-content-card-actions">{actions}</div> : null}
                    </header>
                ) : null}
                <div className={`ui-content-card-body ${bodyClassName}`.trim()}>{children}</div>
            </MagicCard>
        </section>
    );
}
