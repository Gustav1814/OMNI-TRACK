import { useEffect, useMemo, useState } from 'react';
import { AUTH_CHANGE_EVENT, tokenStore } from '../services/api';

function roleFromToken() {
    const token = tokenStore.get();
    if (!token) return null;
    try {
        const payload = token.split('.')[1];
        const normalized = payload.replace(/-/g, '+').replace(/_/g, '/');
        const json = atob(normalized.padEnd(normalized.length + ((4 - normalized.length % 4) % 4), '='));
        return JSON.parse(json)?.role || null;
    } catch {
        return null;
    }
}

export default function useUserRole() {
    const [revision, setRevision] = useState(0);

    useEffect(() => {
        const bump = () => setRevision((value) => value + 1);
        window.addEventListener('storage', bump);
        window.addEventListener(AUTH_CHANGE_EVENT, bump);
        return () => {
            window.removeEventListener('storage', bump);
            window.removeEventListener(AUTH_CHANGE_EVENT, bump);
        };
    }, []);

    return useMemo(() => {
        const user = tokenStore.getUser();
        const role = String(user?.role || roleFromToken() || 'viewer').toLowerCase();
        return {
            user,
            role,
            isAdmin: role === 'admin',
            isOperator: role === 'operator',
            isViewer: role === 'viewer',
            canOperate: role === 'admin' || role === 'operator',
            canAdmin: role === 'admin',
        };
    }, [revision]);
}
