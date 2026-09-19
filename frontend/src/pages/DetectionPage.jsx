/**
 * OmniTrack AI — Recordings
 *
 * Two tabs over two different kinds of video:
 *
 *   Source clips        what you UPLOAD — the footage a job points at. This is
 *                       the library the Add Job modal offers as a source.
 *   Detection captures  what a job PRODUCED — cropped objects and annotated
 *                       clips written to shared/ais1 while the job ran.
 *
 * They were separate concepts with only the first one visible, so the ~24k
 * captures on disk had nowhere to be seen.
 */

import React, { useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import {
    Film, HardDrive, Images, Play, Search, Upload, Video, X,
} from 'lucide-react';
import { footageAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import CaptureBrowser from '../components/captures/CaptureBrowser';

const MB = 1024 * 1024;

function formatSize(bytes) {
    const mb = (bytes || 0) / MB;
    return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb.toFixed(1)} MB`;
}

function formatWhen(ts) {
    if (!ts) return '—';
    const d = new Date(ts * 1000);
    const mins = Math.floor((Date.now() - d.getTime()) / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins} min ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs} hour${hrs === 1 ? '' : 's'} ago`;
    const days = Math.floor(hrs / 24);
    if (days < 7) return `${days} day${days === 1 ? '' : 's'} ago`;
    return d.toLocaleDateString();
}

/** The upload endpoint prefixes camera_{id}_{epoch}_; show the original name. */
function displayName(filename) {
    const m = filename.match(/^camera_\d+_\d+_(.+)$/);
    return (m ? m[1] : filename).replace(/\.[^.]+$/, '');
}

export default function DetectionPage() {
    const [tab, setTab] = useState('captures');

    const { data: footage, refresh } = useLivePoll(
        () => footageAPI.list(), { intervalMs: 10000 },
    );

    const [busy, setBusy] = useState(false);
    const [error, setError] = useState(null);
    const [notice, setNotice] = useState(null);
    const [query, setQuery] = useState('');
    const [playing, setPlaying] = useState(null);
    const fileRef = useRef(null);

    const rows = Array.isArray(footage) ? footage : [];

    const items = useMemo(() => {
        const q = query.trim().toLowerCase();
        const filtered = q
            ? rows.filter((f) => f.filename.toLowerCase().includes(q))
            : rows;
        return [...filtered].sort((a, b) => (b.created_ts || 0) - (a.created_ts || 0));
    }, [rows, query]);

    const totalBytes = useMemo(
        () => rows.reduce((a, f) => a + (f.size_bytes || 0), 0),
        [rows],
    );

    const upload = async (fileList) => {
        if (!fileList || fileList.length === 0) return;
        setBusy(true); setError(null); setNotice(null);
        try {
            await footageAPI.upload(fileList[0], 1);
            setNotice(`${fileList[0].name} uploaded — it is now selectable when you register a job.`);
            await refresh();
        } catch (e) {
            setError(e?.response?.data?.detail || e.message);
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="page-scroll rec-page">
            <header className="rec-head">
                <div>
                    <span className="rec-eyebrow">
                        <Film size={12} aria-hidden />
                        Video workspace
                    </span>
                    <h2 className="rec-title">Recordings</h2>
                    <p className="rec-sub">
                        {tab === 'source'
                            ? 'Clips stored on the backend. These are what a job can point at as its source, so upload footage here before registering one.'
                            : 'What detection produced — cropped objects and annotated clips, filterable by camera, location, object and time.'}
                    </p>
                </div>
                {tab === 'source' && (
                    <label className={`rec-upload${busy ? ' is-busy' : ''}`}>
                        <input
                            ref={fileRef}
                            type="file"
                            accept="video/mp4,video/x-msvideo,video/x-matroska,video/webm,video/quicktime"
                            hidden
                            disabled={busy}
                            onChange={(e) => { upload(e.target.files); e.target.value = ''; }}
                        />
                        <Upload size={15} aria-hidden />
                        {busy ? 'Uploading…' : 'Upload clip'}
                    </label>
                )}
            </header>

            <div className="rec-tabs" role="tablist">
                <button
                    type="button"
                    role="tab"
                    aria-selected={tab === 'source'}
                    className={`rec-tab${tab === 'source' ? ' is-active' : ''}`}
                    onClick={() => setTab('source')}
                >
                    <Film size={13} aria-hidden />
                    Source clips
                    <span className="rec-tab__count">{rows.length}</span>
                </button>
                <button
                    type="button"
                    role="tab"
                    aria-selected={tab === 'captures'}
                    className={`rec-tab${tab === 'captures' ? ' is-active' : ''}`}
                    onClick={() => setTab('captures')}
                >
                    <Images size={13} aria-hidden />
                    Detection captures
                </button>
            </div>

            {tab === 'captures' ? (
                <CaptureBrowser />
            ) : (
                <>
                    <div className="rec-summary">
                        <span className="rec-summary__item">
                            <Film size={13} aria-hidden />
                            <b>{rows.length}</b>
                            <em>clip{rows.length === 1 ? '' : 's'}</em>
                        </span>
                        <span className="rec-summary__dot" aria-hidden />
                        <span className="rec-summary__item">
                            <HardDrive size={13} aria-hidden />
                            <b>{formatSize(totalBytes)}</b>
                            <em>stored</em>
                        </span>
                    </div>

                    {error && <div className="alert-banner danger">{error}</div>}
                    {notice && <div className="alert-banner success">{notice}</div>}

                    {rows.length > 0 && (
                        <div className="rec-search">
                            <Search size={14} aria-hidden />
                            <input
                                className="rec-search__input"
                                placeholder="Filter by filename…"
                                value={query}
                                onChange={(e) => setQuery(e.target.value)}
                            />
                            {query && (
                                <button type="button" className="rec-search__clear" onClick={() => setQuery('')}>
                                    <X size={13} aria-hidden />
                                </button>
                            )}
                        </div>
                    )}

                    {items.length === 0 ? (
                        <div className="rec-empty">
                            <span className="rec-empty__glyph"><Video size={26} aria-hidden /></span>
                            <h3>{query ? 'No clips match that filter' : 'No recordings yet'}</h3>
                            <p>
                                {query
                                    ? 'Try a different search term.'
                                    : 'Upload a video and it becomes available as a job source. MP4, AVI, MKV, WEBM or MOV.'}
                            </p>
                            {!query && (
                                <button
                                    type="button"
                                    className="rec-upload rec-upload--lg"
                                    onClick={() => fileRef.current?.click()}
                                    disabled={busy}
                                >
                                    <Upload size={16} aria-hidden />
                                    {busy ? 'Uploading…' : 'Upload your first clip'}
                                </button>
                            )}
                        </div>
                    ) : (
                        <div className="rec-grid">
                            {items.map((f) => (
                                <article className="rec-card" key={f.filename}>
                                    <button
                                        type="button"
                                        className="rec-card__thumb"
                                        onClick={() => setPlaying(f)}
                                        title="Preview this clip"
                                    >
                                        <span className="rec-card__play"><Play size={18} aria-hidden /></span>
                                    </button>
                                    <div className="rec-card__body">
                                        <span className="rec-card__name" title={f.filename}>
                                            {displayName(f.filename)}
                                        </span>
                                        <span className="rec-card__meta">
                                            {formatSize(f.size_bytes)} · {formatWhen(f.created_ts)}
                                        </span>
                                    </div>
                                </article>
                            ))}
                        </div>
                    )}
                </>
            )}

            {playing && createPortal(
                <div
                    className="rec-player"
                    role="dialog"
                    aria-modal="true"
                    onClick={() => setPlaying(null)}
                >
                    <div className="rec-player__box" onClick={(e) => e.stopPropagation()}>
                        <header className="rec-player__head">
                            <span title={playing.filename}>{displayName(playing.filename)}</span>
                            <button type="button" onClick={() => setPlaying(null)} aria-label="Close preview">
                                <X size={16} />
                            </button>
                        </header>
                        <video
                            className="rec-player__video"
                            src={footageAPI.serveUrl(playing.filename)}
                            controls
                            autoPlay
                        />
                        <footer className="rec-player__foot">
                            <code>{playing.filename}</code>
                            <span>{formatSize(playing.size_bytes)}</span>
                        </footer>
                    </div>
                </div>,
                document.body,
            )}
        </div>
    );
}
