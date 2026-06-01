import React from 'react';

export default function EmptyState({
    icon: Icon,
    title,
    description,
    action,
    compact = false,
    className = '',
}) {
    return (
        <div className={`ui-empty ${compact ? 'ui-empty--compact' : ''} ${className}`.trim()}>
            {Icon ? (
                <div className="ui-empty-icon">
                    <Icon size={compact ? 22 : 28} strokeWidth={1.75} />
                </div>
            ) : null}
            {title ? <h4 className="ui-empty-title">{title}</h4> : null}
            {description ? <p className="ui-empty-desc">{description}</p> : null}
            {action ? <div className="ui-empty-action">{action}</div> : null}
        </div>
    );
}
