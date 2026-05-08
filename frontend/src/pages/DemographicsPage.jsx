/**
 * OmniTrack AI — Demographics (live)
 * Polls /api/demographics/current.
 */

import React, { useState } from 'react';
import { motion } from 'framer-motion';
import { BarChart3, Users } from 'lucide-react';
import {
    BarChart, Bar, ResponsiveContainer, XAxis, YAxis, Tooltip, CartesianGrid,
    PieChart, Pie, Cell, Legend,
} from 'recharts';
import { demographicsAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';

const AGE_ORDER = ['<18', '18-25', '26-35', '36-45', '46-55', '56+'];
const GENDER_COLOR = { male: '#3b82f6', female: '#f472b6', unknown: '#64748b', other: '#a855f7' };

export default function DemographicsPage() {
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
                <Stat icon={Users} label="Total Observed" value={total.toLocaleString()} accent="indigo" />
                <Stat icon={BarChart3} label="Zone Filter" value={zone || 'All'} accent="cyan" />
                <Stat icon={BarChart3} label="Age Buckets" value={Object.keys(ageDist).length} accent="amber" />
                <Stat icon={Users} label="Genders Tracked" value={Object.keys(genderDist).length} accent="emerald" />
            </div>

            <div className="two-col">
                <div className="card">
                    <div className="card-header">
                        <h3 className="card-title">Age Distribution</h3>
                    </div>
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
                                    <Bar dataKey="count" fill="#818cf8" radius={[6, 6, 0, 0]} />
                                </BarChart>
                            </ResponsiveContainer>
                        </div>
                    )}
                </div>

                <div className="card">
                    <div className="card-header">
                        <h3 className="card-title">Gender Split</h3>
                    </div>
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
                                            <Cell key={d.name} fill={GENDER_COLOR[d.name] || '#a5b4fc'} />
                                        ))}
                                    </Pie>
                                    <Tooltip contentStyle={{ background: '#111', border: '1px solid #222' }} />
                                    <Legend />
                                </PieChart>
                            </ResponsiveContainer>
                        </div>
                    )}
                </div>
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

function Stat({ icon: Icon, label, value, accent = 'indigo' }) {
    return (
        <motion.div className="stat-card" whileHover={{ y: -2 }}>
            <div className={`stat-icon stat-icon-${accent}`}><Icon size={18} /></div>
            <div className="stat-label">{label}</div>
            <div className="stat-value">{value}</div>
        </motion.div>
    );
}
