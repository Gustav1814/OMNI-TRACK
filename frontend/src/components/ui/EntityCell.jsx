import React from 'react';

/** Name + meta with a neutral monogram. `color` is accepted for compatibility and ignored. */
// eslint-disable-next-line no-unused-vars
export default function EntityCell({ name, meta, avatar, color }) {
    const initial = (name || '?').charAt(0).toUpperCase();
    return (
        <div className="ui-entity">
            <div className="ui-entity-avatar" aria-hidden={!avatar}>{avatar || initial}</div>
            <div className="ui-entity-copy">
                <span className="ui-entity-name">{name}</span>
                {meta ? <span className="ui-entity-meta">{meta}</span> : null}
            </div>
        </div>
    );
}
