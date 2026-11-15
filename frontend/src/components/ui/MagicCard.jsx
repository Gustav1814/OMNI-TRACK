import React from 'react';

/**
 * @deprecated Compatibility wrapper. Renders a plain `.panel`.
 * Use `<section className="panel">` (or ContentCard) directly in new code.
 * Colour / spotlight props (gradientSize, gradientColor, gradientOpacity, borderRadius) are ignored.
 */
export default function MagicCard({
    children,
    className = '',
    innerClassName = '',
    // eslint-disable-next-line no-unused-vars
    gradientSize = undefined,
    // eslint-disable-next-line no-unused-vars
    gradientColor = undefined,
    // eslint-disable-next-line no-unused-vars
    gradientOpacity = undefined,
    // eslint-disable-next-line no-unused-vars
    borderRadius = undefined,
    as = 'div',
}) {
    const Tag = /** @type {any} */ (as);
    return (
        <Tag className={`panel ${className}`.trim()}>
            {innerClassName ? <div className={innerClassName}>{children}</div> : children}
        </Tag>
    );
}
