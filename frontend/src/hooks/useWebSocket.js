/**
 * OmniTrack AI — WebSocket hook
 * Auto-reconnects, parses JSON frames, dispatches to handlers by `type`.
 *
 * Usage:
 *   const { status, lastEvent, send } = useWebSocket('/ws/live', {
 *     onEvent: (evt) => ...,
 *     onType: { fire_alert: (d) => ..., vibe_update: (d) => ... },
 *   });
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { buildWsUrl } from '../services/api';

const RECONNECT_BASE_MS = 1500;
const RECONNECT_MAX_MS = 15000;

export default function useWebSocket(path = '/ws/live', { onEvent, onType } = {}) {
    const [status, setStatus] = useState('connecting');
    const [lastEvent, setLastEvent] = useState(null);
    const wsRef = useRef(null);
    const attemptRef = useRef(0);
    const stoppedRef = useRef(false);
    const typeMapRef = useRef(onType || {});
    const eventCbRef = useRef(onEvent);

    useEffect(() => { typeMapRef.current = onType || {}; }, [onType]);
    useEffect(() => { eventCbRef.current = onEvent; }, [onEvent]);

    const connect = useCallback(() => {
        if (stoppedRef.current) return;
        const url = buildWsUrl(path);
        let ws;
        try { ws = new WebSocket(url); }
        catch { scheduleReconnect(); return; }
        wsRef.current = ws;
        setStatus('connecting');

        ws.onopen = () => {
            attemptRef.current = 0;
            setStatus('open');
        };
        ws.onclose = () => {
            setStatus('closed');
            scheduleReconnect();
        };
        ws.onerror = () => setStatus('error');
        ws.onmessage = (ev) => {
            let payload;
            try { payload = JSON.parse(ev.data); }
            catch { return; }
            setLastEvent(payload);
            const cb = eventCbRef.current;
            if (cb) { try { cb(payload); } catch { /* noop */ } }
            const typed = typeMapRef.current?.[payload?.type];
            if (typed) { try { typed(payload.data, payload); } catch { /* noop */ } }
        };
    }, [path]);

    function scheduleReconnect() {
        if (stoppedRef.current) return;
        attemptRef.current += 1;
        const delay = Math.min(RECONNECT_BASE_MS * 2 ** (attemptRef.current - 1), RECONNECT_MAX_MS);
        setTimeout(() => connect(), delay);
    }

    useEffect(() => {
        stoppedRef.current = false;
        connect();
        return () => {
            stoppedRef.current = true;
            try { wsRef.current?.close(); } catch { /* noop */ }
        };
    }, [connect]);

    const send = useCallback((obj) => {
        const ws = wsRef.current;
        if (!ws || ws.readyState !== WebSocket.OPEN) return false;
        try { ws.send(typeof obj === 'string' ? obj : JSON.stringify(obj)); return true; }
        catch { return false; }
    }, []);

    return { status, lastEvent, send };
}
