/**
 * OmniTrack AI — Polling hook
 * Polls an async function on an interval, pauses on hidden tabs, exposes
 * data/error/loading and a `refresh()` to trigger a manual poll.
 *
 * Usage:
 *   const { data, error, loading, refresh } = useLivePoll(
 *     () => crowdAPI.status(),
 *     { intervalMs: 5000 }
 *   );
 */

import { useCallback, useEffect, useRef, useState } from 'react';

export default function useLivePoll(fetchFn, { intervalMs = 5000, enabled = true } = {}) {
    const [data, setData] = useState(null);
    const [error, setError] = useState(null);
    const [loading, setLoading] = useState(true);
    const timerRef = useRef(null);
    const mountedRef = useRef(true);
    const fetchFnRef = useRef(fetchFn);

    useEffect(() => { fetchFnRef.current = fetchFn; }, [fetchFn]);

    const doPoll = useCallback(async () => {
        if (!mountedRef.current) return;
        try {
            const res = await fetchFnRef.current();
            if (!mountedRef.current) return;
            const payload = res && typeof res === 'object' && 'data' in res ? res.data : res;
            setData(payload);
            setError(null);
        } catch (e) {
            if (!mountedRef.current) return;
            setError(e);
        } finally {
            if (mountedRef.current) setLoading(false);
        }
    }, []);

    const schedule = useCallback(() => {
        clearTimeout(timerRef.current);
        if (!enabled) return;
        timerRef.current = setTimeout(async () => {
            if (document.visibilityState !== 'hidden') await doPoll();
            schedule();
        }, intervalMs);
    }, [doPoll, intervalMs, enabled]);

    useEffect(() => {
        mountedRef.current = true;
        if (enabled) doPoll();
        schedule();
        const onVis = () => {
            if (document.visibilityState === 'visible' && enabled) doPoll();
        };
        document.addEventListener('visibilitychange', onVis);
        return () => {
            mountedRef.current = false;
            clearTimeout(timerRef.current);
            document.removeEventListener('visibilitychange', onVis);
        };
    }, [doPoll, schedule, enabled]);

    return { data, error, loading, refresh: doPoll };
}
