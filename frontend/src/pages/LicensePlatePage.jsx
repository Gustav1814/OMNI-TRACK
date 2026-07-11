/**
 * OmniTrack AI — License Plate Recognition Demo
 * Upload a vehicle image and run ALPR inference using the backend.
 */

import React, { useEffect, useMemo, useState } from 'react';
import { Barcode, UploadCloud, ImagePlus, CheckCircle, AlertTriangle } from 'lucide-react';
import ContentCard from '../components/ui/ContentCard';
import StatCard from '../components/ui/StatCard';
import RecordCard from '../components/ui/RecordCard';
import { licensePlateAPI } from '../services/api';

const DEFAULT_DETECTOR_MODEL = 'license_plate_detection.pt';
const DEFAULT_OCR_MODEL = 'cct-s-v2-global-model';

export default function LicensePlatePage() {
    const detectorModel = DEFAULT_DETECTOR_MODEL;
    const ocrModel = DEFAULT_OCR_MODEL;
    const [selectedFile, setSelectedFile] = useState(null);
    const [previewUrl, setPreviewUrl] = useState('');
    const [result, setResult] = useState(null);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);

    useEffect(() => {
        return () => {
            if (previewUrl) {
                URL.revokeObjectURL(previewUrl);
            }
        };
    }, [previewUrl]);

    const predictions = Array.isArray(result?.predictions) ? result.predictions : [];
    const avgConfidence = useMemo(() => {
        if (!predictions.length) return 0;
        return predictions.reduce((sum, item) => sum + (item.confidence || 0), 0) / predictions.length;
    }, [predictions]);

    const handleFileChange = (files) => {
        if (!files?.length) return;
        const file = files[0];
        setSelectedFile(file);
        setError('');
        setResult(null);
        if (previewUrl) {
            URL.revokeObjectURL(previewUrl);
        }
        setPreviewUrl(URL.createObjectURL(file));
    };

    const submitRecognition = async () => {
        if (!selectedFile) {
            setError('Please upload an image first.');
            return;
        }
        setError('');
        setBusy(true);
        setResult(null);

        try {
            const response = await licensePlateAPI.recognize(selectedFile, detectorModel, ocrModel);
            setResult(response.data);
        } catch (err) {
            console.error('ALPR request failed', err);
            setError(
                err.response?.data?.detail ||
                err.response?.statusText ||
                err.message ||
                'Inference failed.'
            );
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="page-scroll">
            <div className="page-header">
                <div>
                    <h1 className="page-title">License Plate Recognition</h1>
                    <p className="page-subtitle">Upload an image and run ALPR inference through the backend.</p>
                    <p className="page-note">Note: use the access token for API authorization, not the refresh token.</p>
                </div>
            </div>

            <div className="stats-grid">
                <StatCard icon={Barcode} label="Plates Detected" value={predictions.length} accent="cyan" />
                <StatCard icon={CheckCircle} label="Average Confidence" value={avgConfidence.toFixed(2)} suffix="%" accent="emerald" />
                <StatCard icon={ImagePlus} label="Selected File" value={selectedFile?.name || 'None'} accent="teal" />
                <StatCard icon={UploadCloud} label="Model Pair" value={`${detectorModel} / ${ocrModel}`} accent="violet" />
            </div>

            <div className="two-col">
                <ContentCard title="Input & Model Selection" accent="cyan">
                    <div className="form-field">
                        <label className="form-label">Detector Model</label>
                        <div className="form-static-value">{detectorModel}</div>
                    </div>
                    <div className="form-field">
                        <label className="form-label">OCR Model</label>
                        <div className="form-static-value">{ocrModel}</div>
                    </div>

                    <div className="form-field">
                        <label className="form-label">Upload Image</label>
                        <label className="btn btn-secondary" style={{ alignItems: 'center', display: 'inline-flex' }}>
                            <UploadCloud size={14} />
                            <span style={{ marginLeft: 8 }}>Choose Image</span>
                            <input
                                type="file"
                                accept="image/*"
                                hidden
                                onChange={(e) => handleFileChange(e.target.files)}
                            />
                        </label>
                    </div>

                    {previewUrl ? (
                        <div className="page-preview-card">
                            <img src={previewUrl} alt="Preview" className="preview-image" />
                        </div>
                    ) : (
                        <div className="page-empty-hint">Select an image to preview before running inference.</div>
                    )}

                    <div style={{ marginTop: 16, display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                        <button className="btn btn-primary" type="button" onClick={submitRecognition} disabled={busy || !selectedFile}>
                            {busy ? 'Running...' : 'Run ALPR'}
                        </button>
                        <button className="btn btn-ghost" type="button" onClick={() => {
                            setSelectedFile(null);
                            setResult(null);
                            setError('');
                            if (previewUrl) {
                                URL.revokeObjectURL(previewUrl);
                                setPreviewUrl('');
                            }
                        }}>
                            Reset
                        </button>
                    </div>
                    {error ? (
                        <div className="page-empty-hint page-empty-hint--error" style={{ marginTop: 12 }}>
                            <AlertTriangle size={16} style={{ marginRight: 8, verticalAlign: 'middle' }} />
                            {error}
                        </div>
                    ) : null}
                </ContentCard>

                <ContentCard title="Recognition Results" accent="teal">
                    {!result ? (
                        <div className="page-empty-hint">No results yet. Upload an image and run inference.</div>
                    ) : (
                        <>
                            <div className="ui-feed-list" style={{ marginBottom: 16 }}>
                                {predictions.length === 0 ? (
                                    <RecordCard
                                        icon={AlertTriangle}
                                        accent="rose"
                                        title="No license plates detected"
                                        meta="Try a different image or improve lighting."
                                    />
                                ) : predictions.map((prediction, index) => (
                                    <RecordCard
                                        key={index}
                                        icon={CheckCircle}
                                        accent="emerald"
                                        title={prediction.text || 'Plate detected'}
                                        meta={
                                            <>
                                                <span>Confidence: {(prediction.confidence * 100).toFixed(1)}%</span>
                                                <span style={{ marginLeft: 10 }}>
                                                    OCR Confidence: {prediction.ocr_confidence != null ? `${(prediction.ocr_confidence * 100).toFixed(1)}%` : 'n/a'}
                                                </span>
                                            </>
                                        }
                                    />
                                ))}
                            </div>

                            {result.annotated_image_base64 ? (
                                <div className="page-preview-card">
                                    <img
                                        src={`data:image/jpeg;base64,${result.annotated_image_base64}`}
                                        alt="Annotated license plate"
                                        className="preview-image"
                                    />
                                </div>
                            ) : null}
                        </>
                    )}
                </ContentCard>
            </div>
        </div>
    );
}
