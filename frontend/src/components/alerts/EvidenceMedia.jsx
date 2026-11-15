/**
 * Evidence media: a thumbnail tile and a full viewer (snapshot <img> or clip
 * <video>). Files are decrypted server-side and fetched with ?token=, so they
 * work in plain <img>/<video> elements.
 */

import React, { useEffect, useMemo, useState } from 'react';
import { Film, Image as ImageIcon, ImageOff, Play } from 'lucide-react';
import { evidenceAPI } from '../../services/api';

/** Small thumbnail. Clips get a play badge; failures show a neutral tile, never a fake image. */
export function EvidenceThumb({ item, size = 'sm', onClick = undefined, title = undefined }) {
    const [failed, setFailed] = useState(false);
    const src = useMemo(() => evidenceAPI.thumbUrl(item.id), [item.id]);
    const isClip = item.kind === 'clip';
    const Tag = onClick ? 'button' : 'div';
    return (
        <Tag
            {...(onClick ? { type: 'button', onClick } : {})}
            className={`fp-thumb fp-thumb--${size} ${failed ? 'is-failed' : ''}`}
            title={title || (isClip ? 'Video clip' : 'Snapshot')}
        >
            {failed ? (
                <span className="fp-thumb-fallback">
                    {isClip ? <Film size={size === 'sm' ? 14 : 20} strokeWidth={1.75} /> : <ImageOff size={size === 'sm' ? 14 : 20} strokeWidth={1.75} />}
                </span>
            ) : (
                <img src={src} alt={isClip ? 'Clip preview' : 'Snapshot'} loading="lazy" onError={() => setFailed(true)} />
            )}
            {isClip ? <span className="fp-thumb-play" aria-hidden><Play size={size === 'sm' ? 9 : 12} fill="currentColor" strokeWidth={0} /></span> : null}
        </Tag>
    );
}

/** Full-size viewer with a Snapshot / Clip switch when both exist. */
export function EvidenceViewer({ evidence = [], emptyText = 'No evidence was captured for this alert.' }) {
    const snapshots = evidence.filter((e) => e.kind === 'snapshot');
    const clips = evidence.filter((e) => e.kind === 'clip');
    const ordered = [...clips, ...snapshots];
    const [activeId, setActiveId] = useState(ordered[0]?.id ?? null);
    const [failed, setFailed] = useState({});

    const idsKey = ordered.map((e) => e.id).join(',');
    useEffect(() => {
        setActiveId(ordered[0]?.id ?? null);
        setFailed({});
    }, [idsKey]);

    if (!ordered.length) {
        return (
            <div className="fp-media fp-media--empty">
                <ImageOff size={20} strokeWidth={1.75} aria-hidden />
                <span>{emptyText}</span>
            </div>
        );
    }

    const active = ordered.find((e) => e.id === activeId) || ordered[0];
    const src = evidenceAPI.fileUrl(active.id);
    const markFailed = () => setFailed((f) => ({ ...f, [active.id]: true }));

    return (
        <div className="fp-media-wrap">
            <div className="fp-media vf vf--on-media">
                {failed[active.id] ? (
                    <div className="fp-media-error">
                        <ImageOff size={20} strokeWidth={1.75} aria-hidden />
                        <span>This file is no longer available (it may have passed its retention period).</span>
                    </div>
                ) : active.kind === 'clip' ? (
                    // key forces a fresh element when switching clips
                    <video key={active.id} src={src} controls autoPlay muted playsInline preload="metadata" onError={markFailed} />
                ) : (
                    <img key={active.id} src={src} alt="Alert snapshot" onError={markFailed} />
                )}
            </div>
            {ordered.length > 1 ? (
                <div className="fp-media-strip" role="tablist" aria-label="Evidence files">
                    {ordered.map((e) => (
                        <button
                            key={e.id}
                            type="button"
                            role="tab"
                            aria-selected={e.id === active.id}
                            className={`fp-media-tab ${e.id === active.id ? 'active' : ''}`}
                            onClick={() => setActiveId(e.id)}
                        >
                            {e.kind === 'clip' ? <Film size={14} strokeWidth={1.75} aria-hidden /> : <ImageIcon size={14} strokeWidth={1.75} aria-hidden />}
                            {e.kind === 'clip' ? 'Clip' : 'Snapshot'}
                            {(e.kind === 'clip' ? clips : snapshots).length > 1
                                ? ` ${(e.kind === 'clip' ? clips : snapshots).indexOf(e) + 1}` : ''}
                        </button>
                    ))}
                </div>
            ) : null}
        </div>
    );
}
