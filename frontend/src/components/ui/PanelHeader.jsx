import React from 'react';

export default function PanelHeader({ title, subtitle = undefined, actions = undefined, badge = undefined, className = '' }) {
    return (
        <div className={`panel-head ui-panel-header ${className}`.trim()}>
            <div className="ui-panel-header-copy">
                <div className="ui-panel-header-title-row">
                    <h3 className="panel-title ui-panel-header-title">{title}</h3>
                    {badge ? <span className="pill pill-plain ui-panel-header-badge">{badge}</span> : null}
                </div>
                {subtitle ? <p className="panel-sub ui-panel-header-sub">{subtitle}</p> : null}
            </div>
            {actions ? <div className="ui-panel-header-actions">{actions}</div> : null}
        </div>
    );
}
