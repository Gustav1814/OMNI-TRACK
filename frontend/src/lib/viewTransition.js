import { flushSync } from 'react-dom';

/**
 * Run a React state update as a View Transition, so layout changes (a feed growing to
 * full width, collapsing to a row) animate instead of jumping. Falls back to a plain
 * update where the API is missing or the user prefers reduced motion.
 */
export function prefersReducedMotion() {
    return typeof window !== 'undefined' && Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches);
}

export default function withViewTransition(update) {
    if (typeof document === 'undefined' || !document.startViewTransition || prefersReducedMotion()) {
        update();
        return;
    }
    document.startViewTransition(() => flushSync(update));
}
