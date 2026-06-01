/**
 * OmniTrack AI — Demographics (live)
 * Polls /api/demographics/current.
 */

import React, { useState } from 'react';
import { BarChart3, Users } from 'lucide-react';
import {
    BarChart, Bar, ResponsiveContainer, XAxis, YAxis, Tooltip, CartesianGrid,
    PieChart, Pie, Cell, Legend,
} from 'recharts';
import { demographicsAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import StatCard from '../components/ui/StatCard';
import ContentCard from '../components/ui/ContentCard';
import useGradientColors from '../hooks/useGradientColors';

export default function DemographicsPage() {
    const { a, b } = useGradientColors();
    const GENDER_COLOR = { male: a, female: b, unknown: '#64748b', other: b };

    const AGE_ORDER = ['<18', '18-25', '26-35', '36-45', '46-55', '56+'];
    const [zone, setZone] = useState('');
    const { data } = useLivePoll(
        () => demographicsAPI.current(zone || undefined),
        { intervalMs: 15000 }
    );

    const ageDist = data?.age_distribution || {};
    const genderDist = data?.gender_distribution || {};

    const ageChart = AGE_ORDER
        .map((k) => ({ name: k, count: Number(ageDist[k]) || 0 }))
        .filter((r) => r.count > 0)
        .concat(
            Object.entries(ageDist)
                .filter(([k]) => !AGE_ORDER.includes(k))
                .map(([k, v]) => ({ name: k, count: Number(v) || 0 }))
        );

    const genderChart = Object.entries(genderDist).map(([k, v]) => ({
        name: k, value: Number(v) || 0,
    }));

    const total = Number(data?.total_count) || 0;

    return (
        <div className="page-scroll">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Demographics</h1>
                    <p className="page-subtitle">Age + gender distribution (last 24h)</p>
                </div>
                <input
                    className="form-input"
                    placeholder="Filter by zone…"
                    style={{ maxWidth: 240 }}
                    value={zone}
                    onChange={(e) => setZone(e.target.value)}
                />
            </div>

            <div className="stats-grid">
                <StatCard icon={Users} label="Total Observed" value={total.toLocaleString()} accent="teal" />
                <StatCard icon={BarChart3} label="Zone Filter" value={zone || 'All'} accent="cyan" />
                <StatCard icon={BarChart3} label="Age Buckets" value={Object.keys(ageDist).length} accent="coral" />
                <StatCard icon={Users} label="Genders Tracked" value={Object.keys(genderDist).length} accent="emerald" />
            </div>

            <div className="two-col">
                <ContentCard title="Age Distribution" accent="sky">
                    {ageChart.length === 0 ? (
                        <Empty />
                    ) : (
                        <div style={{ height: 300 }}>
                            <ResponsiveContainer>
                                <BarChart data={ageChart}>
                                    <CartesianGrid stroke="rgba(255,255,255,0.05)" />
                                    <XAxis dataKey="name" stroke="#71717a" fontSize={11} />
                                    <YAxis stroke="#71717a" fontSize={11} />
                                    <Tooltip contentStyle={{ background: '#111', border: '1px solid #222' }} />
                                    <Bar dataKey="count" fill={a} radius={[6, 6, 0, 0]} />
                                </BarChart>
                            </ResponsiveContainer>
                        </div>
                    )}
                </ContentCard>

                <ContentCard title="Gender Split" accent="rose">
                    {genderChart.length === 0 ? (
                        <Empty />
                    ) : (
                        <div style={{ height: 300 }}>
                            <ResponsiveContainer>
                                <PieChart>
                                    <Pie data={genderChart} dataKey="value" nameKey="name"
                                        cx="50%" cy="50%" outerRadius={90} innerRadius={50}
                                        paddingAngle={2}>
                                        {genderChart.map((d) => (
                                            <Cell key={d.name} fill={GENDER_COLOR[d.name] || '#64748b'} />
                                        ))}
                                    </Pie>
                                    <Tooltip contentStyle={{ background: '#111', border: '1px solid #222' }} />
                                    <Legend />
                                </PieChart>
                            </ResponsiveContainer>
                        </div>
                    )}
                </ContentCard>
            </div>
        </div>
    );
}

function Empty() {
    return (
        <div className="page-empty-hint">
            No demographic samples yet — make sure the emotion/demographics module is reaching faces.
        </div>
    );
}
