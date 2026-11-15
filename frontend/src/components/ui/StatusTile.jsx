import React from 'react';

/**
 * Small labelled status value. Only real status tones (ok / warn / danger) get a coloured dot,
 * always next to the text label. Brand tones (teal, sky, coral…) render neutral.
 */
const DOT = { ok: 'is-ok', success: 'is-ok', warn: 'is-warn', warning: 'is-warn', rose: 'is-bad', danger: 'is-bad', bad: 'is-bad' };

function resolveTone(ok, tone) {
    if (tone) return tone;
    if (ok === true) return 'ok';
    if (ok === false) return 'warn';
    return 'neutral';
}

export default function StatusTile({
    icon: Icon,
    label,
    value,
    ok = undefined,
    tone = undefined,
    compact = false,
}) {
    const resolved = resolveTone(ok, tone);
    const dot = DOT[resolved];
    return (
        <div className={`ui-status-tile ui-status-tile--${resolved}${compact ? ' ui-status-tile--compact' : ''}`}>
            <div className="ui-status-tile-top">
                {Icon ? <Icon size={14} strokeWidth={1.75} className="ui-status-tile-icon" aria-hidden /> : null}
                <span className="ui-status-tile-label">{label}</span>
            </div>
            <div className="ui-status-tile-value">
                {dot ? <span className={`live-dot ${dot}`} aria-hidden /> : null}
                <span>{String(value)}</span>
            </div>
        </div>
    );
}
