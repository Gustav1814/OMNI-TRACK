/**
 * OmniTrack AI — License Plate Recognition Demo
 * Accepts images or video sources and runs ALPR inference through the backend.
 */

import React, { useState, useEffect, useRef } from 'react';
import { UploadCloud, AlertTriangle, Link2 } from 'lucide-react';
import ContentCard from '../components/ui/ContentCard';
import SegmentedControl from '../components/ui/SegmentedControl';
import api, { API_BASE, licensePlateAPI, tokenStore } from '../services/api';

const DEFAULT_DETECTOR_MODEL = 'license_plate_detection.pt';
const DEFAULT_OCR_MODEL = 'cct-s-v2-global-model';
const OCR_DISPLAY_LABEL = 'fast-alpr/cct-s-v2';
const DETECTOR_MODELS = [DEFAULT_DETECTOR_MODEL];
const OCR_MODELS = [
    'cct-s-v2-global-model',
    'cct-xs-v2-global-model',
    'cct-s-v1-global-model',
    'cct-xs-v1-global-model',
];
const MODEL_LABELS = {
    'license_plate_detection.pt': 'License Plate Detector',
    'cct-s-v2-global-model': 'fast-alpr cct-s-v2',
    'cct-xs-v2-global-model': 'fast-alpr cct-xs-v2',
    'cct-s-v1-global-model': 'fast-alpr cct-s-v1',
    'cct-xs-v1-global-model': 'fast-alpr cct-xs-v1',
};

export default function LicensePlatePage() {
    const [mediaType, setMediaType] = useState('image');
    const [inputType, setInputType] = useState('file');
    const [selectedFile, setSelectedFile] = useState(null);
    const [sourceUrl, setSourceUrl] = useState('');
    const [detectorModel, setDetectorModel] = useState(DEFAULT_DETECTOR_MODEL);
    const [ocrModel, setOcrModel] = useState(DEFAULT_OCR_MODEL);
    const [detectionThreshold, setDetectionThreshold] = useState(0.4);
    const [ocrThreshold, setOcrThreshold] = useState(0.0);
    const [result, setResult] = useState(null);
    const [liveLogs, setLiveLogs] = useState([]);
    const [streaming, setStreaming] = useState(false);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    const [activeResultTab, setActiveResultTab] = useState('live');
    const abortController = useRef(null);

    const snapshots = Array.isArray(result?.snapshots) ? result.snapshots : [];
    const logs = liveLogs.length ? liveLogs : (Array.isArray(result?.logs) ? result.logs : []);
    const platesDetected = result?.plates_detected ?? 0;
    const framesProcessed = result?.frames_processed ?? 0;

    const resetForm = () => {
        setSelectedFile(null);
        setSourceUrl('');
        setDetectorModel(DEFAULT_DETECTOR_MODEL);
        setOcrModel(DEFAULT_OCR_MODEL);
        setResult(null);
        setLiveLogs([]);
        setStreaming(false);
        setError('');
        setBusy(false);
        setActiveResultTab('live');
    };

    const handleFileChange = (files) => {
        if (!files?.length) return;
        setSelectedFile(files[0]);
        setError('');
        setResult(null);
    };

    useEffect(() => {
        return () => {
            if (abortController.current) {
                abortController.current.abort();
            }
        };
    }, []);

    const hasValidSource = () => {
        if (inputType === 'file') return Boolean(selectedFile);
        return Boolean(sourceUrl.trim());
    };

    const validateSource = () => {
        if (inputType === 'file' && !selectedFile) {
            setError(`Please choose a ${mediaType} file.`);
            return false;
        }
        if (inputType === 'link' && !sourceUrl.trim()) {
            setError('Please paste a valid link.');
            return false;
        }
        return true;
    };

    const submitRecognition = async () => {
        if (!validateSource()) return;
        if (abortController.current) {
            abortController.current.abort();
        }
        abortController.current = new AbortController();
        setError('');
        setBusy(true);
        setStreaming(false);
        setResult(null);

        const form = new FormData();
        form.append('media_type', mediaType);
        form.append('detector_model', detectorModel);
        form.append('ocr_model', ocrModel);
        form.append('detection_threshold', detectionThreshold);
        form.append('ocr_threshold', ocrThreshold);
        if (inputType === 'file' && selectedFile) {
            form.append('file', selectedFile);
        }
        if (inputType === 'link' && sourceUrl.trim()) {
            form.append('source_url', sourceUrl.trim());
        }

        if (mediaType === 'video') {
            setStreaming(true);
            const token = tokenStore.get();
            try {
                const response = await fetch(`${API_BASE}/license-plate/recognize/stream`, {
                    method: 'POST',
                    body: form,
                    signal: abortController.current.signal,
                    headers: token ? { Authorization: `Bearer ${token}` } : {},
                });

                if (!response.ok) {
                    const text = await response.text();
                    throw new Error(text || `Stream failed with status ${response.status}`);
                }

                const reader = response.body.getReader();
                const decoder = new TextDecoder('utf-8');
                let buffer = '';
                let completed = false;

                while (true) {
                    const { value, done } = await reader.read();
                    if (done) break;
                    buffer += decoder.decode(value, { stream: true });

                    let boundary = buffer.indexOf('\n\n');
                    while (boundary !== -1) {
                        const chunk = buffer.slice(0, boundary).trim();
                        buffer = buffer.slice(boundary + 2);
                        boundary = buffer.indexOf('\n\n');

                        if (!chunk) continue;
                        const lines = chunk.split('\n');
                        for (const line of lines) {
                            if (!line.startsWith('data:')) continue;
                            const jsonText = line.replace(/^data:\s*/, '');
                            if (!jsonText) continue;
                            const payload = JSON.parse(jsonText);
                            if (payload?.type === 'frame' || payload?.type === 'complete') {
                                setLiveLogs((prevLogs) => [
                                    ...prevLogs,
                                    ...(payload.logs || []),
                                ]);
                                setResult((prevResult) => ({
                                    ...prevResult,
                                    ...payload,
                                    logs: [
                                        ...(prevResult?.logs || []),
                                        ...(payload.logs || []),
                                    ],
                                }));
                                if (payload?.type === 'complete') {
                                    completed = true;
                                }
                            }
                        }
                    }
                }

                if (!completed) {
                    setError('Stream ended before completion.');
                }
            } catch (err) {
                if (err.name !== 'AbortError') {
                    console.error('ALPR stream failed', err);
                    setError(err?.message || 'Inference failed.');
                }
            } finally {
                setBusy(false);
                setStreaming(false);
            }
        } else {
            try {
                const payload = await licensePlateAPI.recognize({
                    file: inputType === 'file' ? selectedFile : null,
                    mediaType,
                    sourceUrl: inputType === 'link' ? sourceUrl.trim() : '',
                    detectorModel,
                    ocrModel,
                    detectionThreshold,
                    ocrThreshold,
                });
                setResult(payload.data);
            } catch (err) {
                console.error('ALPR request failed', err);
                setError(
                    err?.response?.data?.detail ||
                    err?.response?.statusText ||
                    err?.message ||
                    'Inference failed.'
                );
            } finally {
                setBusy(false);
            }
        }
    };

    const inputLabel = mediaType === 'image' ? 'Upload Image or Paste Image Link' : 'Upload Video or Paste Video Link';
    const sourceHint = mediaType === 'image'
        ? 'Supported image files or direct image URLs.'
        : 'Supported video files, RTSP/HTTP URLs, or hosted video links.';

    return (
        <div className="page-scroll">
            <div className="page-header">
                <div>
                    <h1 className="page-title">License Plate Recognition</h1>
                    <p className="page-subtitle">Run ALPR on uploaded images or remote video feeds.</p>
                    <p className="page-note">For a live demo, use an image upload or a video URL / RTSP stream.</p>
                </div>
            </div>

            <ContentCard title="Input & Model Selection" accent="cyan">
                <div style={{ display: 'grid', gap: 20 }}>
                    <div>
                        <label className="form-label">Content Type</label>
                        <SegmentedControl
                            options={[
                                { label: 'Image', value: 'image' },
                                { label: 'Video', value: 'video' },
                            ]}
                            value={mediaType}
                            onChange={(value) => {
                                setMediaType(value);
                                setSelectedFile(null);
                                setSourceUrl('');
                                setResult(null);
                                setError('');
                            }}
                        />
                    </div>

                    <div>
                        <label className="form-label">Input Source</label>
                        <SegmentedControl
                            options={['file', 'link']}
                            value={inputType}
                            onChange={(value) => {
                                setInputType(value);
                                setSelectedFile(null);
                                setSourceUrl('');
                                setResult(null);
                                setError('');
                            }}
                        />
                    </div>

                    <div className="form-field">
                        <label className="form-label">Detector Model</label>
                        <select
                            className="form-input"
                            value={detectorModel}
                            onChange={(e) => setDetectorModel(e.target.value)}
                        >
                            {DETECTOR_MODELS.map((model) => (
                                <option key={model} value={model}>
                                    {MODEL_LABELS[model] || model}
                                </option>
                            ))}
                        </select>
                    </div>

                    <div className="form-field">
                        <label className="form-label">OCR Model</label>
                        <select
                            className="form-input"
                            value={ocrModel}
                            onChange={(e) => setOcrModel(e.target.value)}
                        >
                            {OCR_MODELS.map((model) => (
                                <option key={model} value={model}>
                                    {MODEL_LABELS[model] || model}
                                </option>
                            ))}
                        </select>
                    </div>

                    <div className="form-field">
                        <label className="form-label">{inputLabel}</label>
                        {inputType === 'file' ? (
                            <label className="btn btn-secondary" style={{ alignItems: 'center', display: 'inline-flex' }}>
                                <UploadCloud size={14} />
                                <span style={{ marginLeft: 8 }}>Choose {mediaType === 'image' ? 'Image' : 'Video'}</span>
                                <input
                                    type="file"
                                    accept={mediaType === 'image' ? 'image/*' : 'video/*'}
                                    hidden
                                    onChange={(e) => handleFileChange(e.target.files)}
                                />
                            </label>
                        ) : (
                            <div className="form-input-group">
                                <input
                                    type="text"
                                    value={sourceUrl}
                                    onChange={(e) => setSourceUrl(e.target.value)}
                                    placeholder="https://example.com/your-media.mp4"
                                    className="form-input"
                                />
                                <span className="form-input-hint">
                                    <Link2 size={14} style={{ verticalAlign: 'middle', marginRight: 6 }} />
                                    Paste a direct URL or RTSP link
                                </span>
                            </div>
                        )}
                        <p className="form-help-text">{sourceHint}</p>
                    </div>

                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                        <div className="form-field">
                            <label className="form-label">Detection Threshold</label>
                            <input
                                type="range"
                                min={0.0}
                                max={1.0}
                                step={0.01}
                                value={detectionThreshold}
                                onChange={(e) => setDetectionThreshold(Number(e.target.value))}
                            />
                            <div className="form-static-value">{(detectionThreshold * 100).toFixed(0)}%</div>
                        </div>
                        <div className="form-field">
                            <label className="form-label">OCR Threshold</label>
                            <input
                                type="range"
                                min={0.0}
                                max={1.0}
                                step={0.01}
                                value={ocrThreshold}
                                onChange={(e) => setOcrThreshold(Number(e.target.value))}
                            />
                            <div className="form-static-value">{(ocrThreshold * 100).toFixed(0)}%</div>
                        </div>
                    </div>

                    <div style={{ marginTop: 16, display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                        <button className="btn btn-primary" type="button" onClick={submitRecognition} disabled={busy || !hasValidSource()}>
                            {busy ? 'Running...' : 'Run ALPR'}
                        </button>
                        <button className="btn btn-ghost" type="button" onClick={resetForm}>
                            Reset
                        </button>
                    </div>

                    {error ? (
                        <div className="page-empty-hint page-empty-hint--error" style={{ marginTop: 12 }}>
                            <AlertTriangle size={16} style={{ marginRight: 8, verticalAlign: 'middle' }} />
                            {error}
                        </div>
                    ) : null}
                </div>
            </ContentCard>

            <ContentCard title="Recognition Results" accent="teal">
                <div style={{ marginBottom: 20, display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                    <button
                        type="button"
                        className={`btn ${activeResultTab === 'live' ? 'btn-primary' : 'btn-ghost'}`}
                        onClick={() => setActiveResultTab('live')}
                    >
                        Live Streaming
                    </button>
                    <button
                        type="button"
                        className={`btn ${activeResultTab === 'snapshots' ? 'btn-primary' : 'btn-ghost'}`}
                        onClick={() => setActiveResultTab('snapshots')}
                    >
                        Snapshots
                    </button>
                </div>

                {busy && streaming && (
                    <div className="page-empty-hint">Streaming inference in progress...</div>
                )}

                {!result && !busy && (
                    <div className="page-empty-hint">
                        Run inference to see license plate results here. For video sources, the annotated result appears after processing completes.
                    </div>
                )}

                {result && activeResultTab === 'live' && (
                    <div>
                        <div className="form-stat-row" style={{ marginBottom: 16 }}>
                            <div className="form-static-value">Source: {result.source_type === 'image' ? 'Image' : 'Video'}</div>
                            <div className="form-static-value">Plates detected: {platesDetected}</div>
                            {result.source_type === 'video' ? (
                                <div className="form-static-value">Frames processed: {framesProcessed}</div>
                            ) : null}
                        </div>
                        <div className="form-stat-row" style={{ marginBottom: 16 }}>
                            <div className="form-static-value">Detector: {MODEL_LABELS[result.detector_model] || result.detector_model}</div>
                            <div className="form-static-value">OCR: {MODEL_LABELS[result.ocr_model] || result.ocr_model}</div>
                        </div>
                        {result.annotated_image_base64 ? (
                            <div className="page-preview-card" style={{ padding: 16, marginBottom: 16 }}>
                                <img
                                    src={`data:image/jpeg;base64,${result.annotated_image_base64}`}
                                    alt="Annotated result"
                                    style={{ width: '100%', maxHeight: 420, objectFit: 'contain', borderRadius: 8 }}
                                />
                            </div>
                        ) : (
                            <div className="page-empty-hint" style={{ marginBottom: 16 }}>
                                Annotated preview is not available for this result.
                            </div>
                        )}
                        <div className="page-preview-card" style={{ padding: 16, maxHeight: 360, overflowY: 'auto' }}>
                            {logs.length ? logs.map((line, index) => (
                                <div key={index} style={{ marginBottom: 8 }}>{line}</div>
                            )) : (
                                <div>No logs available.</div>
                            )}
                        </div>
                    </div>
                )}

                {!busy && result && activeResultTab === 'snapshots' && (
                    <div>
                        {snapshots.length === 0 ? (
                            <div className="page-empty-hint">No license plate snapshots were detected.</div>
                        ) : (
                            <div className="page-card-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 16 }}>
                                {snapshots.map((snapshot, index) => (
                                    <div key={index} className="page-preview-card" style={{ padding: 12 }}>
                                        <img
                                            src={`data:image/jpeg;base64,${snapshot.image_base64}`}
                                            alt={snapshot.text}
                                            className="preview-image"
                                        />
                                        <div style={{ marginTop: 10 }}>
                                            <strong>{snapshot.text || 'Unknown plate'}</strong>
                                            <div>Det: {(snapshot.confidence * 100).toFixed(1)}%</div>
                                            <div>OCR: {snapshot.ocr_confidence != null ? `${(snapshot.ocr_confidence * 100).toFixed(1)}%` : 'n/a'}</div>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                )}
            </ContentCard>
        </div>
    );
}
