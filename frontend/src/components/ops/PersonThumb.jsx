/**
 * Snapshot of one Re-ID identity: the sharpest crop the pipeline embedded for it
 * (GET /api/reid/thumb/{gid}). Falls back to a neutral icon when none is held,
 * e.g. identities restored from the database after a restart.
 *
 * Blob URLs are cached per global ID for a short time so lists that re-poll do not
 * refetch every image; a later, sharper crop replaces the cached one on expiry.
 */
import React, { useEffect, useState } from 'react';
import { User } from 'lucide-react';
import { reidAPI } from '../../services/api';

const HIT_TTL_MS = 60_000;
const MISS_TTL_MS = 15_000;
const MAX_ENTRIES = 200;
const cache = new Map(); // gid -> { url: string | null, at: number, pending?: Promise }

function fresh(entry) {
    if (!entry) return false;
    return Date.now() - entry.at < (entry.url ? HIT_TTL_MS : MISS_TTL_MS);
}

function load(gid) {
    const cur = cache.get(gid);
    if (cur?.pending) return cur.pending;
    if (fresh(cur)) return Promise.resolve(cur.url);
    const pending = reidAPI.thumb(gid)
        .then((res) => URL.createObjectURL(res.data))
        .catch(() => null)
        .then((url) => {
            const prev = cache.get(gid);
            if (prev?.url && prev.url !== url) setTimeout(() => URL.revokeObjectURL(prev.url), HIT_TTL_MS);
            cache.delete(gid);
            cache.set(gid, { url, at: Date.now() });
            while (cache.size > MAX_ENTRIES) {
                const [oldGid, old] = cache.entries().next().value;
                cache.delete(oldGid);
                if (old?.url) setTimeout(() => URL.revokeObjectURL(old.url), HIT_TTL_MS);
            }
            return url;
        });
    cache.set(gid, { ...(cur || { url: null, at: 0 }), pending });
    return pending;
}

export default function PersonThumb({ gid, size = 'sm' }) {
    const [src, setSrc] = useState(() => (fresh(cache.get(gid)) ? cache.get(gid).url : null));
    useEffect(() => {
        if (!gid) return undefined;
        let alive = true;
        load(String(gid)).then((url) => { if (alive) setSrc(url); });
        return () => { alive = false; };
    }, [gid]);
    const cls = `person-thumb person-thumb--${size}`;
    if (!src) {
        return (
            <span className={`${cls} is-empty`} aria-hidden>
                <User size={size === 'lg' ? 24 : 16} strokeWidth={1.75} />
            </span>
        );
    }
    return <img className={cls} src={src} alt={`Snapshot of ${gid}`} loading="lazy" decoding="async" />;
}
