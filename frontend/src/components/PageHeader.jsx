/**
 * Executive page header — magic-card shell, no top accent line.
 */
import React from 'react';
import { motion } from 'framer-motion';
import MagicCard from './ui/MagicCard';

export default function PageHeader({
    kicker,
    title,
    highlight,
    subtitle,
    children,
    className = '',
}) {
    return (
        <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
        >
            <MagicCard
                className={`exec-page-header magic-page-header ${className}`.trim()}
                borderRadius={16}
            >
                <div className="exec-page-header-layout">
                    <div className="exec-page-header-copy">
                        {kicker ? <p className="exec-kicker">{kicker}</p> : null}
                        <h1 className="exec-title">
                            {title}
                            {highlight ? <span className="exec-title-accent"> {highlight}</span> : null}
                        </h1>
                        {subtitle ? <p className="exec-subtitle">{subtitle}</p> : null}
                    </div>
                    {children ? <div className="exec-page-header-actions">{children}</div> : null}
                </div>
            </MagicCard>
        </motion.div>
    );
}
