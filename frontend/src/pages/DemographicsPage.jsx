/**
 * OmniTrack AI — Demographics
 *
 * Estimated age band / gender mix of visitors, aggregated per visit on the
 * server (GET /api/demographics/current). The page only renders what the
 * server reports: when no model is installed, nothing was recorded, or the
 * sample is below the server's minimum, it says so instead of showing numbers.
 */

import React, { useEffect, useMemo, useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import {
    Bar, BarChart, CartesianGrid, Cell, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { demographicsAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import Metric from '../components/ui/Metric';
import { useTokens, withAlpha } from '../lib/tokens';
import { rechartsTheme } from '../lib/chartTheme';
import { AnalyticsPage, DASH, FeatureCard, HeroRow, NoData, Panel, Seg } from '../components/zones/analyticsUi';

const AGE_ORDER = ['0-2', '3-9', '10-19', '20-29', '30-39', '40-49', '50-59', '60-69', '70+', '0-12', '<18', '13-17', '18-24', '18-25', '25-34', '26-35', '35-44', '36-45', '45-54', '46-55', '55-64', '55+', '56+', '65+'];
// Fixed slot per gender key: colour follows the category, never the ranking.
const GENDER_ORDER = ['male', 'female', 'other', 'unknown'];
const GENDER_SLOT = { male: 1, female: 2, other: 3 }; // unknown -> neutral
const WINDOWS = [
    { value: 24, label: '24 h' },
    { value: 168, label: '7 days' },
    { value: 720, label: '30 days' },
];
const DEFAULT_CAVEAT = 'Age and gender are estimates from a face model and are less accurate for some ages, skin tones and '
    + 'presentations. Gender is inferred from appearance, not identity. Use these figures for aggregate trends only, '
    + 'never for decisions about individuals.';

const num = (v) => (v == null || !Number.isFinite(Number(v)) ? null : Number(v));
const pct = (n, total) => (total > 0 ? `${Math.round((n / total) * 100)} %` : DASH);
const titleCase = (s) => String(s).replace(/[_-]/g, ' ').replace(/\b\w/g, (m) => m.toUpperCase());

/**
 * Normalise the contract shape {status, sample_count, min_sample, age_bands,
 * gender, unattributed, model:{name, installed}} and the older
 * {status, age_distribution, gender_distribution, total_count, reason} shape.
 */
function normalise(data) {
    if (!data) return null;
    const objOrEmpty = (v) => (v && typeof v === 'object' && !Array.isArray(v) ? v : {});
    const age = objOrEmpty(data.age_bands ?? data.age_distribution);
    const gender = objOrEmpty(typeof data.gender === 'object' ? data.gender : data.gender_distribution);
    const model = data.model && typeof data.model === 'object' ? data.model : null;
    return {
        status: data.status || 'ok',
        sampleCount: num(data.sample_count ?? data.total_count) ?? 0,
        minSample: num(data.min_sample),
        unattributed: num(data.unattributed),
        age,
        gender,
        model,
        reason: data.reason || null,
        caveat: typeof data.bias_caveat === 'string' && data.bias_caveat.trim() ? data.bias_caveat.trim() : null,
        windowHours: num(data.window_hours),
        legacy: data.sample_count === undefined && data.total_count !== undefined,
    };
}

function sortAge(entries) {
    const rank = (k) => {
        const i = AGE_ORDER.indexOf(k);
        if (i >= 0) return i;
        const lead = parseInt(String(k).replace(/[^0-9]/g, ''), 10);
        return Number.isFinite(lead) ? 100 + lead : 1000;
    };
    return entries.sort(([a], [b]) => rank(a) - rank(b));
}

function genderRank(k) {
    const i = GENDER_ORDER.indexOf(k);
    return i >= 0 ? i : GENDER_ORDER.length;
}

/** Progress toward the server's minimum sample. Shown whenever the breakdown is withheld. */
function SampleProgress({ count, min }) {
    if (min == null || min <= 0) return null;
    const share = Math.min(100, (count / min) * 100);
    return (
        <div className="meter meter--accent">
            <div className="meter-head">
                <span>Visits sampled before the breakdown is shown</span>
                <strong>{count.toLocaleString()} of {min.toLocaleString()}</strong>
            </div>
            <div className="meter-track" role="progressbar" aria-valuemin={0} aria-valuemax={min} aria-valuenow={count} aria-label="Sample progress">
                <div className="meter-fill" style={{ '--value': `${share}%` }} />
            </div>
        </div>
    );
}

export default function DemographicsPage({ embedded = false }) {
    const [zone, setZone] = useState('');
    const [debounced, setDebounced] = useState('');
    const [hours, setHours] = useState(24);
    useEffect(() => {
        const timer = setTimeout(() => setDebounced(zone.trim()), 400);
        return () => clearTimeout(timer);
    }, [zone]);
    const { data: raw, error, loading, refresh } = useLivePoll(
        () => demographicsAPI.current(debounced || undefined, hours),
        { intervalMs: 30000 },
    );
    useEffect(() => { refresh(); }, [debounced, hours, refresh]);
    const t = useTokens();
    const th = rechartsTheme(t);

    const d = useMemo(() => normalise(raw), [raw]);

    const modelMissing = d && (d.status === 'model_not_installed' || (d.model && d.model.installed === false));
    const insufficient = d && !modelMissing && d.minSample != null && d.sampleCount > 0 && d.sampleCount < d.minSample;
    const noData = d && !modelMissing && !insufficient && (d.status === 'no_data' || d.sampleCount === 0);
    const showBreakdown = d && !modelMissing && !insufficient && !noData;

    const ageRows = useMemo(() => (d ? sortAge(Object.entries(d.age).filter(([, v]) => num(v) != null)) : [])
        .map(([name, v]) => ({ name, count: Number(v) })), [d]);
    const genderRows = useMemo(() => (d ? Object.entries(d.gender).filter(([, v]) => num(v) != null) : [])
        .map(([name, v]) => ({ name: name.toLowerCase(), count: Number(v) }))
        .sort((a, b) => genderRank(a.name) - genderRank(b.name)), [d]);
    const ageTotal = ageRows.reduce((s, r) => s + r.count, 0);
    const genderTotal = genderRows.reduce((s, r) => s + r.count, 0);
    const topAge = ageRows.slice().sort((a, b) => b.count - a.count)[0];
    const ageData = ageRows.map((r) => ({ ...r, share: ageTotal ? r.count / ageTotal : 0 }));

    const windowLabel = WINDOWS.find((w) => w.value === hours)?.label || `${hours} h`;
    const modelLabel = d?.model
        ? (d.model.installed === false ? 'Not installed' : 'Installed')
        : (modelMissing ? 'Not installed' : d?.legacy ? 'Not reported' : null);
    const modelSub = d?.model?.name || undefined;
    const slotClass = (name) => (GENDER_SLOT[name] ? `an-seg-${GENDER_SLOT[name]}` : '');
    const swatchClass = (name) => (GENDER_SLOT[name] ? `swatch-${GENDER_SLOT[name]}` : 'swatch-neutral');

    return (
        <AnalyticsPage
            embedded={embedded}
            title="Audience"
            subtitle={`Estimated age and gender mix of visitors, counted once per visit, over the last ${windowLabel}.`}
            controls={(
                <>
                <input
                    className="form-input an-select"
                    placeholder="Filter by zone"
                    value={zone}
                    onChange={(e) => setZone(e.target.value)}
                    aria-label="Filter by zone"
                />
                <Seg value={hours} onChange={setHours} options={WINDOWS} label="Time window" />
                </>
            )}
        >

            <div className="an-caveat" role="note">
                <AlertTriangle size={16} strokeWidth={1.75} aria-hidden />
                <div>
                    <div className="an-caveat-title">
                        Estimates, not facts.
                        {d?.minSample != null ? ` Shown only after ${d.minSample} sampled visits.` : ''}
                    </div>
                    <p>{d?.caveat || DEFAULT_CAVEAT}</p>
                </div>
            </div>

            {error && <div className="alert-banner danger">Unable to reach the demographics service.</div>}

            <HeroRow
                hero={{
                    label: `Visits sampled, ${windowLabel}`,
                    value: d ? d.sampleCount : null,
                    foot: d?.minSample != null
                        ? (d.sampleCount >= d.minSample ? 'Minimum sample reached' : `${Math.max(0, d.minSample - d.sampleCount)} more needed before any breakdown is shown`)
                        : 'Each visit is counted once, from its clearest face',
                }}
            >
                <Metric label="Largest age band" value={showBreakdown && topAge ? topAge.name : null} size="sm" />
                <Metric label="Model" value={modelLabel} size="sm" sub={modelSub} />
            </HeroRow>

            <div className="an-grid an-grid--feature">
                <FeatureCard
                    label="Visits without a usable face"
                    value={d?.unattributed ?? null}
                    sub={d?.unattributed != null ? `Left out of every percentage, last ${windowLabel}` : 'Visits where no face was clear enough to estimate'}
                />
                {!d && loading && !error ? (
                    <Panel title="Breakdown"><NoData title="Loading demographics" /></Panel>
                ) : null}
                {!d && error ? (
                    <Panel title="Breakdown"><NoData title="Demographics unavailable" /></Panel>
                ) : null}
                {modelMissing ? (
                    <Panel title="Breakdown">
                        <NoData title="Age and gender estimation is off">
                            {d.reason ? `${d.reason} ` : ''}Install the ONNX age and gender model on the server to start recording estimates. Nothing is shown until real samples exist.
                        </NoData>
                    </Panel>
                ) : null}
                {noData || insufficient ? (
                    <Panel title="Breakdown" sub={debounced ? `Zone filter: ${debounced}` : undefined}>
                        <NoData title={insufficient ? `${d.sampleCount} of ${d.minSample} visits needed` : 'No visits recorded in this window'}>
                            {insufficient
                                ? 'The breakdown is withheld until the minimum sample is reached, because percentages from a handful of visits mislead.'
                                : (d.reason || `${d.model?.name ? `${d.model.name} is installed, but no` : 'No'} visits with a usable face were recorded in the last ${windowLabel}${debounced ? ` for zone "${debounced}"` : ''}. Start a camera with demographics enabled.`)}
                        </NoData>
                        <SampleProgress count={d.sampleCount} min={d.minSample} />
                    </Panel>
                ) : null}
                {showBreakdown ? (
                    <Panel title="Age bands" sub={`${ageTotal.toLocaleString()} visits with an age estimate. Largest band highlighted.`}>
                        {ageData.length === 0 ? (
                            <NoData title="No age estimates in this window" />
                        ) : (
                            <div className="an-chart">
                                <ResponsiveContainer>
                                    <BarChart data={ageData} margin={{ top: 24, right: 0, left: -8, bottom: 0 }}>
                                        <CartesianGrid {...th.grid} />
                                        <XAxis dataKey="name" {...th.xAxis} interval={0} />
                                        <YAxis {...th.yAxis} allowDecimals={false} />
                                        <Tooltip {...th.tooltip} cursor={{ fill: withAlpha(t.text, 0.04) }} formatter={(v) => [`${v.toLocaleString()} (${pct(v, ageTotal)})`, 'Visits']} />
                                        <Bar dataKey="count" name="Visits" radius={[4, 4, 0, 0]} maxBarSize={36}>
                                            {ageData.map((r) => <Cell key={r.name} fill={topAge && r.name === topAge.name ? t.highlight : t.neutral} />)}
                                            <LabelList dataKey="count" position="top" formatter={(v) => pct(v, ageTotal)} {...th.xAxis.tick} />
                                        </Bar>
                                    </BarChart>
                                </ResponsiveContainer>
                            </div>
                        )}
                    </Panel>
                ) : null}
            </div>

            {showBreakdown ? (
                <Panel title="Gender, estimated" sub={`${genderTotal.toLocaleString()} visits with a gender estimate`}>
                    {genderRows.length === 0 ? (
                        <NoData title="No gender estimates in this window" />
                    ) : (
                        <div className="an-meter-list">
                            <div className="an-stack an-stack--tall" role="img" aria-label={genderRows.map((g) => `${titleCase(g.name)} ${pct(g.count, genderTotal)}`).join(', ')}>
                                {genderRows.map((g) => (
                                    <span key={g.name} className={slotClass(g.name)} title={`${titleCase(g.name)} ${pct(g.count, genderTotal)}`} style={{ '--w': g.count }} />
                                ))}
                            </div>
                            <div className="an-rows">
                                {genderRows.map((g) => (
                                    <div key={g.name} className="an-row">
                                        <span className="an-meter-label an-row-name"><span className={`swatch ${swatchClass(g.name)}`} />{titleCase(g.name)}</span>
                                        <span className="an-row-val an-row-val--muted">{g.count.toLocaleString()} visits</span>
                                        <span className="an-row-val">{pct(g.count, genderTotal)}</span>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                </Panel>
            ) : null}

            {showBreakdown && (d.minSample != null || d.legacy) ? (
                <p className="an-note">
                    {d.minSample != null ? `Breakdowns appear once at least ${d.minSample} visits are sampled. Visits without a usable face count as not attributable and are left out of the percentages.` : ''}
                    {d.legacy ? ' This server reports an older demographics format without per-visit sample rules.' : ''}
                </p>
            ) : null}
        </AnalyticsPage>
    );
}
