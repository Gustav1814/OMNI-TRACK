import React from 'react';

/** Honest empty state (`.empty-state`): what is missing, why, and the next step. */
export default function EmptyState({
    icon: Icon,
    title,
    description,
    action = undefined,
    compact = false,
    className = '',
}) {
    return (
        <div className={`empty-state ui-empty ${compact ? 'ui-empty--compact' : ''} ${className}`.trim()}>
            {Icon ? <Icon size={compact ? 16 : 20} strokeWidth={1.75} className="ui-empty-icon" aria-hidden /> : null}
            {title ? <h4 className="empty-state-title">{title}</h4> : null}
            {description ? <p className="ui-empty-desc">{description}</p> : null}
            {action ? <div className="ui-empty-action">{action}</div> : null}
        </div>
    );
}
