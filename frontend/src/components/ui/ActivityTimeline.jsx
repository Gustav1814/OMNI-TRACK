import React from 'react';

function relativeTime(ts) {
    if (!ts) return '';
    const diff = Date.now() - new Date(ts).getTime();
    if (diff < 60000) return 'Just now';
    if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
    if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
    return new Date(ts).toLocaleTimeString();
}

export default function ActivityTimeline({ items = [], renderMessage, badgeClass, badgeLabel }) {
    if (!items.length) return null;

    return (
        <ul className="ui-timeline">
            {items.map((item, i) => {
                const type = item.type || 'default';
                const badge = badgeLabel ? badgeLabel(type) : type;
                const cls = badgeClass ? badgeClass(type) : type;
                const msg = renderMessage ? renderMessage(item.type, item.data) : item.message;

                return (
                    <li key={item.id || i} className="ui-timeline-item">
                        <span className="ui-timeline-dot" aria-hidden="true" />
                        <div className="ui-timeline-card">
                            <div className="ui-timeline-head">
                                <span className={`ui-timeline-badge ui-timeline-badge--${cls}`}>{badge}</span>
                                <time className="ui-timeline-time">{relativeTime(item.timestamp)}</time>
                            </div>
                            <p className="ui-timeline-copy">{msg}</p>
                        </div>
                    </li>
                );
            })}
        </ul>
    );
}
