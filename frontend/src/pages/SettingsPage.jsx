/**
 * OmniTrack — Appearance & gradient settings
 */

import React from 'react';
import { Check, Moon, Sun, Palette } from 'lucide-react';
import { useTheme } from '../contexts/ThemeContext';
import { GRADIENT_PRESETS } from '../lib/gradientPresets';
import MagicCard from '../components/ui/MagicCard';
import MagicPageHeader from '../components/ui/MagicPageHeader';
import ContentCard from '../components/ui/ContentCard';

export default function SettingsPage() {
    const { theme, setTheme, gradientPreset, setGradientPreset } = useTheme();
    const active = GRADIENT_PRESETS.find((p) => p.id === gradientPreset) || GRADIENT_PRESETS[0];

    return (
        <div className="page-scroll">
            <MagicPageHeader>
                <h1 className="page-title">Settings</h1>
                <p className="page-subtitle">
                    Appearance and interface accents for executive reporting. Dashboard KPI colors remain fixed.
                </p>
            </MagicPageHeader>

            <ContentCard title="Theme" subtitle="Light workspace or high-contrast dark mode">
                <div className="settings-theme-row">
                    <button
                        type="button"
                        className={`settings-theme-btn${theme === 'light' ? ' is-active' : ''}`}
                        onClick={() => setTheme('light')}
                    >
                        <Sun size={16} />
                        Light
                    </button>
                    <button
                        type="button"
                        className={`settings-theme-btn${theme === 'dark' ? ' is-active' : ''}`}
                        onClick={() => setTheme('dark')}
                    >
                        <Moon size={16} />
                        Dark
                    </button>
                </div>
            </ContentCard>

            <ContentCard
                title="Accent scheme"
                subtitle="Choose a restrained palette for the canvas and UI chrome"
            >
                <div className="scheme-row">
                    {GRADIENT_PRESETS.map((preset) => {
                        const selected = preset.id === gradientPreset;
                        return (
                            <button
                                key={preset.id}
                                type="button"
                                className={`scheme-dot${selected ? ' is-active' : ''}`}
                                onClick={() => setGradientPreset(preset.id)}
                                aria-pressed={selected}
                                title={preset.name}
                            >
                                <span
                                    className="scheme-dot-fill"
                                    style={{ background: `linear-gradient(135deg, ${preset.a} 0%, ${preset.b} 100%)` }}
                                />
                                {selected ? (
                                    <span className="scheme-dot-check" aria-hidden>
                                        <Check size={14} strokeWidth={3} />
                                    </span>
                                ) : null}
                            </button>
                        );
                    })}
                </div>
                <div className="scheme-caption">
                    <span className="scheme-caption-name">{active.name}</span>
                    <span className="scheme-caption-desc">{active.description}</span>
                </div>
            </ContentCard>

            <ContentCard
                title="Preview"
                subtitle={`Active scheme: ${active.name}`}
                actions={<Palette size={16} style={{ color: 'var(--gradient-a)' }} />}
            >
                <div className="settings-magic-preview">
                    <MagicCard borderRadius={14}>
                        <div className="settings-magic-preview-header">
                            <h4 className="ui-content-card-title" style={{ margin: 0 }}>Interface sample</h4>
                            <p className="ui-content-card-sub" style={{ margin: '4px 0 0' }}>
                                Subtle accent on cards and navigation
                            </p>
                        </div>
                        <div className="settings-magic-preview-body">
                            <p style={{ fontSize: 13, color: 'var(--exec-muted)', margin: 0, lineHeight: 1.5 }}>
                                The selected scheme applies a light canvas wash and tones the sidebar, controls, and panel chrome.
                                Dashboard KPI metrics retain their standard operational colors.
                            </p>
                        </div>
                    </MagicCard>
                </div>
            </ContentCard>
        </div>
    );
}
