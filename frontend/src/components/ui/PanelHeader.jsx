import React from 'react';

export default function PanelHeader({ title, subtitle, actions, badge, className = '' }) {
    return (
        <div className={`ui-panel-header ${className}`.trim()}>
            <div className="ui-panel-header-copy">
                <div className="ui-panel-header-title-row">
                    <h3 className="ui-panel-header-title">{title}</h3>
                    {badge ? <span className="ui-panel-header-badge">{badge}</span> : null}
                </div>
                {subtitle ? <p className="ui-panel-header-sub">{subtitle}</p> : null}
            </div>
            {actions ? <div className="ui-panel-header-actions">{actions}</div> : null}
        </div>
    );
}
